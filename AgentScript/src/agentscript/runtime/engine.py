"""Replay-ready async runtime engine for AgentScript."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agentscript.compiler.ast import (
    BinaryExpression,
    CallExpression,
    Expression,
    IdentifierExpression,
    LiteralExpression,
    MemberExpression,
    UnaryExpression,
)
from agentscript.compiler.ir import (
    BoundArgument,
    IRInstruction,
    IRWorkflow,
    MemorySetUnit,
    OpCode,
    ToolCallUnit,
    ToolResultUnit,
)
from agentscript.observability.otel import RuntimeTelemetry
from agentscript.runtime.clock import Clock, FunctionalClock, ReplayClock, SystemClock
from agentscript.runtime.environment import Environment
from agentscript.runtime.errors import AgentScriptRuntimeError, ToolInvocationError
from agentscript.runtime.gateway import ToolGateway
from agentscript.runtime.memory import MemoryManager
from agentscript.runtime.program import AgentPolicy, FallbackStep, RuntimeProgram
from agentscript.runtime.records import ReplaySource, ToolResult
from agentscript.runtime.tools import ToolRegistry
from agentscript.runtime.tracing import SQLiteTraceRecorder


class AsyncInterpreter:
    """Executes AgentScript IR with replay-aware tool calls."""

    def __init__(
        self,
        program: RuntimeProgram,
        *,
        tools: ToolRegistry | None = None,
        memory: MemoryManager | None = None,
        trace_recorder: SQLiteTraceRecorder | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Clock | None = None,
        replay_source: ReplaySource | None = None,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        self.program = program
        self.tools = tools or ToolRegistry()
        self.memory = memory or MemoryManager()
        self.trace_recorder = trace_recorder
        self.replay_source = replay_source
        self.telemetry = telemetry or RuntimeTelemetry()
        self.clock = self._resolve_clock(clock=clock, sleep=sleep, replay_source=replay_source)
        self.gateway = ToolGateway(
            self.tools,
            trace_recorder=trace_recorder,
            replay_source=replay_source,
            clock=self.clock,
            telemetry=self.telemetry,
        )
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
        run_arguments = arguments or {}
        run_id = self._start_run(
            workflow_name,
            agent_name=agent.name if agent else None,
            arguments=run_arguments,
        )
        self.last_run_id = run_id
        with self.telemetry.span(
            "agentscript.workflow",
            kind="internal",
            attributes={
                "gen_ai.agent.name": agent.name if agent else "default",
                "gen_ai.operation.name": "workflow",
                "gen_ai.provider.name": "agentscript",
                "gen_ai.conversation.id": run_id or "local-run",
                "agentscript.workflow.name": workflow_name,
            },
        ) as span:
            try:
                result = await self._execute_workflow(
                    workflow,
                    run_arguments,
                    parent=None,
                    agent=agent,
                    run_id=run_id,
                )
            except Exception as error:  # noqa: BLE001
                self.telemetry.mark_error(span, error)
                self._finish_run(run_id, status="failed", error=error)
                raise

            self._finish_run(run_id, status="completed", output=result)
            if hasattr(span, "set_attribute"):
                span.set_attribute("agentscript.workflow.status", "completed")  # type: ignore[call-arg]
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
                frame.define(_result_name(instruction), instruction.args[0])
                pointer += 1
                continue

            if opcode is OpCode.LOAD_NAME:
                frame.define(_result_name(instruction), frame.get(str(instruction.args[0])))
                pointer += 1
                continue

            if opcode is OpCode.LOAD_ATTR:
                value = frame.get(str(instruction.args[0]))
                frame.define(
                    _result_name(instruction),
                    _load_attribute(value, str(instruction.args[1])),
                )
                pointer += 1
                continue

            if opcode is OpCode.STORE_NAME:
                target_name = str(instruction.args[0])
                value = frame.get(str(instruction.args[1]))
                frame.define(target_name, value)
                pointer += 1
                continue

            if opcode is OpCode.MEM_SET:
                unit = instruction.args[0]
                if not isinstance(unit, MemorySetUnit):
                    raise AgentScriptRuntimeError("Invalid MEM_SET payload in IR.")
                value = frame.get(unit.value_temp)
                self.memory.write(unit.key, value)
                self._record(
                    run_id,
                    "memory_write",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={
                        "key": unit.key,
                        "value": value,
                        "source": "mem_set",
                        "semantic_indexed": unit.semantic,
                    },
                )
                pointer += 1
                continue

            if opcode is OpCode.MEM_SEARCH:
                query = frame.get(str(instruction.args[0]))
                with self.telemetry.span(
                    "agentscript.mem_search",
                    kind="internal",
                    attributes={
                        "gen_ai.operation.name": "memory_search",
                        "gen_ai.provider.name": "agentscript",
                        "gen_ai.conversation.id": run_id or "local-run",
                        "agentscript.workflow.name": workflow.name,
                    },
                ):
                    results = self.memory.search(str(query))
                frame.define(_result_name(instruction), results)
                self._record(
                    run_id,
                    "mem_search",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={"query": query, "results": results, "count": len(results)},
                )
                pointer += 1
                continue

            if opcode is OpCode.TOOL_CALL:
                unit = instruction.args[0]
                if not isinstance(unit, ToolCallUnit):
                    raise AgentScriptRuntimeError("Invalid TOOL_CALL payload in IR.")
                bound_arguments = self._resolve_bound_arguments(
                    unit.tool_name,
                    unit.arguments,
                    frame,
                    is_workflow=False,
                )
                call_result = await self.gateway.invoke(
                    run_id=run_id,
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    step_id=unit.step_id,
                    tool_name=unit.tool_name,
                    arguments=bound_arguments,
                    retry=agent.retry if agent else None,
                    circuit_breaker=agent.circuit_breaker if agent else None,
                )
                frame.define(_result_name(instruction), call_result)
                pointer += 1
                continue

            if opcode is OpCode.TOOL_RESULT:
                unit = instruction.args[0]
                if not isinstance(unit, ToolResultUnit):
                    raise AgentScriptRuntimeError("Invalid TOOL_RESULT payload in IR.")
                call_result = frame.get(unit.call_temp)
                if not isinstance(call_result, ToolResult):
                    raise AgentScriptRuntimeError("TOOL_RESULT expects a ToolResult input.")
                payload = await self._materialize_tool_result(
                    call_result,
                    frame,
                    agent,
                    run_id=run_id,
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                )
                frame.define(_result_name(instruction), payload)
                pointer += 1
                continue

            if opcode is OpCode.CALL_WORKFLOW:
                workflow_target = str(instruction.args[0])
                bound_arguments = self._resolve_bound_arguments(
                    workflow_target,
                    instruction.args[1],
                    frame,
                    is_workflow=True,
                )
                nested_workflow = self.program.workflow(workflow_target)
                self._record(
                    run_id,
                    "workflow_call",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={"target": workflow_target, "arguments": bound_arguments},
                )
                with self.telemetry.span(
                    "agentscript.workflow.call",
                    kind="internal",
                    attributes={
                        "gen_ai.agent.name": agent.name if agent else "default",
                        "gen_ai.operation.name": "workflow_call",
                        "gen_ai.provider.name": "agentscript",
                        "gen_ai.conversation.id": run_id or "local-run",
                        "agentscript.workflow.name": workflow_target,
                    },
                ) as span:
                    try:
                        result = await self._execute_workflow(
                            nested_workflow,
                            bound_arguments,
                            parent=frame,
                            agent=agent,
                            run_id=run_id,
                        )
                    except Exception as error:  # noqa: BLE001
                        self.telemetry.mark_error(span, error)
                        raise
                frame.define(_result_name(instruction), result)
                pointer += 1
                continue

            if opcode is OpCode.COMPARE_OP:
                left = frame.get(str(instruction.args[1]))
                right = frame.get(str(instruction.args[2]))
                frame.define(
                    _result_name(instruction),
                    _compare(str(instruction.args[0]), left, right),
                )
                pointer += 1
                continue

            if opcode is OpCode.BINARY_OP:
                left = frame.get(str(instruction.args[1]))
                right = frame.get(str(instruction.args[2]))
                frame.define(
                    _result_name(instruction),
                    _binary_op(str(instruction.args[0]), left, right),
                )
                pointer += 1
                continue

            if opcode is OpCode.UNARY_OP:
                operand = frame.get(str(instruction.args[1]))
                frame.define(
                    _result_name(instruction),
                    _unary_op(str(instruction.args[0]), operand),
                )
                pointer += 1
                continue

            if opcode is OpCode.POP:
                frame.get(str(instruction.args[0]))
                pointer += 1
                continue

            if opcode is OpCode.JUMP_IF_FALSE:
                condition = frame.get(str(instruction.args[0]))
                taken = not bool(condition)
                self._record(
                    run_id,
                    "branch",
                    workflow_name=workflow.name,
                    instruction_index=pointer,
                    payload={
                        "condition": condition,
                        "taken": taken,
                        "target": instruction.args[1],
                    },
                )
                pointer = labels[str(instruction.args[1])] if taken else pointer + 1
                continue

            if opcode is OpCode.JUMP:
                pointer = labels[str(instruction.args[0])]
                continue

            if opcode is OpCode.LABEL:
                pointer += 1
                continue

            if opcode is OpCode.RETURN:
                result = frame.get(str(instruction.args[0]))
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

    async def _materialize_tool_result(
        self,
        call_result: ToolResult,
        environment: Environment,
        agent: AgentPolicy | None,
        *,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        if call_result.ok:
            return call_result.payload

        if agent and agent.fallback_steps:
            return await self._execute_fallback(
                failed_result=call_result,
                environment=environment,
                agent=agent,
                run_id=run_id,
                workflow_name=workflow_name,
                instruction_index=instruction_index,
            )

        attempts = call_result.retries + 1
        if call_result.source == "circuit-open":
            raise ToolInvocationError(
                f"Tool '{call_result.tool_name}' was rejected because its circuit is open."
            )
        raise ToolInvocationError(
            f"Tool '{call_result.tool_name}' failed after {attempts} attempt(s): {call_result.error}"
        )

    async def _execute_fallback(
        self,
        *,
        failed_result: ToolResult,
        environment: Environment,
        agent: AgentPolicy,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> object:
        self._record(
            run_id,
            "fallback_started",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={
                "tool_name": failed_result.tool_name,
                "reason": failed_result.source,
                "step_id": failed_result.step_id,
            },
        )
        final_result: ToolResult | None = None
        for step in agent.fallback_steps:
            final_result = await self._invoke_fallback_step(
                failed_result=failed_result,
                step=step,
                environment=environment,
                run_id=run_id,
                workflow_name=workflow_name,
                instruction_index=instruction_index,
            )
            if final_result.ok:
                environment.define(step.name, final_result.payload)

        if final_result is None:
            raise ToolInvocationError(
                f"Tool '{failed_result.tool_name}' failed and no fallback result was produced."
            )
        if not final_result.ok:
            raise ToolInvocationError(
                f"Fallback step '{final_result.tool_name}' failed: {final_result.error}"
            )
        return final_result.payload

    async def _invoke_fallback_step(
        self,
        *,
        failed_result: ToolResult,
        step: FallbackStep,
        environment: Environment,
        run_id: str | None,
        workflow_name: str,
        instruction_index: int,
    ) -> ToolResult:
        named_arguments = {
            argument.name: await self._evaluate_fallback_expression(argument.expression, environment)
            for argument in step.arguments
            if argument.name is not None
        }
        positional = [
            await self._evaluate_fallback_expression(argument.expression, environment)
            for argument in step.arguments
            if argument.name is None
        ]
        if positional:
            raise AgentScriptRuntimeError(
                f"Fallback step '{step.name}' currently requires named arguments or no arguments."
            )

        fallback_step_id = f"{failed_result.step_id}__fallback__{step.name}"
        self._record(
            run_id,
            "fallback_step",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={
                "step_name": step.name,
                "tool_name": step.tool_name,
                "arguments": named_arguments,
                "step_id": fallback_step_id,
            },
        )
        result = await self.gateway.invoke(
            run_id=run_id,
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            step_id=fallback_step_id,
            tool_name=step.tool_name,
            arguments=named_arguments,
            retry=None,
            circuit_breaker=None,
        )
        self._record(
            run_id,
            "fallback_result",
            workflow_name=workflow_name,
            instruction_index=instruction_index,
            payload={
                "step_name": step.name,
                "tool_name": step.tool_name,
                "ok": result.ok,
                "source": result.source,
            },
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

    def _resolve_bound_arguments(
        self,
        callable_name: str,
        bindings: tuple[BoundArgument, ...],
        environment: Environment,
        *,
        is_workflow: bool,
    ) -> dict[str, object]:
        signature = (
            self.program.semantic_model.workflows[callable_name]
            if is_workflow
            else self.program.semantic_model.tools[callable_name]
        )
        positional = [
            environment.get(binding.source_temp)
            for binding in bindings
            if binding.name is None
        ]
        named = {
            binding.name: environment.get(binding.source_temp)
            for binding in bindings
            if binding.name is not None
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
            unknown = ", ".join(sorted(str(name) for name in named))
            raise AgentScriptRuntimeError(
                f"Unknown bound argument(s) for '{callable_name}': {unknown}."
            )
        return bound

    def _resolve_clock(
        self,
        *,
        clock: Clock | None,
        sleep: Callable[[float], Awaitable[None]] | None,
        replay_source: ReplaySource | None,
    ) -> Clock:
        if clock is not None:
            return clock
        if replay_source is not None:
            return ReplayClock(replay_source.timestamps)
        if sleep is not None:
            return FunctionalClock(sleep_fn=sleep)
        return SystemClock()

    def _start_run(
        self,
        workflow_name: str,
        *,
        agent_name: str | None,
        arguments: dict[str, object],
    ) -> str | None:
        if self.trace_recorder is None:
            return None
        run_id = self.trace_recorder.start_run(
            workflow_name,
            agent_name=agent_name,
            arguments=arguments,
        )
        if self.replay_source is not None:
            self.trace_recorder.record(
                run_id,
                "replay_loaded",
                workflow_name=workflow_name,
                payload={"source_run_id": self.replay_source.run_id},
            )
        return run_id

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
        workflow_name: str,
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


def _result_name(instruction: IRInstruction) -> str:
    if instruction.result is None:
        raise AgentScriptRuntimeError(
            f"Instruction '{instruction.opcode.value}' is missing a result target."
        )
    return instruction.result


def _build_label_table(instructions: tuple[IRInstruction, ...]) -> dict[str, int]:
    return {
        str(instruction.args[0]): index
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
    clock: Clock | None = None,
    replay_source: ReplaySource | None = None,
    telemetry: RuntimeTelemetry | None = None,
) -> object:
    """Convenience helper for running one workflow."""

    interpreter = AsyncInterpreter(
        program,
        tools=tools,
        memory=memory,
        trace_recorder=trace_recorder,
        sleep=sleep,
        clock=clock,
        replay_source=replay_source,
        telemetry=telemetry,
    )
    return await interpreter.run_workflow(
        workflow_name,
        arguments=arguments,
        agent_name=agent_name,
    )
