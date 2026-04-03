"""Shared runtime records for tool execution and replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolCall:
    run_id: str | None
    step_id: str
    workflow_name: str
    tool_name: str
    args: dict[str, object]
    attempt: int
    timestamp: float
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ToolResult:
    run_id: str | None
    step_id: str
    workflow_name: str
    tool_name: str
    ok: bool
    status_code: int
    payload: object
    error: str | None
    latency_ms: float
    retries: int
    timestamp: float
    source: str = "live"
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class TraceEvent:
    seq: int
    run_id: str
    event_type: str
    workflow_name: str | None
    instruction_index: int | None
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    run_id: str
    workflow_name: str
    status: str
    final_output: object
    events: tuple[TraceEvent, ...]


@dataclass(frozen=True, slots=True)
class ReplaySource:
    run_id: str
    workflow_name: str
    arguments: dict[str, object]
    tool_results: dict[str, ToolResult]
    timestamps: tuple[float, ...] = field(default_factory=tuple)
