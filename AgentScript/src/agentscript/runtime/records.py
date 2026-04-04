"""Shared runtime records for tool execution and replay.

These dataclasses represent the core execution events:
- ToolCall: A tool invocation request
- ToolResult: A tool invocation result (success or failure)
- TraceEvent: A generic event in the execution trace
- ReplayResult: The output of a deterministic replay
- ReplaySource: Snapshot of execution data for replay
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Configuration snapshot of the language model at execution time.
    
    Enables full forensic auditability: same model ID + config = same behavior.
    """
    
    model_id: str  # e.g., "gpt-4-turbo-2024-04-09"
    fine_tune_id: str | None = None  # e.g., "ft-ABC123"
    temperature: float | None = None  # 0.0 to 2.0
    top_p: float | None = None  # 0.0 to 1.0
    max_tokens: int | None = None
    system_prompt_hash: str | None = None  # SHA256 of system prompt
    other_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Represents a request to invoke a tool."""
    
    run_id: str | None
    step_id: str
    workflow_name: str
    tool_name: str
    args: dict[str, object]
    attempt: int  # Retry attempt number (0 = first attempt)
    timestamp: float
    model_config: ModelConfig | None = None  # Model config at invocation time
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Represents the result of a tool invocation."""
    
    run_id: str | None
    step_id: str
    workflow_name: str
    tool_name: str
    ok: bool  # True if successful, False if failed
    status_code: int  # HTTP status or custom code
    payload: object  # Return value or error response
    error: str | None  # Error message if ok=False
    latency_ms: float  # Execution time in milliseconds
    retries: int  # Number of retries before this result
    timestamp: float
    source: str = "live"  # "live" or "replay"
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """An immutable event in the execution trace.
    
    Represents any event during workflow execution: step executed, tool called,
    LLM response received, error occurred, etc. Enables full reconstruction of
    execution flow and forensic debugging.
    
    Attributes:
        seq: Sequence number in the trace (monotonically increasing)
        run_id: Unique execution identifier
        event_type: e.g., "step_start", "tool_call", "tool_result", "error", "checkpoint"
        workflow_name: Name of containing workflow (None for global events)
        instruction_index: Line number in AgentScript source (None for system events)
        payload: Event-specific data (redacted of PII before storage)
        created_at: ISO 8601 timestamp with timezone
    """
    
    seq: int
    run_id: str
    event_type: str
    workflow_name: str | None
    instruction_index: int | None
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Outcome of a deterministic replay execution.
    
    When replaying with pre-recorded tool results, the execution must be deterministic.
    This record captures whether the replay succeeded and what events were generated.
    """
    
    run_id: str  # Original run ID being replayed
    workflow_name: str
    status: str  # "success", "diverged", "error"
    final_output: object  # Return value from the workflow
    events: tuple[TraceEvent, ...]  # Events generated during replay


@dataclass(frozen=True, slots=True)
class ReplaySource:
    """Snapshot of execution state for deterministic replay.
    
    Enables re-executing a workflow with identical tool results injected,
    supporting debugging, testing, and cost-free re-evaluation.
    
    Attributes:
        run_id: Original run identifier
        workflow_name: Workflow being replayed
        arguments: Original input arguments
        tool_results: Pre-recorded results substituted during replay (keyed by step_id)
        timestamps: Execution times at each step (for latency reproduction)
    """
    
    run_id: str
    workflow_name: str
    arguments: dict[str, object]
    tool_results: dict[str, ToolResult]  # step_id -> ToolResult
    timestamps: tuple[float, ...] = field(default_factory=tuple)
