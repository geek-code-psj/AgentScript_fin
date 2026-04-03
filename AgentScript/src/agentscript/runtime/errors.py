"""Runtime error types for AgentScript."""

from __future__ import annotations


class AgentScriptRuntimeError(Exception):
    """Base class for runtime failures."""


class UnknownWorkflowError(AgentScriptRuntimeError):
    """Raised when a workflow is requested that does not exist."""


class UnknownAgentError(AgentScriptRuntimeError):
    """Raised when a runtime agent policy is requested that does not exist."""


class ToolNotRegisteredError(AgentScriptRuntimeError):
    """Raised when a tool is invoked without a registry implementation."""


class ToolInvocationError(AgentScriptRuntimeError):
    """Raised when tool execution ultimately fails."""
