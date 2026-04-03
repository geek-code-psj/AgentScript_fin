"""Replay-ready tool gateway for AgentScript runtime calls."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from agentscript.observability.otel import RuntimeTelemetry
from agentscript.runtime.clock import Clock, SystemClock
from agentscript.runtime.errors import ToolInvocationError
from agentscript.runtime.program import CircuitBreakerConfig, RetryConfig
from agentscript.runtime.records import ReplaySource, ToolCall, ToolResult
from agentscript.runtime.tools import ToolRegistry
from agentscript.runtime.tracing import SQLiteTraceRecorder


class CircuitPhase(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class CircuitBreakerState:
    phase: CircuitPhase = CircuitPhase.CLOSED
    recent_outcomes: list[bool] = field(default_factory=list)
    opened_at: float | None = None
    half_open_calls: int = 0

    def before_call(
        self,
        *,
        now: float,
        config: CircuitBreakerConfig,
    ) -> tuple[bool, CircuitPhase | None]:
        transition: CircuitPhase | None = None
        if self.phase is CircuitPhase.OPEN:
            if self.opened_at is not None and (now - self.opened_at) >= config.cooldown_seconds:
                self.phase = CircuitPhase.HALF_OPEN
                self.half_open_calls = 0
                transition = CircuitPhase.HALF_OPEN
            else:
                return False, transition

        if self.phase is CircuitPhase.HALF_OPEN:
            if self.half_open_calls >= config.half_open_max_calls:
                return False, transition
            self.half_open_calls += 1

        return True, transition

    def record_success(
        self,
        *,
        now: float,
        config: CircuitBreakerConfig,
    ) -> CircuitPhase | None:
        if self.phase is CircuitPhase.HALF_OPEN:
            self._close()
            return CircuitPhase.CLOSED
        self._remember(True, config)
        return None

    def record_failure(
        self,
        *,
        now: float,
        config: CircuitBreakerConfig,
    ) -> CircuitPhase | None:
        if self.phase is CircuitPhase.HALF_OPEN:
            self._open(now)
            return CircuitPhase.OPEN

        self._remember(False, config)
        if len(self.recent_outcomes) < config.min_calls:
            return None
        failure_rate = 1.0 - (sum(1 for outcome in self.recent_outcomes if outcome) / len(self.recent_outcomes))
        if failure_rate >= config.threshold:
            self._open(now)
            return CircuitPhase.OPEN
        return None

    def _remember(self, outcome: bool, config: CircuitBreakerConfig) -> None:
        self.recent_outcomes.append(outcome)
        if len(self.recent_outcomes) > config.window_size:
            self.recent_outcomes = self.recent_outcomes[-config.window_size :]

    def _open(self, now: float) -> None:
        self.phase = CircuitPhase.OPEN
        self.opened_at = now
        self.half_open_calls = 0

    def _close(self) -> None:
        self.phase = CircuitPhase.CLOSED
        self.opened_at = None
        self.half_open_calls = 0
        self.recent_outcomes.clear()


@dataclass(slots=True)
class ToolGateway:
    """Single choke-point around all tool calls, replay, and circuit state."""

    tools: ToolRegistry
    trace_recorder: SQLiteTraceRecorder | None = None
    replay_source: ReplaySource | None = None
    clock: Clock = field(default_factory=SystemClock)
    telemetry: RuntimeTelemetry = field(default_factory=RuntimeTelemetry)
    _circuits: dict[str, CircuitBreakerState] = field(default_factory=dict, init=False)

    async def invoke(
        self,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
        step_id: str,
        tool_name: str,
        arguments: dict[str, object],
        retry: RetryConfig | None,
        circuit_breaker: CircuitBreakerConfig | None,
    ) -> ToolResult:
        if self.replay_source is not None:
            with self.telemetry.span(
                "agentscript.tool.replay",
                kind="client",
                attributes={
                    "gen_ai.operation.name": "tool_replay",
                    "gen_ai.provider.name": "agentscript",
                    "gen_ai.conversation.id": run_id or "local-run",
                    "agentscript.workflow.name": workflow_name,
                    "agentscript.tool.name": tool_name,
                },
            ):
                return self._replay_result(
                    run_id=run_id,
                    workflow_name=workflow_name,
                    instruction_index=instruction_index,
                    step_id=step_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )

        gate_timestamp = self.clock.now()
        if circuit_breaker is not None:
            state = self._circuits.setdefault(tool_name, CircuitBreakerState())
            allowed, transition = state.before_call(now=gate_timestamp, config=circuit_breaker)
            if transition is not None:
                self._record_event(
                    run_id,
                    "circuit_transition",
                    workflow_name=workflow_name,
                    instruction_index=instruction_index,
                    payload={"tool_name": tool_name, "state": transition.value},
                )
            if not allowed:
                blocked_call = ToolCall(
                    run_id=run_id,
                    step_id=step_id,
                    workflow_name=workflow_name,
                    tool_name=tool_name,
                    args=arguments,
                    attempt=0,
                    timestamp=gate_timestamp,
                )
                blocked_result = ToolResult(
                    run_id=run_id,
                    step_id=step_id,
                    workflow_name=workflow_name,
                    tool_name=tool_name,
                    ok=False,
                    status_code=503,
                    payload=None,
                    error="circuit-open",
                    latency_ms=0.0,
                    retries=0,
                    timestamp=gate_timestamp,
                    source="circuit-open",
                )
                self._record_call(blocked_call)
                self._record_result(blocked_result)
                self._record_event(
                    run_id,
                    "circuit_rejected",
                    workflow_name=workflow_name,
                    instruction_index=instruction_index,
                    payload={"tool_name": tool_name, "state": state.phase.value},
                )
                return blocked_result

        retry_config = retry or RetryConfig()
        attempts = max(1, retry_config.attempts)
        last_failure: ToolResult | None = None

        with self.telemetry.span(
            "agentscript.tool.call",
            kind="client",
            attributes={
                "gen_ai.operation.name": "tool_call",
                "gen_ai.provider.name": "agentscript",
                "gen_ai.conversation.id": run_id or "local-run",
                "agentscript.workflow.name": workflow_name,
                "agentscript.tool.name": tool_name,
            },
        ) as span:
            for attempt in range(1, attempts + 1):
                started_at = self.clock.now()
                call = ToolCall(
                    run_id=run_id,
                    step_id=step_id,
                    workflow_name=workflow_name,
                    tool_name=tool_name,
                    args=arguments,
                    attempt=attempt,
                    timestamp=started_at,
                )
                self._record_call(call)
                if hasattr(span, "set_attribute"):
                    span.set_attribute("agentscript.tool.attempt", attempt)  # type: ignore[call-arg]
                try:
                    payload = await self.tools.invoke(tool_name, **arguments)
                except Exception as error:  # noqa: BLE001
                    self.telemetry.mark_error(span, error)
                    finished_at = self.clock.now()
                    last_failure = ToolResult(
                        run_id=run_id,
                        step_id=step_id,
                        workflow_name=workflow_name,
                        tool_name=tool_name,
                        ok=False,
                        status_code=_status_code(error),
                        payload=None,
                        error=str(error),
                        latency_ms=max(0.0, (finished_at - started_at) * 1000.0),
                        retries=attempt - 1,
                        timestamp=finished_at,
                        source="live",
                    )
                    self._record_result(last_failure)

                    if attempt < attempts:
                        delay = _backoff_delay_seconds(retry_config, attempt)
                        self._record_event(
                            run_id,
                            "retry_scheduled",
                            workflow_name=workflow_name,
                            instruction_index=instruction_index,
                            payload={
                                "tool_name": tool_name,
                                "attempt": attempt,
                                "delay_seconds": delay,
                                "backoff": retry_config.backoff,
                            },
                        )
                        await self.clock.sleep(delay)
                        continue
                    break

                finished_at = self.clock.now()
                result = ToolResult(
                    run_id=run_id,
                    step_id=step_id,
                    workflow_name=workflow_name,
                    tool_name=tool_name,
                    ok=True,
                    status_code=200,
                    payload=payload,
                    error=None,
                    latency_ms=max(0.0, (finished_at - started_at) * 1000.0),
                    retries=attempt - 1,
                    timestamp=finished_at,
                    source="live",
                )
                self._record_result(result)
                if hasattr(span, "set_attribute"):
                    span.set_attribute("agentscript.tool.status_code", 200)  # type: ignore[call-arg]
                if circuit_breaker is not None:
                    transition = self._circuits.setdefault(tool_name, CircuitBreakerState()).record_success(
                        now=finished_at,
                        config=circuit_breaker,
                    )
                    if transition is not None:
                        self._record_event(
                            run_id,
                            "circuit_transition",
                            workflow_name=workflow_name,
                            instruction_index=instruction_index,
                            payload={"tool_name": tool_name, "state": transition.value},
                        )
                return result

        if last_failure is None:
            raise RuntimeError("Tool gateway exhausted without a result.")

        if circuit_breaker is not None:
            transition = self._circuits.setdefault(tool_name, CircuitBreakerState()).record_failure(
                now=last_failure.timestamp,
                config=circuit_breaker,
            )
            if transition is not None:
                self._record_event(
                    run_id,
                    "circuit_transition",
                    workflow_name=workflow_name,
                    instruction_index=instruction_index,
                    payload={"tool_name": tool_name, "state": transition.value},
                )
        return last_failure

    def _replay_result(
        self,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
        step_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> ToolResult:
        recorded = self.replay_source.tool_results.get(step_id) if self.replay_source else None
        if recorded is None:
            raise ToolInvocationError(
                f"Replay trace does not contain a ToolResult for step '{step_id}'."
            )

        replay_timestamp = self.clock.now()
        call = ToolCall(
            run_id=run_id,
            step_id=step_id,
            workflow_name=workflow_name,
            tool_name=tool_name,
            args=arguments,
            attempt=max(1, recorded.retries + 1),
            timestamp=replay_timestamp,
            replayed=True,
        )
        replayed_result = replace(
            recorded,
            run_id=run_id,
            workflow_name=workflow_name,
            replayed=True,
            source="replay",
            timestamp=replay_timestamp,
        )
        self._record_call(call)
        self._record_result(replayed_result)
        self._record_event(
            run_id,
            "tool_replayed",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={"tool_name": tool_name, "step_id": step_id},
        )
        return replayed_result

    def _record_call(self, call: ToolCall) -> None:
        if self.trace_recorder is not None:
            self.trace_recorder.record_tool_call(call)

    def _record_result(self, result: ToolResult) -> None:
        if self.trace_recorder is not None:
            self.trace_recorder.record_tool_result(result)

    def _record_event(
        self,
        run_id: str | None,
        event_type: str,
        *,
        workflow_name: str,
        instruction_index: int,
        payload: dict[str, object],
    ) -> None:
        if self.trace_recorder is not None and run_id is not None:
            self.trace_recorder.record(
                run_id,
                event_type,
                workflow_name=workflow_name,
                instruction_index=instruction_index,
                payload=payload,
            )


def _backoff_delay_seconds(config: RetryConfig, failure_index: int) -> float:
    if config.backoff == "exponential":
        delay = config.base_delay_seconds * (2 ** (failure_index - 1))
    elif config.backoff == "linear":
        delay = config.base_delay_seconds * failure_index
    else:
        delay = config.base_delay_seconds
    return min(delay, config.max_delay_seconds)


def _status_code(error: Exception) -> int:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return 500
