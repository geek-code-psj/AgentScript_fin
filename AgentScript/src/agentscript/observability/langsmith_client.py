"""LangSmith REST API client for AgentScript trace logging.

Enables deep semantic debugging via LangSmith UI by sending execution traces
to the LangSmith platform. Runs are visible at https://smith.langchain.com/hub

Configuration:
    export LANGSMITH_API_KEY=ls_...
    export LANGSMITH_ENDPOINT=https://api.smith.langchain.com  (optional, default shown)
    
Usage:
    client = LangSmithClient()
    await client.log_run(
        name="legal_brief",
        inputs={"query": "BNS theft appeal"},
        outputs={"brief": claim},
        trace_events=[...],  # From SQLiteTraceRecorder
    )
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


def _serialize_value(value: Any) -> Any:
    """Serialize dataclass/complex objects to JSON-compatible format."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    elif isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    elif isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    return value


@dataclass(slots=True)
class LangSmithClient:
    """Client for logging AgentScript runs to LangSmith.
    
    Automatically reads LANGSMITH_API_KEY and LANGSMITH_ENDPOINT from environment.
    Gracefully degrades if httpx is unavailable or API key is missing.
    """
    
    api_key: str | None = None
    endpoint: str = "https://api.smith.langchain.com"
    project_name: str = "agentscript"
    enabled: bool = False
    
    def __post_init__(self) -> None:
        """Initialize from environment variables."""
        if self.api_key is None:
            self.api_key = os.getenv("LANGSMITH_API_KEY")
        
        if os.getenv("LANGSMITH_ENDPOINT") is not None:
            self.endpoint = os.getenv("LANGSMITH_ENDPOINT", self.endpoint)
        
        self.enabled = self.api_key is not None and httpx is not None
    
    async def log_run(
        self,
        *,
        name: str,
        run_type: str = "agent",
        inputs: dict[str, Any],
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        trace_events: list[dict[str, Any]] | None = None,
        parent_run_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Log a single run to LangSmith.
        
        Args:
            name: Run name (e.g., "legal_brief")
            run_type: Run type (agent, tool, chain, retriever, etc.)
            inputs: Input parameters
            outputs: Output values
            error: Error message if failed
            trace_events: List of trace events from execution
            parent_run_id: Parent run ID for nested runs
            tags: List of tags for categorization
            metadata: Arbitrary metadata dictionary
            
        Returns:
            Run ID if successful, None otherwise
        """
        if not self.enabled or self.api_key is None:
            return None
        
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        
        # Build run payload per LangSmith API
        run_payload: dict[str, Any] = {
            "id": run_id,
            "name": name,
            "run_type": run_type,
            "inputs": _serialize_value(inputs),
            "start_time": now,
            "status": "error" if error else "success",
            "end_time": now,
        }
        
        if outputs is not None:
            run_payload["outputs"] = _serialize_value(outputs)
        
        if error is not None:
            run_payload["error"] = error
        
        if parent_run_id is not None:
            run_payload["parent_run_id"] = parent_run_id
        
        if tags is not None:
            run_payload["tags"] = tags
        
        if metadata is not None:
            run_payload["metadata"] = metadata
        
        # Add project name
        run_payload["project_name"] = self.project_name
        
        # Attempt to post run to LangSmith
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                }
                
                # POST run
                response = await client.post(
                    f"{self.endpoint}/runs",
                    json=run_payload,
                    headers=headers,
                    timeout=10.0,
                )
                
                if response.status_code not in (200, 201):
                    # Log warning but don't crash
                    print(f"LangSmith API error: {response.status_code} {response.text}")
                    return None
                
                # Log trace events as child spans if provided
                if trace_events is not None:
                    await self._log_trace_events(run_id, trace_events, headers, client)
                
                return run_id
        
        except Exception as e:
            # Graceful degradation: log but don't crash
            print(f"LangSmith logging failed: {e}")
            return None
    
    async def _log_trace_events(
        self,
        parent_run_id: str,
        trace_events: list[dict[str, Any]],
        headers: dict[str, str],
        client: Any,
    ) -> None:
        """Log trace events as child spans under the parent run.
        
        Args:
            parent_run_id: Parent run ID
            trace_events: List of trace events
            headers: HTTP headers with API key
            client: httpx.AsyncClient instance
        """
        for event in trace_events:
            if event.get("event_type") not in ("tool_call", "tool_result", "memory_search"):
                continue
            
            child_run_id = str(uuid4())
            now = datetime.now(UTC).isoformat()
            
            child_payload: dict[str, Any] = {
                "id": child_run_id,
                "name": event.get("tool", event.get("event_type", "event")),
                "run_type": "tool",
                "parent_run_id": parent_run_id,
                "inputs": event.get("args") or event.get("query") or {},
                "outputs": event.get("response") or {},
                "start_time": now,
                "end_time": now,
                "status": "success" if event.get("ok", True) else "error",
                "project_name": self.project_name,
                "tags": ["trace_event"],
            }
            
            try:
                await client.post(
                    f"{self.endpoint}/runs",
                    json=child_payload,
                    headers=headers,
                    timeout=5.0,
                )
            except Exception:
                # Silently ignore child event logging failures
                pass
    
    async def update_run(
        self,
        run_id: str,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Update an existing run (e.g., mark as complete or failed).
        
        Args:
            run_id: Run ID to update
            outputs: Output values
            error: Error message if failed
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or self.api_key is None:
            return False
        
        payload: dict[str, Any] = {
            "status": "error" if error else "success",
            "end_time": datetime.now(UTC).isoformat(),
        }
        
        if outputs is not None:
            payload["outputs"] = _serialize_value(outputs)
        
        if error is not None:
            payload["error"] = error
        
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                }
                
                response = await client.patch(
                    f"{self.endpoint}/runs/{run_id}",
                    json=payload,
                    headers=headers,
                    timeout=10.0,
                )
                
                return response.status_code in (200, 204)
        
        except Exception as e:
            print(f"LangSmith update failed: {e}")
            return False


async def demo_langsmith_logging() -> None:
    """Demonstrate LangSmith logging."""
    import asyncio
    
    client = LangSmithClient()
    
    if not client.enabled:
        print("LangSmith not configured. Set LANGSMITH_API_KEY to enable.")
        return
    
    # Log a sample run
    run_id = await client.log_run(
        name="demo_agent",
        run_type="agent",
        inputs={"query": "What is the capital of France?"},
        outputs={"answer": "Paris"},
        tags=["demo", "test"],
        metadata={"source": "agentscript_demo"},
    )
    
    if run_id is not None:
        print(f"✓ Logged to LangSmith: {run_id}")
        print(f"  View at: https://smith.langchain.com/hub/r/{run_id}")
    else:
        print("✗ Failed to log to LangSmith")


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo_langsmith_logging())
