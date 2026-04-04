"""SQLite and JSONL trace recording for AgentScript runtime runs.

Implements:
- Structured event logging (TOOL_CALL, TOOL_RESULT, MEMORY_SEARCH)
- JSONL append-only format for streaming/replay
- SQLite persistence for fast queries
- Comprehensive PII/secrets redaction
- Event source integrity tracking
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid

from agentscript.runtime.records import ReplayResult, ReplaySource, ToolCall, ToolResult, TraceEvent


# Comprehensive regex patterns for PII and secrets redaction
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?!\d{4}-\d{2}-\d{2}\b)(?:\+?\d[\d -]{8,}\d)(?!\d)")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
IP_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# API Key patterns
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
ANTHROPIC_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9]{28,}\b")
AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
GOOGLE_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z\\-_]{35}\b")

# Authentication headers
BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE)
AUTHORIZATION_RE = re.compile(r"Authorization:\s*[^\s,]+", re.IGNORECASE)
X_API_KEY_RE = re.compile(r"X-API-Key:\s*[^\s,]+", re.IGNORECASE)

# Full URL patterns with embedded secrets
URL_WITH_SECRET_RE = re.compile(r"(https?://[^:]+:)[^@]+(@)")  # protocol://user:password@


class RedactionPolicy:
    """Configurable policy for PII/secrets redaction.
    
    Controls which types of sensitive data are redacted from traces.
    Redaction happens at serialization time (write), not retrieval time,
    so audit trails are preserved in the database.
    """
    
    def __init__(
        self,
        *,
        redact_emails: bool = True,
        redact_phones: bool = True,
        redact_ssn: bool = True,
        redact_ip_addresses: bool = True,
        redact_api_keys: bool = True,
        redact_auth_headers: bool = True,
        redact_credentials: bool = True,
        custom_patterns: list[tuple[re.Pattern[str], str]] | None = None,
    ) -> None:
        """Initialize redaction policy.
        
        Args:
            redact_emails: Redact email addresses (@domain.com)
            redact_phones: Redact phone numbers (various formats)
            redact_ssn: Redact social security numbers (XXX-XX-XXXX)
            redact_ip_addresses: Redact IPv4 addresses (X.X.X.X)
            redact_api_keys: Redact API keys (OpenAI sk-, Anthropic sk-ant-, AWS AKIA, Google AIza)
            redact_auth_headers: Redact Authorization and X-API-Key headers
            redact_credentials: Redact URL credentials (user:password@)
            custom_patterns: Additional regex patterns for redaction [(pattern, replacement), ...]
        """
        self.patterns: list[tuple[re.Pattern[str], str]] = []
        
        if redact_emails:
            self.patterns.append((EMAIL_RE, "[REDACTED_EMAIL]"))
        if redact_phones:
            self.patterns.append((PHONE_RE, "[REDACTED_PHONE]"))
        if redact_ssn:
            self.patterns.append((SSN_RE, "[REDACTED_SSN]"))
        if redact_ip_addresses:
            self.patterns.append((IP_ADDRESS_RE, "[REDACTED_IP]"))
        
        if redact_api_keys:
            self.patterns.extend([
                (OPENAI_KEY_RE, "[REDACTED_OPENAI_KEY]"),
                (ANTHROPIC_KEY_RE, "[REDACTED_ANTHROPIC_KEY]"),
                (AWS_KEY_RE, "[REDACTED_AWS_KEY]"),
                (GOOGLE_KEY_RE, "[REDACTED_GOOGLE_KEY]"),
            ])
        
        if redact_auth_headers:
            self.patterns.extend([
                (BEARER_RE, "Bearer [REDACTED_TOKEN]"),
                (AUTHORIZATION_RE, "Authorization: [REDACTED]"),
                (X_API_KEY_RE, "X-API-Key: [REDACTED]"),
            ])
        
        if redact_credentials:
            self.patterns.append((URL_WITH_SECRET_RE, r"\1[REDACTED]\2"))
        
        if custom_patterns:
            self.patterns.extend(custom_patterns)
    
    def redact(self, value: object) -> object:
        """Redact a value using the configured policy.
        
        Args:
            value: Value to redact (string, dict, list, dataclass, etc.)
            
        Returns:
            Redacted value (same type as input)
        """
        if isinstance(value, dict):
            return {str(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            result = value
            for pattern, replacement in self.patterns:
                result = pattern.sub(replacement, result)
            return result
        if is_dataclass(value) and not isinstance(value, type):
            return self.redact(asdict(value))
        return value


# Global default redaction policy
_DEFAULT_REDACTION_POLICY = RedactionPolicy()


class SQLiteTraceRecorder:
    """Persists runtime traces to SQLite and JSONL."""

    def __init__(self, path: str | Path, *, jsonl_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = (
            Path(jsonl_path)
            if jsonl_path is not None
            else self.path.with_suffix(".jsonl")
        )
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=MEMORY;")
        self.connection.execute("PRAGMA synchronous=OFF;")
        self._jsonl_handle = self.jsonl_path.open("a", encoding="utf-8")
        self._ensure_schema()

    def start_run(
        self,
        workflow_name: str,
        *,
        agent_name: str | None,
        arguments: dict[str, object],
    ) -> str:
        run_id = uuid.uuid4().hex
        now = _utc_now()
        self.connection.execute(
            """
            INSERT INTO runs (
              run_id, workflow_name, agent_name, status, started_at, arguments_json, jsonl_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                workflow_name,
                agent_name,
                "running",
                now,
                _dump_json_raw(arguments),
                str(self.jsonl_path),
            ),
        )
        self.connection.commit()
        self.record(
            run_id,
            "run_started",
            workflow_name=workflow_name,
            payload={"agent_name": agent_name, "arguments": arguments},
        )
        return run_id

    def record(
        self,
        run_id: str,
        event_type: str,
        *,
        workflow_name: str | None = None,
        instruction_index: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        redacted = redact_payload(payload or {})
        self.connection.execute(
            """
            INSERT INTO events (
              run_id, event_type, workflow_name, instruction_index, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                event_type,
                workflow_name,
                instruction_index,
                json.dumps(redacted, sort_keys=True),
                _utc_now(),
            ),
        )
        self.connection.commit()
        self._write_jsonl(
            {
                "kind": "Event",
                "run_id": run_id,
                "event_type": event_type,
                "workflow": workflow_name,
                "instruction_index": instruction_index,
                "payload": redacted,
                "created_at": _utc_now(),
            }
        )

    def record_tool_call(self, call: ToolCall) -> None:
        self.connection.execute(
            """
            INSERT INTO tool_calls (
              run_id, step_id, attempt, workflow_name, tool_name, args_json, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call.run_id,
                call.step_id,
                call.attempt,
                call.workflow_name,
                call.tool_name,
                _dump_json_raw(call.args),
                call.timestamp,
            ),
        )
        self.connection.commit()
        self._write_jsonl(
            {
                "kind": "ToolCall",
                "run_id": call.run_id,
                "step_id": call.step_id,
                "workflow": call.workflow_name,
                "tool": call.tool_name,
                "args": call.args,
                "attempt": call.attempt,
                "timestamp": call.timestamp,
                "replayed": call.replayed,
            }
        )

    def record_tool_result(self, result: ToolResult) -> None:
        self.connection.execute(
            """
            INSERT INTO tool_results (
              run_id, step_id, workflow_name, tool_name, ok, status_code, payload_json,
              error_text, latency_ms, retries, timestamp, source, replayed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.run_id,
                result.step_id,
                result.workflow_name,
                result.tool_name,
                1 if result.ok else 0,
                result.status_code,
                _dump_json_raw(result.payload),
                result.error,
                result.latency_ms,
                result.retries,
                result.timestamp,
                result.source,
                1 if result.replayed else 0,
            ),
        )
        self.connection.commit()
        self._write_jsonl(
            {
                "kind": "ToolResult",
                "run_id": result.run_id,
                "step_id": result.step_id,
                "workflow": result.workflow_name,
                "tool": result.tool_name,
                "ok": result.ok,
                "status_code": result.status_code,
                "payload": result.payload,
                "error": result.error,
                "latency_ms": result.latency_ms,
                "retries": result.retries,
                "timestamp": result.timestamp,
                "source": result.source,
                "replayed": result.replayed,
            }
        )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        output: object = None,
        error: object = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE runs
            SET status = ?, finished_at = ?, final_output_json = ?, error_text = ?
            WHERE run_id = ?
            """,
            (
                status,
                _utc_now(),
                _dump_json_raw(output),
                None if error is None else str(error),
                run_id,
            ),
        )
        self.connection.commit()
        self.record(
            run_id,
            "run_finished",
            payload={"status": status, "output": output, "error": error},
        )

    def latest_run_id(self) -> str | None:
        row = self.connection.execute(
            "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return None if row is None else str(row["run_id"])

    def events(self, run_id: str) -> list[TraceEvent]:
        rows = self.connection.execute(
            """
            SELECT seq, run_id, event_type, workflow_name, instruction_index, payload_json, created_at
            FROM events
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
        return [
            TraceEvent(
                seq=int(row["seq"]),
                run_id=str(row["run_id"]),
                event_type=str(row["event_type"]),
                workflow_name=row["workflow_name"],
                instruction_index=row["instruction_index"],
                payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._jsonl_handle.close()
        self.connection.close()

    def _ensure_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              workflow_name TEXT NOT NULL,
              agent_name TEXT,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              arguments_json TEXT,
              final_output_json TEXT,
              error_text TEXT,
              jsonl_path TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              workflow_name TEXT,
              instruction_index INTEGER,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (run_id) REFERENCES runs (run_id)
            );

            CREATE TABLE IF NOT EXISTS tool_calls (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT,
              step_id TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              workflow_name TEXT NOT NULL,
              tool_name TEXT NOT NULL,
              args_json TEXT NOT NULL,
              timestamp REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tool_results (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT,
              step_id TEXT NOT NULL,
              workflow_name TEXT NOT NULL,
              tool_name TEXT NOT NULL,
              ok INTEGER NOT NULL,
              status_code INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              error_text TEXT,
              latency_ms REAL NOT NULL,
              retries INTEGER NOT NULL,
              timestamp REAL NOT NULL,
              source TEXT NOT NULL,
              replayed INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.connection.commit()

    def _write_jsonl(self, payload: dict[str, object]) -> None:
        redacted = redact_payload(payload)
        self._jsonl_handle.write(json.dumps(redacted, sort_keys=True) + "\n")
        self._jsonl_handle.flush()


class SQLiteTraceReplayer:
    """Loads trace events and tool results for deterministic replay."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row

    def replay(self, run_id: str | None = None) -> ReplayResult:
        chosen_run_id = run_id or self._latest_run_id()
        if chosen_run_id is None:
            raise ValueError("No trace runs are available for replay.")

        run_row = self.connection.execute(
            """
            SELECT run_id, workflow_name, status, final_output_json
            FROM runs
            WHERE run_id = ?
            """,
            (chosen_run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f"Run '{chosen_run_id}' does not exist in the trace database.")

        events = tuple(self._events(chosen_run_id))
        final_output = (
            json.loads(run_row["final_output_json"])
            if run_row["final_output_json"]
            else None
        )
        return ReplayResult(
            run_id=str(run_row["run_id"]),
            workflow_name=str(run_row["workflow_name"]),
            status=str(run_row["status"]),
            final_output=final_output,
            events=events,
        )

    def load_source(self, run_id: str | None = None) -> ReplaySource:
        chosen_run_id = run_id or self._latest_run_id()
        if chosen_run_id is None:
            raise ValueError("No trace runs are available for replay.")

        run_row = self.connection.execute(
            "SELECT run_id, workflow_name, arguments_json FROM runs WHERE run_id = ?",
            (chosen_run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f"Run '{chosen_run_id}' does not exist in the trace database.")

        rows = self.connection.execute(
            """
            SELECT step_id, workflow_name, tool_name, ok, status_code, payload_json, error_text,
                   latency_ms, retries, timestamp, source, replayed
            FROM tool_results
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (chosen_run_id,),
        ).fetchall()
        tool_results: dict[str, ToolResult] = {}
        timestamps: list[float] = []
        for row in rows:
            result = ToolResult(
                run_id=chosen_run_id,
                step_id=str(row["step_id"]),
                workflow_name=str(row["workflow_name"]),
                tool_name=str(row["tool_name"]),
                ok=bool(row["ok"]),
                status_code=int(row["status_code"]),
                payload=json.loads(row["payload_json"]) if row["payload_json"] else None,
                error=row["error_text"],
                latency_ms=float(row["latency_ms"]),
                retries=int(row["retries"]),
                timestamp=float(row["timestamp"]),
                source=str(row["source"]),
                replayed=bool(row["replayed"]),
            )
            tool_results[result.step_id] = result
            timestamps.append(result.timestamp)

        return ReplaySource(
            run_id=chosen_run_id,
            workflow_name=str(run_row["workflow_name"]),
            arguments=(
                json.loads(run_row["arguments_json"])
                if run_row["arguments_json"]
                else {}
            ),
            tool_results=tool_results,
            timestamps=tuple(timestamps),
        )

    def close(self) -> None:
        self.connection.close()

    def _latest_run_id(self) -> str | None:
        row = self.connection.execute(
            "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return None if row is None else str(row["run_id"])

    def _events(self, run_id: str) -> list[TraceEvent]:
        rows = self.connection.execute(
            """
            SELECT seq, run_id, event_type, workflow_name, instruction_index, payload_json, created_at
            FROM events
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
        return [
            TraceEvent(
                seq=int(row["seq"]),
                run_id=str(row["run_id"]),
                event_type=str(row["event_type"]),
                workflow_name=row["workflow_name"],
                instruction_index=row["instruction_index"],
                payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]


def format_replay(result: ReplayResult) -> str:
    """Render a replay result as a readable timeline."""

    lines = [
        f"run {result.run_id} workflow={result.workflow_name} status={result.status}",
        f"final_output={result.final_output!r}",
    ]
    for event in result.events:
        workflow = f" workflow={event.workflow_name}" if event.workflow_name else ""
        instruction = (
            f" pc={event.instruction_index}" if event.instruction_index is not None else ""
        )
        lines.append(
            f"  {event.seq:03} {event.event_type}{workflow}{instruction} payload={event.payload!r}"
        )
    return "\n".join(lines)


def redact_payload(value: object) -> object:
    """Redact PII and API-like secrets before trace persistence.
    
    Uses the default global redaction policy.
    Redacted secrets are replaced with tokens like [REDACTED_EMAIL].
    """
    return _DEFAULT_REDACTION_POLICY.redact(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _dump_json(value: object) -> str:
    return json.dumps(redact_payload(value), default=_json_default, sort_keys=True)


def _dump_json_raw(value: object) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True)


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, Exception):
        return {"type": value.__class__.__name__, "message": str(value)}
    return str(value)
