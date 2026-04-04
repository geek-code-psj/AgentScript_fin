"""Runtime error types for AgentScript.

Error types in AgentScript are designed for comprehensive observability:
- Each error captures context (run_id, workflow, step)
- Errors are automatically recorded as structured spans in OTel
- Error categorization enables SLO tracking and on-call alerting
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ErrorContext:
    """Structured context for observability integration.
    
    Captured automatically and added to OTel spans for full causality tracking.
    """
    
    run_id: str | None = None
    workflow_name: str | None = None
    step_id: str | None = None
    tool_name: str | None = None
    attempt: int = 0
    latency_ms: float | None = None
    user_id: str | None = None  # For multi-tenant observability
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentScriptRuntimeError(Exception):
    """Base class for runtime failures.
    
    Automatically creates structured error spans in OpenTelemetry with:
    - Error category (recoverable, permanent, transient, etc.)
    - Root cause classification
    - Full context chain (run_id -> workflow -> step -> tool)
    """
    
    error_category: str = "permanent"  # permanent, transient, recoverable, system
    
    def __init__(self, message: str, context: ErrorContext | None = None) -> None:
        super().__init__(message)
        self.context = context or ErrorContext()
        self.message = message


class UnknownWorkflowError(AgentScriptRuntimeError):
    """Raised when a workflow is requested that does not exist.
    
    Category: permanent (the workflow definition is missing from registry)
    """
    
    error_category = "permanent"


class UnknownAgentError(AgentScriptRuntimeError):
    """Raised when a runtime agent policy is not available.
    
    Category: permanent (incorrect deployment or configuration)
    """
    
    error_category = "permanent"


class ToolNotRegisteredError(AgentScriptRuntimeError):
    """Raised when a tool is invoked without a registry implementation.
    
    Category: permanent (tool dependency not available)
    """
    
    error_category = "permanent"


class ToolInvocationError(AgentScriptRuntimeError):
    """Raised when tool execution ultimately fails after retries.
    
    Category: transient or permanent (depends on underlying cause)
    Can be enriched with:
    - HTTP status code (for REST tools)
    - Retry count and backoff strategy
    - Root cause from tool provider
    """
    
    error_category = "transient"
    
    def __init__(
        self,
        message: str,
        context: ErrorContext | None = None,
        status_code: int | None = None,
        retries: int = 0,
    ) -> None:
        super().__init__(message, context)
        self.status_code = status_code
        self.retries = retries
