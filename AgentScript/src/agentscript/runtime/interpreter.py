"""Async interpreter for AgentScript IR."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentscript.compiler.ast import (
    BinaryExpression,
    CallExpression,
    Expression,
    IdentifierExpression,
    LiteralExpression,
    MemberExpression,
    UnaryExpression,
)
from agentscript.compiler.ir import IRInstruction, IRWorkflow, OpCode
from agentscript.runtime.environment import Environment
from agentscript.runtime.errors import AgentScriptRuntimeError, ToolInvocationError
from agentscript.runtime.memory import MemoryManager
from agentscript.runtime.program import (
    AgentPolicy,
    CircuitBreakerConfig,
    FallbackStep,
    RetryConfig,
    RuntimeProgram,
)
from agentscript.runtime.tracing import SQLiteTraceRecorder
from agentscript.runtime.tools import ToolRegistry


@dataclass(slots=True)
class CircuitBreakerState:
    failures: int = 0
    total: int = 0
    is_open: bool = False

    def record(self, *, success: bool, config: CircuitBreakerConfig) -> None:
        self.total += 1
        if not success:
            self.failures += 1
        if self.total > 0:
            self.is_open = (self.failures / self.total) >= config.threshold


class AsyncInterpreter:
    """Executes AgentScript workflows using an async tool runtime."""

    def __init__(
        self,
        program: RuntimeProgram,
        *,
        tools: ToolRegistry | None = None,
        memory: MemoryManager | None = None,
        trace_recorder: SQLiteTraceRecorder | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.program = program
        self.tools = tools or ToolRegistry()
        self.memory = memory or MemoryManager()
        self.trace_recorder = trace_recorder
        self.sleep = sleep or asyncio.sleep
        self._circuit_states: dict[str, CircuitBreakerState] = {}
        self.last_run_id: str | None = None

    async def run_workflow(
        self,
        workflow_name: str,
        *,
        arguments: dict[str, object] | None = None,
        agent_name: str | None = None,
    ) -> object:
        workflow = self.program.workflow(workflow_name)
        agent = self.program.agent(agent_name)
        environment = Environment()
        if arguments:
            for name, value in arguments.items():
                environment.define(name, value)
        run_id = self._start_run(
            workflow_name,
            agent_name=agent.name if agent else None,
            arguments=arguments or {},
        )
        self.last_run_id = run_id
        try:
            result = await self._execute_workflow(
                workflow,
                arguments or {},
                environment,
                agent,
                run_id=run_id,
            )
        except Exception as error:  # noqa: BLE001
            self._finish_run(run_id, status="failed", error=error)
            raise
        self._finish_run(run_id, status="completed", output=result)
        return result

    async def _execute_workflow(
        self,
        workflow: IRWorkflow,
        arguments: dict[str, object],
        parent: Environment | None,
        agent: AgentPolicy | None,
        *,
        run_id: str | None,
    ) -> object:
        frame = Environment(parent=parent)
        self._record(
            run_id,
            "workflow_started",
            workflow_name=workflow.name,
            payload={"arguments": arguments},
        )
        for parameter in workflow.parameters:
            if parameter not in arguments:
                raise AgentScriptRuntimeError(
                    f"Missing runtime argument '{parameter}' for workflow '{workflow.name}'."
                )
            frame.define(parameter, arguments[parameter])
            self.memory.session.put(parameter, arguments[parameter])
            self._record(
                run_id,
                "memory_write",
                workflow_name=workflow.name,
                payload={
                    "key": parameter,
                    "value": arguments[parameter],
                    "source": "parameter",
                    "semantic_indexed": False,
                },
            )

        instructions = workflow.instructions
        labels = _build_label_table(instructions)
        pointer = 0

        while pointer < len(instructions):
            instruction = instructions[pointer]
            opcode = instruction.opcode
            self._record_instruction(run_id, workflow.name, pointer, instruction)

            if opcode is OpCode.LOAD_CONST:
                frame.define(instruction.result, instruction.args[0])
                pointer += 1
                continue

            if opcode is OpCode.LOAD_NAME:
                frame.define(instruction.result, frame.get(instruction.args[0]))
                pointer += 1
                continue

            if opcode is OpCode.LOAD_ATTR:
                value = frame.get(instruction.args[0])
                frame.define(instruction.result, _load_attribute(value, instruction.args[1]))
                pointer += 1
                continue

            if opcode is OpCode.STORE_NAME:
                value = frame.get(instruction.args[1])
                frame.define(instruction.args[0], value)
                self.memory.write(str(instruction.args[0]), value)
                self._record(
                    run_id,
                    "memory_write",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={"key": instruction.args[0], "value": value, "source": "store"},
                )
                pointer += 1
                continue

            if opcode is OpCode.MEM_SEARCH:
                query = frame.get(instruction.args[0])
                results = self.memory.search(str(query))
                frame.define(instruction.result, results)
                self._record(
                    run_id,
                    "mem_search",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={"query": query, "results": results, "count": len(results)},
                )
                pointer += 1
                continue

            if opcode is OpCode.CALL_TOOL:
                result = await self._invoke_tool_instruction(
                    instruction,
                    frame,
                    agent,
                    run_id=run_id,
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                )
                frame.define(instruction.result, result)
                pointer += 1
                continue

            if opcode is OpCode.CALL_WORKFLOW:
                result = await self._invoke_workflow_instruction(
                    instruction,
                    frame,
                    agent,
                    run_id=run_id,
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                )
                frame.define(instruction.result, result)
                pointer += 1
                continue

            if opcode is OpCode.STEP:
                result = await self._invoke_step_instruction(
                    instruction,
                    frame,
                    agent,
                    run_id=run_id,
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                )
                frame.define(instruction.args[0], result)
                self.memory.write(str(instruction.args[0]), result)
                self._record(
                    run_id,
                    "memory_write",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={"key": instruction.args[0], "value": result, "source": "step"},
                )
                pointer += 1
                continue

            if opcode is OpCode.COMPARE_OP:
                left = frame.get(instruction.args[1])
                right = frame.get(instruction.args[2])
                frame.define(instruction.result, _compare(instruction.args[0], left, right))
                pointer += 1
                continue

            if opcode is OpCode.BINARY_OP:
                left = frame.get(instruction.args[1])
                right = frame.get(instruction.args[2])
                frame.define(instruction.result, _binary_op(instruction.args[0], left, right))
                pointer += 1
                continue

            if opcode is OpCode.UNARY_OP:
                operand = frame.get(instruction.args[1])
                frame.define(instruction.result, _unary_op(instruction.args[0], operand))
                pointer += 1
                continue

            if opcode is OpCode.POP:
                frame.get(instruction.args[0])
                pointer += 1
                continue

            if opcode is OpCode.JUMP_IF_FALSE:
                condition = frame.get(instruction.args[0])
                taken = not bool(condition)
                self._record(
                    run_id,
                    "branch",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={"condition": condition, "taken": taken, "target": instruction.args[1]},
                )
                if taken:
                    pointer = labels[instruction.args[1]]
                else:
                    pointer += 1
                continue

            if opcode is OpCode.JUMP:
                pointer = labels[instruction.args[0]]
                continue

            if opcode is OpCode.LABEL:
                pointer += 1
                continue

            if opcode is OpCode.RETURN:
                result = frame.get(instruction.args[0])
                self._record(
                    run_id,
                    "workflow_return",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={"value": result},
                )
                return result

            raise AgentScriptRuntimeError(f"Unsupported opcode '{opcode}'.")

        self._record(
            run_id,
            "workflow_completed_without_return",
            workflow_name=workflow.name,
            payload={},
        )
        return None

    async def _invoke_tool_instruction(
        self,
        instruction: IRInstruction,
        environment: Environment,
        agent: AgentPolicy | None,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        tool_name = instruction.args[0]
        arguments = self._resolve_bound_arguments(
            tool_name,
            instruction.args[1],
            environment,
            is_workflow=False,
        )
        return await self._invoke_tool(
            tool_name,
            arguments,
            environment,
            agent,
            run_id=run_id,
            workflow_name=workflow_name,
            instruction_index=instruction_index,
        )

    async def _invoke_workflow_instruction(
        self,
        instruction: IRInstruction,
        environment: Environment,
        agent: AgentPolicy | None,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        workflow_name = instruction.args[0]
        arguments = self._resolve_bound_arguments(
            workflow_name,
            instruction.args[1],
            environment,
            is_workflow=True,
        )
        workflow = self.program.workflow(workflow_name)
        self._record(
            run_id,
            "workflow_call",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={"arguments": arguments},
        )
        return await self._execute_workflow(
            workflow,
            arguments,
            environment,
            agent,
            run_id=run_id,
        )

    async def _invoke_step_instruction(
        self,
        instruction: IRInstruction,
        environment: Environment,
        agent: AgentPolicy | None,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        arguments = self._resolve_bound_arguments(
            instruction.args[1],
            instruction.args[2],
            environment,
            is_workflow=False,
        )
        return await self._invoke_tool(
            instruction.args[1],
            arguments,
            environment,
            agent,
            run_id=run_id,
            workflow_name=workflow_name,
            instruction_index=instruction_index,
        )

    def _resolve_bound_arguments(
        self,
        callable_name: str,
        bindings: tuple[tuple[str | None, str], ...],
        environment: Environment,
        *,
        is_workflow: bool,
    ) -> dict[str, object]:
        signature = (
            self.program.semantic_model.workflows[callable_name]
            if is_workflow
            else self.program.semantic_model.tools[callable_name]
        )
        positional = [environment.get(temp_name) for name, temp_name in bindings if name is None]
        named = {
            name: environment.get(temp_name)
            for name, temp_name in bindings
            if name is not None
        }

        bound: dict[str, object] = {}
        positional_index = 0
        for parameter in signature.parameters:
            if positional_index < len(positional):
                bound[parameter.name] = positional[positional_index]
                positional_index += 1
            elif parameter.name in named:
                bound[parameter.name] = named.pop(parameter.name)

        if named:
            unknown = ", ".join(sorted(named))
            raise AgentScriptRuntimeError(
                f"Unknown bound argument(s) for '{callable_name}': {unknown}."
            )
        return bound

    async def _invoke_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        environment: Environment,
        agent: AgentPolicy | None,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        if agent and agent.circuit_breaker and self._is_circuit_open(tool_name):
            self._record(
                run_id,
                "circuit_open",
                workflow_name=workflow_name,
                instruction_index=instruction_index,
                payload={"tool_name": tool_name},
            )
            return await self._execute_fallback(
                tool_name,
                environment,
                agent,
                "circuit-open",
                run_id=run_id,
                workflow_name=workflow_name,
                instruction_index=instruction_index,
            )

        retry = agent.retry if agent and agent.retry else RetryConfig(attempts=1)
        attempts = max(1, retry.attempts)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            self._record(
                run_id,
                "tool_attempt",
                workflow_name=workflow_name,
                instruction_index=instruction_index,
                payload={"tool_name": tool_name, "attempt": attempt, "arguments": arguments},
            )
            try:
                result = await self.tools.invoke(tool_name, **arguments)
                self._record_circuit(tool_name, success=True, config=agent.circuit_breaker if agent else None)
                self._record(
                    run_id,
                    "tool_success",
                    workflow_name=workflow_name,
                    instruction_index=instruction_index,
                    payload={"tool_name": tool_name, "attempt": attempt, "result": result},
                )
                return result
            except Exception as error:  # noqa: BLE001
                last_error = error
                self._record(
                    run_id,
                    "tool_failure",
                    workflow_name=workflow_name,
                    instruction_index=instruction_index,
                    payload={"tool_name": tool_name, "attempt": attempt, "error": error},
                )
                if attempt < attempts:
                    delay = _backoff_delay_seconds(retry, attempt)
                    self._record(
                        run_id,
                        "retry_scheduled",
                        workflow_name=workflow_name,
                        instruction_index=instruction_index,
                        payload={
                            "tool_name": tool_name,
                            "attempt": attempt,
                            "delay_seconds": delay,
                            "backoff": retry.backoff,
                        },
                    )
                    await self.sleep(delay)
                    continue

        self._record_circuit(tool_name, success=False, config=agent.circuit_breaker if agent else None)

        if agent and agent.fallback_steps:
            return await self._execute_fallback(
                tool_name,
                environment,
                agent,
                "tool-failure",
                run_id=run_id,
                workflow_name=workflow_name,
                instruction_index=instruction_index,
            )

        raise ToolInvocationError(
            f"Tool '{tool_name}' failed after {attempts} attempt(s): {last_error}"
        ) from last_error

    def _record_circuit(
        self,
        tool_name: str,
        *,
        success: bool,
        config: CircuitBreakerConfig | None,
    ) -> None:
        if config is None:
            return
        state = self._circuit_states.setdefault(tool_name, CircuitBreakerState())
        state.record(success=success, config=config)

    def _is_circuit_open(self, tool_name: str) -> bool:
        state = self._circuit_states.get(tool_name)
        return state.is_open if state else False

    async def _execute_fallback(
        self,
        tool_name: str,
        environment: Environment,
        agent: AgentPolicy,
        reason: str,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        if not agent.fallback_steps:
            raise ToolInvocationError(
                f"Tool '{tool_name}' cannot run because the agent circuit is open."
                if reason == "circuit-open"
                else f"Tool '{tool_name}' failed and no fallback is available."
            )

        self._record(
            run_id,
            "fallback_started",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={"tool_name": tool_name, "reason": reason},
        )
        result: object = None
        for step in agent.fallback_steps:
            result = await self._invoke_fallback_step(
                step,
                environment,
                run_id=run_id,
                workflow_name=workflow_name,
                instruction_index=instruction_index,
            )
        return result

    async def _invoke_fallback_step(
        self,
        step: FallbackStep,
        environment: Environment,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        arguments = {
            parameter.name: await self._evaluate_fallback_expression(parameter.expression, environment)
            for parameter in step.arguments
            if parameter.name is not None
        }
        positional = [
            await self._evaluate_fallback_expression(parameter.expression, environment)
            for parameter in step.arguments
            if parameter.name is None
        ]
        if positional:
            raise AgentScriptRuntimeError(
                f"Fallback step '{step.name}' currently requires named arguments or no arguments."
            )
        self._record(
            run_id,
            "fallback_step",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={"step_name": step.name, "tool_name": step.tool_name, "arguments": arguments},
        )
        result = await self.tools.invoke(step.tool_name, **arguments)
        self._record(
            run_id,
            "fallback_result",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={"step_name": step.name, "tool_name": step.tool_name, "result": result},
        )
        return result

    async def _evaluate_fallback_expression(
        self,
        expression: Expression,
        environment: Environment,
    ) -> object:
        if isinstance(expression, IdentifierExpression):
            return environment.get(expression.name)
        if isinstance(expression, LiteralExpression):
            return expression.value
        if isinstance(expression, MemberExpression):
            value = await self._evaluate_fallback_expression(expression.object, environment)
            return _load_attribute(value, expression.attribute)
        if isinstance(expression, UnaryExpression):
            operand = await self._evaluate_fallback_expression(expression.operand, environment)
            return _unary_op(expression.operator, operand)
        if isinstance(expression, BinaryExpression):
            left = await self._evaluate_fallback_expression(expression.left, environment)
            right = await self._evaluate_fallback_expression(expression.right, environment)
            if expression.operator in {"<", "<=", ">", ">=", "==", "!="}:
                return _compare(expression.operator, left, right)
            return _binary_op(expression.operator, left, right)
        if isinstance(expression, CallExpression):
            raise AgentScriptRuntimeError(
                "Fallback argument expressions do not support nested calls yet."
            )
        raise AgentScriptRuntimeError("Unsupported fallback expression encountered.")

    def _start_run(
        self,
        workflow_name: str,
        *,
        agent_name: str | None,
        arguments: dict[str, object],
    ) -> str | None:
        if self.trace_recorder is None:
            return None
        return self.trace_recorder.start_run(
            workflow_name,
            agent_name=agent_name,
            arguments=arguments,
        )

    def _finish_run(
        self,
        run_id: str | None,
        *,
        status: str,
        output: object = None,
        error: object = None,
    ) -> None:
        if self.trace_recorder is None or run_id is None:
            return
        self.trace_recorder.finish_run(
            run_id,
            status=status,
            output=output,
            error=error,
        )

    def _record_instruction(
        self,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
        instruction: IRInstruction,
    ) -> None:
        self._record(
            run_id,
            "instruction",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={
                "opcode": instruction.opcode.value,
                "args": instruction.args,
                "result": instruction.result,
            },
        )

    def _record(
        self,
        run_id: str | None,
        event_type: str,
        *,
        workflow_name: str | None,
        instruction_index: int | None = None,
        payload: dict[str, object],
    ) -> None:
        if self.trace_recorder is None or run_id is None:
            return
        self.trace_recorder.record(
            run_id,
            event_type,
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload=payload,
        )


def _build_label_table(instructions: tuple[IRInstruction, ...]) -> dict[str, int]:
    return {
        instruction.args[0]: index
        for index, instruction in enumerate(instructions)
        if instruction.opcode is OpCode.LABEL
    }


def _load_attribute(value: object, attribute: str) -> object:
    if isinstance(value, dict):
        return value[attribute]
    return getattr(value, attribute)


def _compare(operator: str, left: object, right: object) -> bool:
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    raise AgentScriptRuntimeError(f"Unsupported comparison operator '{operator}'.")


def _binary_op(operator: str, left: object, right: object) -> object:
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator == "*":
        return left * right
    if operator == "/":
        return left / right
    raise AgentScriptRuntimeError(f"Unsupported binary operator '{operator}'.")


def _unary_op(operator: str, operand: object) -> object:
    if operator == "-":
        return -operand
    raise AgentScriptRuntimeError(f"Unsupported unary operator '{operator}'.")


def _backoff_delay_seconds(config: RetryConfig, failure_index: int) -> float:
    if config.backoff == "exponential":
        return config.base_delay_seconds * (2 ** (failure_index - 1))
    if config.backoff == "linear":
        return config.base_delay_seconds * failure_index
    return config.base_delay_seconds


async def run_workflow(
    program: RuntimeProgram,
    workflow_name: str,
    *,
    tools: ToolRegistry | None = None,
    memory: MemoryManager | None = None,
    trace_recorder: SQLiteTraceRecorder | None = None,
    arguments: dict[str, object] | None = None,
    agent_name: str | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> object:
    """Convenience helper for running one workflow."""

    interpreter = AsyncInterpreter(
        program,
        tools=tools,
        memory=memory,
        trace_recorder=trace_recorder,
        sleep=sleep,
    )
    return await interpreter.run_workflow(
        workflow_name,
        arguments=arguments,
        agent_name=agent_name,
    )
