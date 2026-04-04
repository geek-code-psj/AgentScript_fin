"""OpenTelemetry semantic conventions for generative AI systems.

Implements the official OTel GenAI semantic standards:
https://opentelemetry.io/docs/specs/semconv/gen-ai/

Required environment variable for latest conventions:
    export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

try:  # pragma: no cover - import availability varies by environment
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode
except Exception:  # pragma: no cover - fallback path
    trace = None
    SpanKind = None
    Status = None
    StatusCode = None


class GenAIOperation(str, Enum):
    """Valid values for gen_ai.operation.name semantic attribute."""
    
    TOOL_CALL = "tool_call"
    AGENT_RUN = "agent_run"
    INFERENCE = "inference"
    RETRIEVALS = "retrievals"
    MEMORY_SEARCH = "memory_search"
    CIRCUIT_BREAKER_TRANSITION = "circuit_breaker_transition"
    FALLBACK_EXECUTION = "fallback_execution"


class _NoopSpan:
    """No-op span implementation for when OTel is unavailable."""
    
    def set_attribute(self, key: str, value: object) -> None:
        return None

    def set_attributes(self, attributes: dict[str, object]) -> None:
        return None

    def record_exception(self, error: BaseException) -> None:
        return None
    
    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        return None


@dataclass(slots=True)
class RuntimeTelemetry:
    """Wrapper for OpenTelemetry integration with GenAI semantic conventions.
    
    Degradation: If OTel is unavailable, all methods become no-ops.
    
    Example:
        telemetry = RuntimeTelemetry(agent_name="legal_researcher")
        with telemetry.span(
            "tool_call",
            operation=GenAIOperation.TOOL_CALL,
            attributes={"agentscript.tool.name": "search_law"}
        ) as span:
            try:
                result = await tool.invoke()
                span.set_attribute("gen_ai.usage.input_tokens", 100)
            except Exception as e:
                telemetry.mark_error(span, e)
    """

    agent_name: str | None = None
    tracer_name: str = "agentscript.runtime"
    tracer: object | None = field(init=False, default=None)
    _use_latest_semconv: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if trace is not None:
            self.tracer = trace.get_tracer(self.tracer_name)
            # Check if using latest experimental semantic conventions
            self._use_latest_semconv = os.getenv("OTEL_SEMCONV_STABILITY_OPT_IN") == "gen_ai_latest_experimental"

    @contextmanager
    def span(
        self,
        name: str,
        *,
        operation: GenAIOperation | str | None = None,
        kind: str = "internal",
        attributes: dict[str, object] | None = None,
        conversation_id: str | None = None,
        provider_name: str | None = None,
    ) -> Iterator[_NoopSpan | object]:
        """Create a span with GenAI semantic convention attributes.
        
        Args:
            name: Human-readable span name (e.g., "tool_call", "agent_run")
            operation: GenAI operation type (tool_call, agent_run, inference, etc.)
            kind: "internal" or "client"
            attributes: Additional span attributes
            conversation_id: Unique session/conversation identifier
            provider_name: LLM provider (OpenAI, Anthropic, AWS Bedrock, etc.)
        """
        span_kind = _map_kind(kind)
        if self.tracer is None or span_kind is None:
            noop = _NoopSpan()
            yield noop
            return

        # Build semantic convention attributes
        otel_attrs: dict[str, object] = attributes or {}
        
        # Required attributes
        if operation is not None:
            op_value = operation.value if isinstance(operation, GenAIOperation) else operation
            otel_attrs["gen_ai.operation.name"] = op_value
        
        if self.agent_name is not None:
            otel_attrs["gen_ai.agent.name"] = self.agent_name
        
        if conversation_id is not None:
            otel_attrs["gen_ai.conversation.id"] = conversation_id
        
        if provider_name is not None:
            otel_attrs["gen_ai.provider.name"] = provider_name

        with self.tracer.start_as_current_span(  # type: ignore[union-attr]
            name,
            kind=span_kind,
            attributes=otel_attrs,
        ) as span:
            yield span

    def mark_error(
        self,
        span: _NoopSpan | object,
        error: BaseException,
        recovery_action: str | None = None,
    ) -> None:
        """Record an error in the span with structured attributes.
        
        Automatically extracts ErrorContext if available and enriches span.
        
        Args:
            span: The span to mark
            error: The exception that occurred
            recovery_action: Description of recovery action (retry, fallback, escalate)
        """
        if hasattr(span, "record_exception"):
            span.record_exception(error)  # type: ignore[call-arg]
        
        if Status is not None and StatusCode is not None and hasattr(span, "set_status"):
            span.set_status(Status(StatusCode.ERROR, str(error)))  # type: ignore[call-arg]
        
        if hasattr(span, "set_attribute"):
            # Set error type per OTel specification
            span.set_attribute("error.type", error.__class__.__name__)  # type: ignore[call-arg]
            
            # Set recovery action if provided
            if recovery_action is not None:
                span.set_attribute("agentscript.error.recovery_action", recovery_action)  # type: ignore[call-arg]
            
            # Extract and record AgentScriptRuntimeError context
            try:
                from agentscript.runtime.errors import AgentScriptRuntimeError
                if isinstance(error, AgentScriptRuntimeError):
                    ctx = error.context
                    if ctx.run_id:
                        span.set_attribute("agentscript.run.id", ctx.run_id)  # type: ignore[call-arg]
                    if ctx.workflow_name:
                        span.set_attribute("agentscript.workflow.name", ctx.workflow_name)  # type: ignore[call-arg]
                    if ctx.step_id:
                        span.set_attribute("agentscript.step.id", ctx.step_id)  # type: ignore[call-arg]
                    if ctx.tool_name:
                        span.set_attribute("agentscript.tool.name", ctx.tool_name)  # type: ignore[call-arg]
                    if ctx.attempt > 0:
                        span.set_attribute("agentscript.error.attempt", ctx.attempt)  # type: ignore[call-arg]
                    if ctx.latency_ms is not None:
                        span.set_attribute("agentscript.error.latency_ms", ctx.latency_ms)  # type: ignore[call-arg]
                    if ctx.user_id:
                        span.set_attribute("agentscript.user.id", ctx.user_id)  # type: ignore[call-arg]
                    
                    # Set error category for SLO tracking
                    span.set_attribute("agentscript.error.category", error.error_category)  # type: ignore[call-arg]
            except ImportError:
                pass  # AgentScriptRuntimeError not available

    def record_event(
        self,
        span: _NoopSpan | object,
        name: str,
        attributes: dict[str, object] | None = None,
    ) -> None:
        """Record an event within a span (e.g., circuit breaker transition).
        
        Args:
            span: The span to record the event in
            name: Event name (e.g., "circuit_breaker_opened")
            attributes: Event attributes
        """
        if hasattr(span, "add_event"):
            span.add_event(name, attributes or {})  # type: ignore[call-arg]

    @staticmethod
    def add_llm_usage(
        span: _NoopSpan | object,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        """Record LLM token usage in a span.
        
        Args:
            span: The span
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            total_tokens: Total tokens (alternative to input+output)
        """
        if hasattr(span, "set_attribute"):
            if input_tokens is not None:
                span.set_attribute("gen_ai.usage.input_tokens", input_tokens)  # type: ignore[call-arg]
            if output_tokens is not None:
                span.set_attribute("gen_ai.usage.output_tokens", output_tokens)  # type: ignore[call-arg]
            if total_tokens is not None:
                span.set_attribute("gen_ai.usage.total_tokens", total_tokens)  # type: ignore[call-arg]


def _map_kind(kind: str) -> object | None:
    """Map kind string to SpanKind enum."""
    if SpanKind is None:
        return None
    if kind == "client":
        return SpanKind.CLIENT
    return SpanKind.INTERNAL

