"""Optional OpenTelemetry integration for AgentScript runtime spans."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

try:  # pragma: no cover - import availability varies by environment
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode
except Exception:  # pragma: no cover - fallback path
    trace = None
    SpanKind = None
    Status = None
    StatusCode = None


class _NoopSpan:
    def set_attribute(self, key: str, value: object) -> None:
        return None

    def set_attributes(self, attributes: dict[str, object]) -> None:
        return None

    def record_exception(self, error: BaseException) -> None:
        return None


@dataclass(slots=True)
class RuntimeTelemetry:
    """Thin wrapper that degrades to a no-op when OTel is unavailable."""

    tracer_name: str = "agentscript.runtime"
    tracer: object | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if trace is not None:
            self.tracer = trace.get_tracer(self.tracer_name)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: dict[str, object] | None = None,
    ) -> Iterator[_NoopSpan | object]:
        span_kind = _map_kind(kind)
        if self.tracer is None or span_kind is None:
            noop = _NoopSpan()
            yield noop
            return

        with self.tracer.start_as_current_span(  # type: ignore[union-attr]
            name,
            kind=span_kind,
            attributes=attributes or {},
        ) as span:
            yield span

    def mark_error(self, span: _NoopSpan | object, error: BaseException) -> None:
        if hasattr(span, "record_exception"):
            span.record_exception(error)  # type: ignore[call-arg]
        if Status is not None and StatusCode is not None and hasattr(span, "set_status"):
            span.set_status(Status(StatusCode.ERROR, str(error)))  # type: ignore[call-arg]
        if hasattr(span, "set_attribute"):
            span.set_attribute("error.type", error.__class__.__name__)  # type: ignore[call-arg]


def _map_kind(kind: str) -> object | None:
    if SpanKind is None:
        return None
    if kind == "client":
        return SpanKind.CLIENT
    return SpanKind.INTERNAL
