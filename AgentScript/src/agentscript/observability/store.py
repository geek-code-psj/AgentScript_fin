"""Trace-backed observability views for AgentScript."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from agentscript.runtime.tracing import SQLiteTraceReplayer, format_replay


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    workflow_name: str
    agent_name: str | None
    status: str
    started_at: str
    finished_at: str | None
    duration_ms: float | None
    jsonl_path: str | None
    error_text: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolTimelineEntry:
    step_id: str
    workflow_name: str
    tool_name: str
    attempt: int
    started_at: float
    finished_at: float
    latency_ms: float
    ok: bool
    status_code: int
    source: str
    replayed: bool
    retries: int
    args: dict[str, object]
    payload: object
    error: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MemoryPoint:
    seq: int
    key: str
    source: str
    value: object
    semantic_indexed: bool
    snapshot: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunDetail:
    summary: RunSummary
    event_count: int
    tool_call_count: int
    tool_result_count: int
    final_output: object
    arguments: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["summary"] = self.summary.to_dict()
        return data


class TraceStore:
    """Read-friendly observability views over a trace SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row

    def list_runs(self, *, limit: int = 20) -> list[RunSummary]:
        rows = self.connection.execute(
            """
            SELECT run_id, workflow_name, agent_name, status, started_at, finished_at,
                   jsonl_path, error_text
            FROM runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._run_summary(row) for row in rows]

    def get_run(self, run_id: str) -> RunDetail:
        row = self.connection.execute(
            """
            SELECT run_id, workflow_name, agent_name, status, started_at, finished_at,
                   jsonl_path, error_text, final_output_json, arguments_json
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Run '{run_id}' does not exist.")

        event_count = self._scalar(
            "SELECT COUNT(*) FROM events WHERE run_id = ?",
            (run_id,),
        )
        tool_call_count = self._scalar(
            "SELECT COUNT(*) FROM tool_calls WHERE run_id = ?",
            (run_id,),
        )
        tool_result_count = self._scalar(
            "SELECT COUNT(*) FROM tool_results WHERE run_id = ?",
            (run_id,),
        )

        return RunDetail(
            summary=self._run_summary(row),
            event_count=int(event_count),
            tool_call_count=int(tool_call_count),
            tool_result_count=int(tool_result_count),
            final_output=_load_json(row["final_output_json"]),
            arguments=_load_json(row["arguments_json"]) or {},
        )

    def timeline(self, run_id: str) -> list[ToolTimelineEntry]:
        call_rows = self.connection.execute(
            """
            SELECT step_id, attempt, workflow_name, tool_name, args_json, timestamp
            FROM tool_calls
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
        result_rows = self.connection.execute(
            """
            SELECT step_id, workflow_name, tool_name, ok, status_code, payload_json,
                   error_text, latency_ms, retries, timestamp, source, replayed
            FROM tool_results
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()

        results_by_key: dict[tuple[str, int], sqlite3.Row] = {}
        for row in result_rows:
            attempt_key = 0 if row["source"] == "circuit-open" else int(row["retries"]) + 1
            results_by_key[(str(row["step_id"]), attempt_key)] = row

        entries: list[ToolTimelineEntry] = []
        for call in call_rows:
            step_id = str(call["step_id"])
            attempt = int(call["attempt"])
            result = results_by_key.get((step_id, attempt))
            if result is None:
                continue
            started_at = float(call["timestamp"])
            finished_at = float(result["timestamp"])
            entries.append(
                ToolTimelineEntry(
                    step_id=step_id,
                    workflow_name=str(call["workflow_name"]),
                    tool_name=str(call["tool_name"]),
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=float(result["latency_ms"]),
                    ok=bool(result["ok"]),
                    status_code=int(result["status_code"]),
                    source=str(result["source"]),
                    replayed=bool(result["replayed"]),
                    retries=int(result["retries"]),
                    args=_load_json(call["args_json"]) or {},
                    payload=_load_json(result["payload_json"]),
                    error=result["error_text"],
                )
            )
        return entries

    def memory_evolution(self, run_id: str) -> list[MemoryPoint]:
        rows = self.connection.execute(
            """
            SELECT seq, payload_json
            FROM events
            WHERE run_id = ? AND event_type = 'memory_write'
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
        snapshot: dict[str, object] = {}
        points: list[MemoryPoint] = []
        for row in rows:
            payload = _load_json(row["payload_json"]) or {}
            key = str(payload.get("key", ""))
            snapshot[key] = payload.get("value")
            points.append(
                MemoryPoint(
                    seq=int(row["seq"]),
                    key=key,
                    source=str(payload.get("source", "unknown")),
                    value=payload.get("value"),
                    semantic_indexed=bool(payload.get("semantic_indexed", False)),
                    snapshot=dict(snapshot),
                )
            )
        return points

    def replay_view(self, run_id: str) -> dict[str, object]:
        replayer = SQLiteTraceReplayer(self.path)
        try:
            replay = replayer.replay(run_id)
            return {
                "run_id": replay.run_id,
                "workflow_name": replay.workflow_name,
                "status": replay.status,
                "final_output": replay.final_output,
                "events": [asdict(event) for event in replay.events],
                "formatted": format_replay(replay),
            }
        finally:
            replayer.close()

    def dashboard_payload(self, run_id: str) -> dict[str, object]:
        return {
            "run": self.get_run(run_id).to_dict(),
            "timeline": [entry.to_dict() for entry in self.timeline(run_id)],
            "memory": [point.to_dict() for point in self.memory_evolution(run_id)],
            "replay": self.replay_view(run_id),
        }

    def close(self) -> None:
        self.connection.close()

    def _scalar(self, query: str, parameters: tuple[object, ...]) -> object:
        row = self.connection.execute(query, parameters).fetchone()
        if row is None:
            return 0
        return row[0]

    def _run_summary(self, row: sqlite3.Row) -> RunSummary:
        started_at = str(row["started_at"])
        finished_at = None if row["finished_at"] is None else str(row["finished_at"])
        return RunSummary(
            run_id=str(row["run_id"]),
            workflow_name=str(row["workflow_name"]),
            agent_name=row["agent_name"],
            status=str(row["status"]),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=_duration_ms(started_at, finished_at),
            jsonl_path=row["jsonl_path"],
            error_text=row["error_text"],
        )


def _load_json(raw: object) -> object:
    if raw in {None, ""}:
        return None
    return json.loads(str(raw))


def _duration_ms(started_at: str, finished_at: str | None) -> float | None:
    if finished_at is None:
        return None
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    return max(0.0, (finished - started).total_seconds() * 1000.0)
