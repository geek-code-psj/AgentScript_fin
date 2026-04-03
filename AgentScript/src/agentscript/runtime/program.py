"""Runtime-ready compilation bundle for AgentScript."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentscript.compiler.ast import (
    AgentDeclaration,
    BinaryExpression,
    CallArgument,
    CircuitBreakerPolicy,
    Expression,
    FallbackPolicy,
    IdentifierExpression,
    LiteralExpression,
    RetryPolicy,
    StepStatement,
    UnaryExpression,
)
from agentscript.compiler.ir import IRProgram, IRWorkflow, lower_program
from agentscript.compiler.parser import parse_file, parse_source
from agentscript.compiler.semantics import SemanticModel, analyze_program
from agentscript.runtime.errors import UnknownAgentError, UnknownWorkflowError


@dataclass(frozen=True, slots=True)
class RetryConfig:
    attempts: int = 1
    backoff: str = "fixed"
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 16.0


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    threshold: float
    window_size: int = 4
    cooldown_seconds: float = 30.0
    half_open_max_calls: int = 1
    min_calls: int = 2


@dataclass(frozen=True, slots=True)
class RuntimeArgument:
    name: str | None
    expression: Expression


@dataclass(frozen=True, slots=True)
class FallbackStep:
    name: str
    tool_name: str
    arguments: tuple[RuntimeArgument, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    name: str
    retry: RetryConfig | None = None
    circuit_breaker: CircuitBreakerConfig | None = None
    fallback_steps: tuple[FallbackStep, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeProgram:
    ir: IRProgram
    semantic_model: SemanticModel
    agents: dict[str, AgentPolicy] = field(default_factory=dict)
    workflows: dict[str, IRWorkflow] = field(default_factory=dict)
    default_agent_name: str | None = None

    def workflow(self, name: str) -> IRWorkflow:
        if name not in self.workflows:
            raise UnknownWorkflowError(f"Workflow '{name}' does not exist.")
        return self.workflows[name]

    def agent(self, name: str | None) -> AgentPolicy | None:
        if name is None:
            if self.default_agent_name is None:
                return None
            return self.agents[self.default_agent_name]
        if name not in self.agents:
            raise UnknownAgentError(f"Agent '{name}' does not exist.")
        return self.agents[name]


def compile_runtime_program(source: str, *, filename: str = "<memory>") -> RuntimeProgram:
    """Parse, analyze, lower, and package a runtime-ready AgentScript program."""

    program = parse_source(source, filename=filename)
    semantic_model = analyze_program(program)
    ir_program = lower_program(program, semantic_model)
    workflows = {workflow.name: workflow for workflow in ir_program.workflows}
    agents = {
        declaration.name: _extract_agent_policy(declaration)
        for declaration in program.declarations
        if isinstance(declaration, AgentDeclaration)
    }
    default_agent_name = next(iter(agents)) if len(agents) == 1 else None
    return RuntimeProgram(
        ir=ir_program,
        semantic_model=semantic_model,
        agents=agents,
        workflows=workflows,
        default_agent_name=default_agent_name,
    )


def compile_runtime_file(path: str | Path) -> RuntimeProgram:
    """Compile an AgentScript file for runtime execution."""

    source_path = Path(path)
    return compile_runtime_program(
        source_path.read_text(encoding="utf-8"),
        filename=str(source_path),
    )


def _extract_agent_policy(declaration: AgentDeclaration) -> AgentPolicy:
    retry: RetryConfig | None = None
    circuit_breaker: CircuitBreakerConfig | None = None
    fallback_steps: list[FallbackStep] = []

    for item in declaration.body:
        if isinstance(item, RetryPolicy):
            retry = _parse_retry(item)
        elif isinstance(item, CircuitBreakerPolicy):
            circuit_breaker = _parse_circuit_breaker(item)
        elif isinstance(item, FallbackPolicy):
            fallback_steps.extend(_parse_fallback(item))

    return AgentPolicy(
        name=declaration.name,
        retry=retry,
        circuit_breaker=circuit_breaker,
        fallback_steps=tuple(fallback_steps),
    )


def _parse_retry(policy: RetryPolicy) -> RetryConfig:
    first = policy.arguments[0].value
    attempts = first.value if isinstance(first, LiteralExpression) and isinstance(first.value, int) else 1
    backoff = "fixed"
    base_delay_seconds = 2.0
    max_delay_seconds = 16.0
    for argument in policy.arguments[1:]:
        if argument.name == "backoff":
            if isinstance(argument.value, IdentifierExpression):
                backoff = argument.value.name
            elif isinstance(argument.value, LiteralExpression) and isinstance(argument.value.value, str):
                backoff = argument.value.value
        elif argument.name == "base_delay_seconds":
            base_delay_seconds = _numeric_literal(argument.value, default=base_delay_seconds)
        elif argument.name == "max_delay_seconds":
            max_delay_seconds = _numeric_literal(argument.value, default=max_delay_seconds)
    return RetryConfig(
        attempts=attempts,
        backoff=backoff,
        base_delay_seconds=float(base_delay_seconds),
        max_delay_seconds=float(max_delay_seconds),
    )


def _parse_circuit_breaker(policy: CircuitBreakerPolicy) -> CircuitBreakerConfig:
    threshold = 1.0
    window_size = 4
    cooldown_seconds = 30.0
    half_open_max_calls = 1
    min_calls = 2
    for argument in policy.arguments:
        if argument.name == "threshold":
            threshold = _numeric_literal(argument.value, default=threshold)
        elif argument.name == "window":
            window_size = int(_numeric_literal(argument.value, default=window_size))
        elif argument.name == "cooldown_seconds":
            cooldown_seconds = _numeric_literal(argument.value, default=cooldown_seconds)
        elif argument.name == "half_open_max_calls":
            half_open_max_calls = int(
                _numeric_literal(argument.value, default=half_open_max_calls)
            )
        elif argument.name == "min_calls":
            min_calls = int(_numeric_literal(argument.value, default=min_calls))
    if all(argument.name is None for argument in policy.arguments):
        threshold = _numeric_literal(policy.arguments[0].value, default=threshold)
    return CircuitBreakerConfig(
        threshold=float(threshold),
        window_size=max(1, int(window_size)),
        cooldown_seconds=float(cooldown_seconds),
        half_open_max_calls=max(1, int(half_open_max_calls)),
        min_calls=max(1, int(min_calls)),
    )


def _parse_fallback(policy: FallbackPolicy) -> list[FallbackStep]:
    fallback_steps: list[FallbackStep] = []
    for statement in policy.body:
        if not isinstance(statement, StepStatement):
            continue
        fallback_steps.append(
            FallbackStep(
                name=statement.name,
                tool_name=statement.tool_name,
                arguments=tuple(
                    RuntimeArgument(name=argument.name, expression=argument.value)
                    for argument in statement.arguments
                ),
            )
        )
    return fallback_steps


def _numeric_literal(expression: Expression, *, default: float) -> float:
    if isinstance(expression, LiteralExpression) and isinstance(expression.value, (int, float)):
        return float(expression.value)
    return default
