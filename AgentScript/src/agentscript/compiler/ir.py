"""IR lowering for AgentScript."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from agentscript.compiler.ast import (
    BinaryExpression,
    CallExpression,
    Expression,
    ExpressionStatement,
    IdentifierExpression,
    IfStatement,
    LetStatement,
    LiteralExpression,
    MemberExpression,
    Program,
    ReturnStatement,
    SourceSpan,
    Statement,
    StepStatement,
    UnaryExpression,
    WorkflowDeclaration,
)
from agentscript.compiler.errors import IRLoweringError
from agentscript.compiler.parser import parse_file, parse_source
from agentscript.compiler.semantics import SemanticModel, analyze_program, is_builtin_tool


class OpCode(str, Enum):
    LOAD_CONST = "LOAD_CONST"
    LOAD_NAME = "LOAD_NAME"
    LOAD_ATTR = "LOAD_ATTR"
    STORE_NAME = "STORE_NAME"
    MEM_SET = "MEM_SET"
    MEM_SEARCH = "MEM_SEARCH"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    CALL_WORKFLOW = "CALL_WORKFLOW"
    COMPARE_OP = "COMPARE_OP"
    BINARY_OP = "BINARY_OP"
    UNARY_OP = "UNARY_OP"
    POP = "POP"
    JUMP_IF_FALSE = "JUMP_IF_FALSE"
    JUMP = "JUMP"
    LABEL = "LABEL"
    RETURN = "RETURN"


@dataclass(frozen=True, slots=True)
class BoundArgument:
    name: str | None
    source_temp: str


@dataclass(frozen=True, slots=True)
class ToolCallUnit:
    step_id: str
    tool_name: str
    arguments: tuple[BoundArgument, ...]
    origin: str


@dataclass(frozen=True, slots=True)
class ToolResultUnit:
    step_id: str
    call_temp: str


@dataclass(frozen=True, slots=True)
class MemorySetUnit:
    key: str
    value_temp: str
    semantic: bool = True


@dataclass(frozen=True, slots=True)
class IRInstruction:
    opcode: OpCode
    args: tuple[object, ...] = ()
    result: str | None = None


@dataclass(frozen=True, slots=True)
class IRWorkflow:
    name: str
    parameters: tuple[str, ...]
    return_type: str
    instructions: tuple[IRInstruction, ...]


@dataclass(frozen=True, slots=True)
class IRProgram:
    workflows: tuple[IRWorkflow, ...] = field(default_factory=tuple)


class IRLowerer:
    """Lowers validated AgentScript workflows into a flat IR."""

    def __init__(self, semantic_model: SemanticModel) -> None:
        self.semantic_model = semantic_model
        self._instructions: list[IRInstruction] = []
        self._temp_counter = 0
        self._label_counter = 0
        self._tool_call_counter = 0

    def lower(self, program: Program) -> IRProgram:
        workflows: list[IRWorkflow] = []
        for declaration in program.declarations:
            if isinstance(declaration, WorkflowDeclaration):
                workflows.append(self._lower_workflow(declaration))
        return IRProgram(workflows=tuple(workflows))

    def _lower_workflow(self, declaration: WorkflowDeclaration) -> IRWorkflow:
        self._instructions = []
        self._temp_counter = 0
        self._label_counter = 0
        self._tool_call_counter = 0
        signature = self.semantic_model.workflows[declaration.name]

        for statement in declaration.body:
            self._lower_statement(statement)

        workflow = IRWorkflow(
            name=declaration.name,
            parameters=tuple(parameter.name for parameter in declaration.parameters),
            return_type=signature.return_type.display(),
            instructions=tuple(self._instructions),
        )
        return eliminate_dead_code(workflow)

    def _lower_statement(self, statement: Statement) -> None:
        if isinstance(statement, LetStatement):
            value_temp = self._lower_expression(statement.value)
            self._emit(OpCode.STORE_NAME, statement.name, value_temp)
            self._emit(
                OpCode.MEM_SET,
                MemorySetUnit(key=statement.name, value_temp=value_temp, semantic=True),
            )
            return

        if isinstance(statement, ReturnStatement):
            value_temp = self._lower_expression(statement.value)
            self._emit(OpCode.RETURN, value_temp)
            return

        if isinstance(statement, ExpressionStatement):
            value_temp = self._lower_expression(statement.expression)
            self._emit(OpCode.POP, value_temp)
            return

        if isinstance(statement, StepStatement):
            bindings = tuple(
                BoundArgument(name=argument.name, source_temp=self._lower_expression(argument.value))
                for argument in statement.arguments
            )
            call_temp = self._new_temp()
            result_temp = self._new_temp()
            step_id = statement.name
            self._emit(
                OpCode.TOOL_CALL,
                ToolCallUnit(
                    step_id=step_id,
                    tool_name=statement.tool_name,
                    arguments=bindings,
                    origin="step",
                ),
                result=call_temp,
            )
            self._emit(
                OpCode.TOOL_RESULT,
                ToolResultUnit(step_id=step_id, call_temp=call_temp),
                result=result_temp,
            )
            self._emit(OpCode.STORE_NAME, statement.name, result_temp)
            self._emit(
                OpCode.MEM_SET,
                MemorySetUnit(key=statement.name, value_temp=result_temp, semantic=True),
            )
            return

        if isinstance(statement, IfStatement):
            self._lower_if(statement)
            return

        raise self._error(
            statement.span,
            "Unsupported statement encountered during IR lowering.",
            hint="Add a lowering rule for this statement kind.",
        )

    def _lower_if(self, statement: IfStatement) -> None:
        condition_temp = self._lower_expression(statement.condition)
        false_label = self._new_label("if_false")
        end_label = self._new_label("if_end")

        self._emit(OpCode.JUMP_IF_FALSE, condition_temp, false_label)
        for nested in statement.then_branch:
            self._lower_statement(nested)

        if statement.else_branch is not None:
            self._emit(OpCode.JUMP, end_label)
            self._emit(OpCode.LABEL, false_label)
            for nested in statement.else_branch:
                self._lower_statement(nested)
            self._emit(OpCode.LABEL, end_label)
            return

        self._emit(OpCode.LABEL, false_label)

    def _lower_expression(self, expression: Expression) -> str:
        if isinstance(expression, IdentifierExpression):
            temp = self._new_temp()
            self._emit(OpCode.LOAD_NAME, expression.name, result=temp)
            return temp

        if isinstance(expression, LiteralExpression):
            temp = self._new_temp()
            self._emit(OpCode.LOAD_CONST, expression.value, result=temp)
            return temp

        if isinstance(expression, MemberExpression):
            object_temp = self._lower_expression(expression.object)
            temp = self._new_temp()
            self._emit(OpCode.LOAD_ATTR, object_temp, expression.attribute, result=temp)
            return temp

        if isinstance(expression, UnaryExpression):
            operand_temp = self._lower_expression(expression.operand)
            temp = self._new_temp()
            self._emit(OpCode.UNARY_OP, expression.operator, operand_temp, result=temp)
            return temp

        if isinstance(expression, BinaryExpression):
            left_temp = self._lower_expression(expression.left)
            right_temp = self._lower_expression(expression.right)
            temp = self._new_temp()
            opcode = (
                OpCode.COMPARE_OP
                if expression.operator in {"<", "<=", ">", ">=", "==", "!="}
                else OpCode.BINARY_OP
            )
            self._emit(opcode, expression.operator, left_temp, right_temp, result=temp)
            return temp

        if isinstance(expression, CallExpression):
            if not isinstance(expression.callee, IdentifierExpression):
                raise self._error(
                    expression.span,
                    "Only direct callable names can be lowered right now.",
                    hint="Call tools or workflows by their declared names.",
                )

            callee_name = expression.callee.name
            if callee_name == "mem_search":
                if len(expression.arguments) != 1:
                    raise self._error(
                        expression.span,
                        "mem_search(...) currently expects exactly one argument.",
                        hint='Try: mem_search("query text")',
                    )
                query_temp = self._lower_expression(expression.arguments[0].value)
                temp = self._new_temp()
                self._emit(OpCode.MEM_SEARCH, query_temp, result=temp)
                return temp

            bindings = tuple(
                BoundArgument(name=argument.name, source_temp=self._lower_expression(argument.value))
                for argument in expression.arguments
            )

            if callee_name in self.semantic_model.workflows:
                temp = self._new_temp()
                self._emit(OpCode.CALL_WORKFLOW, callee_name, bindings, result=temp)
                return temp

            if callee_name in self.semantic_model.tools or is_builtin_tool(callee_name):
                call_temp = self._new_temp()
                payload_temp = self._new_temp()
                step_id = self._new_tool_step_id(callee_name)
                self._emit(
                    OpCode.TOOL_CALL,
                    ToolCallUnit(
                        step_id=step_id,
                        tool_name=callee_name,
                        arguments=bindings,
                        origin="expression",
                    ),
                    result=call_temp,
                )
                self._emit(
                    OpCode.TOOL_RESULT,
                    ToolResultUnit(step_id=step_id, call_temp=call_temp),
                    result=payload_temp,
                )
                return payload_temp

        raise self._error(
            expression.span,
            "Unsupported expression encountered during IR lowering.",
            hint="Add a lowering rule for this expression kind.",
        )

    def _emit(self, opcode: OpCode, *args: object, result: str | None = None) -> None:
        self._instructions.append(IRInstruction(opcode=opcode, args=args, result=result))

    def _new_temp(self) -> str:
        temp = f"%t{self._temp_counter}"
        self._temp_counter += 1
        return temp

    def _new_label(self, prefix: str) -> str:
        label = f"{prefix}_{self._label_counter}"
        self._label_counter += 1
        return label

    def _new_tool_step_id(self, tool_name: str) -> str:
        step_id = f"{tool_name}_{self._tool_call_counter}"
        self._tool_call_counter += 1
        return step_id

    def _error(self, span: SourceSpan, message: str, *, hint: str) -> IRLoweringError:
        return IRLoweringError(message, line=span.line, column=span.column, hint=hint)


def eliminate_dead_code(workflow: IRWorkflow) -> IRWorkflow:
    """Remove instructions that are unreachable within one workflow."""

    instructions = list(workflow.instructions)
    if not instructions:
        return workflow

    label_to_index = {
        instruction.args[0]: index
        for index, instruction in enumerate(instructions)
        if instruction.opcode is OpCode.LABEL
    }

    reachable: set[int] = set()
    stack = [0]

    while stack:
        index = stack.pop()
        if index in reachable or index < 0 or index >= len(instructions):
            continue

        reachable.add(index)
        instruction = instructions[index]

        if instruction.opcode is OpCode.RETURN:
            continue

        if instruction.opcode is OpCode.JUMP:
            stack.append(label_to_index[instruction.args[0]])
            continue

        if instruction.opcode is OpCode.JUMP_IF_FALSE:
            stack.append(index + 1)
            stack.append(label_to_index[instruction.args[1]])
            continue

        stack.append(index + 1)

    filtered = tuple(
        instruction
        for index, instruction in enumerate(instructions)
        if index in reachable
    )
    return IRWorkflow(
        name=workflow.name,
        parameters=workflow.parameters,
        return_type=workflow.return_type,
        instructions=filtered,
    )


def format_ir(program: IRProgram) -> str:
    """Render IR as text for debugging."""

    lines: list[str] = []
    for workflow in program.workflows:
        params = ", ".join(workflow.parameters)
        lines.append(f"workflow {workflow.name}({params}) -> {workflow.return_type}")
        for index, instruction in enumerate(workflow.instructions):
            arguments = ", ".join(_format_argument(argument) for argument in instruction.args)
            result = f" -> {instruction.result}" if instruction.result is not None else ""
            lines.append(f"  {index:03} {instruction.opcode.value:<14} {arguments}{result}")
    return "\n".join(lines)


def _format_argument(argument: object) -> str:
    if isinstance(argument, tuple):
        inner = ", ".join(_format_argument(value) for value in argument)
        return f"({inner})"
    return repr(argument)


def lower_program(program: Program, semantic_model: SemanticModel) -> IRProgram:
    """Lower a parsed, validated AgentScript program to IR."""

    return IRLowerer(semantic_model).lower(program)


def lower_source(
    source: str,
    *,
    filename: str = "<memory>",
) -> IRProgram:
    """Parse, analyze, and lower AgentScript source."""

    program = parse_source(source, filename=filename)
    semantic_model = analyze_program(program)
    return lower_program(program, semantic_model)


def lower_file(path: str | Path) -> IRProgram:
    """Parse, analyze, and lower an AgentScript file."""

    program = parse_file(path)
    semantic_model = analyze_program(program)
    return lower_program(program, semantic_model)
