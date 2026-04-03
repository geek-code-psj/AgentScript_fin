"""Observability helpers for AgentScript."""

from agentscript.observability.otel import RuntimeTelemetry
from agentscript.observability.store import TraceStore

__all__ = ["RuntimeTelemetry", "TraceStore", "create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from agentscript.observability.server import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
