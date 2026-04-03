"""Semantic analysis for AgentScript."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentscript.compiler.ast import (
    AgentDeclaration,
    BinaryExpression,
    CallArgument,
    CallExpression,
    CircuitBreakerPolicy,
    Expression,
    ExpressionStatement,
    FallbackPolicy,
    IdentifierExpression,
    IfStatement,
    LetStatement,
    LiteralExpression,
    MemberExpression,
    Parameter,
    Program,
    RetryPolicy,
    ReturnStatement,
    SourceSpan,
    Statement,
    StepStatement,
    ToolDeclaration,
    TypeDeclaration,
    TypeRef,
    UnaryExpression,
    WorkflowDeclaration,
)
from agentscript.compiler.errors import SemanticError
from agentscript.compiler.parser import parse_file, parse_source


@dataclass(frozen=True, slots=True)
class SemanticType:
    name: str
    arguments: tuple["SemanticType", ...] = ()

    def display(self) -> str:
        if not self.arguments:
            return self.name
        rendered = ", ".join(argument.display() for argument in self.arguments)
        return f"{self.name}[{rendered}]"

    def __str__(self) -> str:
        return self.display()


@dataclass(frozen=True, slots=True)
class TypeDefinition:
    name: str
    arity: int = 0
    fields: dict[str, SemanticType] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParameterInfo:
    name: str
    type: SemanticType
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class ToolSignature:
    name: str
    parameters: tuple[ParameterInfo, ...]
    return_type: SemanticType
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class WorkflowSignature:
    name: str
    parameters: tuple[ParameterInfo, ...]
    return_type: SemanticType
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class SemanticModel:
    type_aliases: dict[str, SemanticType]
    tools: dict[str, ToolSignature]
    workflows: dict[str, WorkflowSignature]


@dataclass(slots=True)
class Scope:
    parent: "Scope | None" = None
    symbols: dict[str, SemanticType] = field(default_factory=dict)

    def define(self, name: str, type_: SemanticType) -> None:
        self.symbols[name] = type_

    def resolve(self, name: str) -> SemanticType | None:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent is None:
            return None
        return self.parent.resolve(name)


STRING = SemanticType("string")
INT = SemanticType("int")
FLOAT = SemanticType("float")
BOOL = SemanticType("bool")
NULL = SemanticType("null")
CLAIM = SemanticType("Claim")
CITATION = SemanticType("Citation")
INTENT = SemanticType("Intent")
EMBEDDING = SemanticType("Embedding")
MEMORY_ENTRY = SemanticType("MemoryEntry")
LIST_FLOAT = SemanticType("list", (FLOAT,))

BUILTIN_TYPES = {
    "string": TypeDefinition("string"),
    "int": TypeDefinition("int"),
    "float": TypeDefinition("float"),
    "bool": TypeDefinition("bool"),
    "null": TypeDefinition("null"),
    "Claim": TypeDefinition(
        "Claim",
        fields={
            "confidence": FLOAT,
            "text": STRING,
        },
    ),
    "Citation": TypeDefinition(
        "Citation",
        fields={
            "source": STRING,
            "span": STRING,
            "url": STRING,
        },
    ),
    "Intent": TypeDefinition(
        "Intent",
        fields={
            "name": STRING,
            "score": FLOAT,
        },
    ),
    "Embedding": TypeDefinition(
        "Embedding",
        fields={
            "dim": INT,
            "vector": LIST_FLOAT,
        },
    ),
    "MemoryEntry": TypeDefinition(
        "MemoryEntry",
        fields={
            "key": STRING,
            "value": STRING,
            "score": FLOAT,
        },
    ),
    "list": TypeDefinition("list", arity=1),
}


BUILTIN_TOOL_SIGNATURES = {
    "mem_search": ToolSignature(
        name="mem_search",
        parameters=(
            ParameterInfo(
                name="query",
                type=STRING,
                span=SourceSpan(1, 1),
            ),
        ),
        return_type=SemanticType("list", (MEMORY_ENTRY,)),
        span=SourceSpan(1, 1),
    )
}


class SemanticAnalyzer:
    """Validates AgentScript programs after parsing."""

    def __init__(self) -> None:
        self._type_alias_nodes: dict[str, TypeDeclaration] = {}
        self._type_aliases: dict[str, SemanticType] = {}
        self._tools: dict[str, ToolSignature] = {}
        self._workflows: dict[str, WorkflowSignature] = {}

    def analyze(self, program: Program) -> SemanticModel:
        self._collect_declarations(program)
        self._resolve_type_aliases()
        self._resolve_callables(program)
        self._validate_agents(program)
        self._validate_workflows(program)
        return SemanticModel(
            type_aliases=dict(self._type_aliases),
            tools=dict(self._tools),
            workflows=dict(self._workflows),
        )

    def _collect_declarations(self, program: Program) -> None:
        for declaration in program.declarations:
            if isinstance(declaration, TypeDeclaration):
                if declaration.name in BUILTIN_TYPES or declaration.name in self._type_alias_nodes:
                    raise self._error(
                        declaration.span,
                        f"Type '{declaration.name}' is already defined.",
                        hint="Choose a unique type alias name.",
                    )
                self._type_alias_nodes[declaration.name] = declaration

    def _resolve_type_aliases(self) -> None:
        for name, declaration in self._type_alias_nodes.items():
            if name not in self._type_aliases:
                self._type_aliases[name] = self._resolve_type_ref(
                    declaration.target,
                    stack=(name,),
                )

    def _resolve_callables(self, program: Program) -> None:
        callable_names: dict[str, SourceSpan] = {}

        for declaration in program.declarations:
            if isinstance(declaration, ToolDeclaration):
                self._assert_callable_name_available(
                    declaration.name,
                    declaration.span,
                    callable_names,
                )
                self._tools[declaration.name] = self._build_tool_signature(declaration)
                callable_names[declaration.name] = declaration.span

            if isinstance(declaration, WorkflowDeclaration):
                self._assert_callable_name_available(
                    declaration.name,
                    declaration.span,
                    callable_names,
                )
                self._workflows[declaration.name] = self._build_workflow_signature(
                    declaration
                )
                callable_names[declaration.name] = declaration.span

    def _assert_callable_name_available(
        self,
        name: str,
        span: SourceSpan,
        known_names: dict[str, SourceSpan],
    ) -> None:
        if name in known_names:
            raise self._error(
                span,
                f"Callable '{name}' is already defined.",
                hint="Tool and workflow names share one namespace.",
            )

    def _build_tool_signature(self, declaration: ToolDeclaration) -> ToolSignature:
        parameters = self._resolve_parameters(declaration.parameters)
        return_type = self._resolve_type_ref(declaration.return_type)
        return ToolSignature(
            name=declaration.name,
            parameters=tuple(parameters),
            return_type=return_type,
            span=declaration.span,
        )

    def _build_workflow_signature(
        self,
        declaration: WorkflowDeclaration,
    ) -> WorkflowSignature:
        parameters = self._resolve_parameters(declaration.parameters)
        return_type = self._resolve_type_ref(declaration.return_type)
        return WorkflowSignature(
            name=declaration.name,
            parameters=tuple(parameters),
            return_type=return_type,
            span=declaration.span,
        )

    def _resolve_parameters(self, parameters: list[Parameter]) -> list[ParameterInfo]:
        resolved: list[ParameterInfo] = []
        seen_names: set[str] = set()

        for parameter in parameters:
            if parameter.name in seen_names:
                raise self._error(
                    parameter.span,
                    f"Parameter '{parameter.name}' is declared more than once.",
                    hint="Parameter names must be unique within one signature.",
                )
            seen_names.add(parameter.name)
            resolved.append(
                ParameterInfo(
                    name=parameter.name,
                    type=self._resolve_type_ref(parameter.type_ref),
                    span=parameter.span,
                )
            )
        return resolved

    def _resolve_type_ref(
        self,
        type_ref: TypeRef | None,
        *,
        stack: tuple[str, ...] = (),
    ) -> SemanticType:
        if type_ref is None:
            raise self._error(
                SourceSpan(1, 1),
                "Missing type annotation.",
                hint="Every tool, workflow, and binding needs an explicit type.",
            )

        if type_ref.name in BUILTIN_TYPES:
            definition = BUILTIN_TYPES[type_ref.name]
            if len(type_ref.arguments) != definition.arity:
                if definition.arity == 0 and type_ref.arguments:
                    raise self._error(
                        type_ref.span,
                        f"Type '{type_ref.name}' does not take type arguments.",
                        hint=f"Remove '[...]' from '{type_ref.name}'.",
                    )
                raise self._error(
                    type_ref.span,
                    f"Type '{type_ref.name}' expects {definition.arity} type argument(s).",
                    hint=f"Try '{type_ref.name}[...]'.",
                )
            return SemanticType(
                type_ref.name,
                tuple(self._resolve_type_ref(argument, stack=stack) for argument in type_ref.arguments),
            )

        if type_ref.name in stack:
            chain = " -> ".join((*stack, type_ref.name))
            raise self._error(
                type_ref.span,
                f"Cyclic type alias detected: {chain}.",
                hint="Break the cycle so aliases eventually resolve to built-in types.",
            )

        if type_ref.name in self._type_alias_nodes:
            if type_ref.arguments:
                raise self._error(
                    type_ref.span,
                    f"Type alias '{type_ref.name}' does not take type arguments.",
                    hint="Apply type arguments to the underlying generic type instead.",
                )
            alias_name = type_ref.name
            if alias_name not in self._type_aliases:
                self._type_aliases[alias_name] = self._resolve_type_ref(
                    self._type_alias_nodes[alias_name].target,
                    stack=(*stack, alias_name),
                )
            return self._type_aliases[alias_name]

        raise self._error(
            type_ref.span,
            f"Unknown type '{type_ref.name}'.",
            hint="Declare the type alias first or use a built-in AgentScript type.",
        )

    def _validate_agents(self, program: Program) -> None:
        for declaration in program.declarations:
            if not isinstance(declaration, AgentDeclaration):
                continue
            for item in declaration.body:
                if isinstance(item, RetryPolicy):
                    self._validate_retry_policy(item)
                elif isinstance(item, CircuitBreakerPolicy):
                    self._validate_circuit_breaker_policy(item)
                elif isinstance(item, FallbackPolicy):
                    self._validate_fallback_policy(item)

    def _validate_retry_policy(self, policy: RetryPolicy) -> None:
        if not policy.arguments:
            raise self._error(
                policy.span,
                "retry(...) requires at least one argument.",
                hint="Try: retry(3, backoff=exponential)",
            )

        first = policy.arguments[0].value
        if not isinstance(first, LiteralExpression) or not isinstance(first.value, int):
            raise self._error(
                policy.arguments[0].span,
                "retry(...) expects the first argument to be an integer literal.",
                hint="Try: retry(3, backoff=exponential)",
            )

        named = self._named_arguments(policy.arguments[1:], policy.span)
        if "backoff" in named and not isinstance(
            named["backoff"],
            (IdentifierExpression, LiteralExpression),
        ):
            raise self._error(
                named["backoff"].span,
                "retry backoff must be a name or string literal.",
                hint='Examples: backoff=exponential or backoff="linear"',
            )

    def _validate_circuit_breaker_policy(self, policy: CircuitBreakerPolicy) -> None:
        if not policy.arguments:
            raise self._error(
                policy.span,
                "circuit_breaker(...) requires a threshold.",
                hint="Try: circuit_breaker(threshold=0.50)",
            )

        threshold_arg = policy.arguments[0]
        named = self._named_arguments(policy.arguments, policy.span)
        if "threshold" in named:
            value = named["threshold"]
        else:
            value = threshold_arg.value

        if not isinstance(value, LiteralExpression) or not isinstance(value.value, (int, float)):
            raise self._error(
                threshold_arg.span,
                "circuit_breaker threshold must be a numeric literal.",
                hint="Try: circuit_breaker(threshold=0.50)",
            )

    def _validate_fallback_policy(self, policy: FallbackPolicy) -> None:
        for statement in policy.body:
            if not isinstance(statement, StepStatement):
                raise self._error(
                    statement.span,
                    "Fallback blocks currently support only step statements.",
                    hint="Use 'step ... using tool_name(...)' inside fallback blocks.",
                )
            if statement.tool_name not in self._tools:
                raise self._error(
                    statement.span,
                    f"Unknown tool '{statement.tool_name}' in fallback block.",
                    hint="Declare the tool before using it in an agent policy.",
                )
            self._ensure_unique_named_arguments(statement.arguments, statement.span)

    def _validate_workflows(self, program: Program) -> None:
        for declaration in program.declarations:
            if not isinstance(declaration, WorkflowDeclaration):
                continue

            signature = self._workflows[declaration.name]
            scope = Scope()
            for parameter in signature.parameters:
                scope.define(parameter.name, parameter.type)

            for statement in declaration.body:
                self._validate_statement(statement, scope, signature.return_type)

    def _validate_statement(
        self,
        statement: Statement,
        scope: Scope,
        expected_return_type: SemanticType,
    ) -> None:
        if isinstance(statement, LetStatement):
            value_type = self._infer_expression(statement.value, scope)
            declared_type = self._resolve_type_ref(statement.type_ref)
            if not self._is_assignable(declared_type, value_type):
                raise self._type_error(statement.value.span, declared_type, value_type)
            if statement.name in scope.symbols:
                raise self._error(
                    statement.span,
                    f"Variable '{statement.name}' is already defined in this scope.",
                    hint="Choose a new variable name or reuse the existing binding.",
                )
            scope.define(statement.name, declared_type)
            return

        if isinstance(statement, ReturnStatement):
            actual_type = self._infer_expression(statement.value, scope)
            if not self._is_assignable(expected_return_type, actual_type):
                raise self._type_error(statement.value.span, expected_return_type, actual_type)
            return

        if isinstance(statement, StepStatement):
            result_type = self._validate_step(statement, scope)
            if statement.name in scope.symbols:
                raise self._error(
                    statement.span,
                    f"Variable '{statement.name}' is already defined in this scope.",
                    hint="Choose a new step binding name or reuse the existing symbol.",
                )
            scope.define(statement.name, result_type)
            return

        if isinstance(statement, IfStatement):
            condition_type = self._infer_expression(statement.condition, scope)
            if condition_type != BOOL:
                raise self._type_error(statement.condition.span, BOOL, condition_type)
            then_scope = Scope(parent=scope)
            for nested in statement.then_branch:
                self._validate_statement(nested, then_scope, expected_return_type)
            if statement.else_branch is not None:
                else_scope = Scope(parent=scope)
                for nested in statement.else_branch:
                    self._validate_statement(nested, else_scope, expected_return_type)
            return

        if isinstance(statement, ExpressionStatement):
            self._infer_expression(statement.expression, scope)
            return

        raise self._error(
            statement.span,
            "Unsupported statement encountered during semantic analysis.",
            hint="Update the semantic analyzer to handle this statement kind.",
        )

    def _validate_step(self, statement: StepStatement, scope: Scope) -> SemanticType:
        signature = resolve_tool_signature(
            SemanticModel(
                type_aliases=dict(self._type_aliases),
                tools=dict(self._tools),
                workflows=dict(self._workflows),
            ),
            statement.tool_name,
        )
        if signature is None:
            raise self._error(
                statement.span,
                f"Unknown tool '{statement.tool_name}'.",
                hint="Declare the tool before using it in a workflow step.",
            )
        self._validate_call_arguments(statement.arguments, signature, scope, statement.span)
        return signature.return_type

    def _infer_expression(self, expression: Expression, scope: Scope) -> SemanticType:
        if isinstance(expression, IdentifierExpression):
            symbol = scope.resolve(expression.name)
            if symbol is not None:
                return symbol
            if expression.name in self._tools or expression.name in self._workflows:
                raise self._error(
                    expression.span,
                    f"Callable '{expression.name}' must be invoked, not used as a raw value.",
                    hint=f"Try '{expression.name}(...)'.",
                )
            raise self._error(
                expression.span,
                f"Unknown symbol '{expression.name}'.",
                hint="Define the symbol earlier in the workflow or add it as a parameter.",
            )

        if isinstance(expression, LiteralExpression):
            return self._literal_type(expression.value)

        if isinstance(expression, MemberExpression):
            object_type = self._infer_expression(expression.object, scope)
            fields = BUILTIN_TYPES.get(object_type.name, TypeDefinition(object_type.name)).fields
            if expression.attribute not in fields:
                raise self._error(
                    expression.span,
                    f"Type '{object_type.display()}' has no field '{expression.attribute}'.",
                    hint="Check the field name or use a type that exposes that attribute.",
                )
            return fields[expression.attribute]

        if isinstance(expression, UnaryExpression):
            operand_type = self._infer_expression(expression.operand, scope)
            if expression.operator == "-" and self._is_numeric(operand_type):
                return operand_type
            raise self._error(
                expression.span,
                f"Unsupported unary operator '{expression.operator}' for {operand_type.display()}.",
                hint="Unary '-' currently supports only int and float operands.",
            )

        if isinstance(expression, BinaryExpression):
            return self._infer_binary_expression(expression, scope)

        if isinstance(expression, CallExpression):
            if not isinstance(expression.callee, IdentifierExpression):
                raise self._error(
                    expression.span,
                    "Only direct callable names can be invoked right now.",
                    hint="Call tools or workflows by name, e.g. search_law(query).",
                )
            signature = self._resolve_callable_signature(expression.callee)
            self._validate_call_arguments(expression.arguments, signature, scope, expression.span)
            return signature.return_type

        raise self._error(
            expression.span,
            "Unsupported expression encountered during semantic analysis.",
            hint="Extend the analyzer to infer this expression kind.",
        )

    def _infer_binary_expression(
        self,
        expression: BinaryExpression,
        scope: Scope,
    ) -> SemanticType:
        left = self._infer_expression(expression.left, scope)
        right = self._infer_expression(expression.right, scope)
        operator = expression.operator

        if operator in {"<", "<=", ">", ">="}:
            if self._is_numeric(left) and self._is_numeric(right):
                return BOOL
            raise self._error(
                expression.span,
                f"Operator '{operator}' expects numeric operands, got {left.display()} and {right.display()}.",
                hint="Comparisons currently support int and float values.",
            )

        if operator in {"==", "!="}:
            if self._is_assignable(left, right) or self._is_assignable(right, left):
                return BOOL
            raise self._error(
                expression.span,
                f"Cannot compare {left.display()} with {right.display()}.",
                hint="Equality checks require compatible types.",
            )

        if operator == "+" and left == STRING and right == STRING:
            return STRING

        if operator in {"+", "-", "*", "/"} and self._is_numeric(left) and self._is_numeric(right):
            if operator == "/" or left == FLOAT or right == FLOAT:
                return FLOAT
            return INT

        raise self._error(
            expression.span,
            f"Operator '{operator}' is not supported for {left.display()} and {right.display()}.",
            hint="Arithmetic supports numeric types, and '+' also supports string concatenation.",
        )

    def _resolve_callable_signature(
        self,
        callee: IdentifierExpression,
    ) -> ToolSignature | WorkflowSignature:
        if callee.name in self._tools:
            return self._tools[callee.name]
        if callee.name in BUILTIN_TOOL_SIGNATURES:
            return BUILTIN_TOOL_SIGNATURES[callee.name]
        if callee.name in self._workflows:
            return self._workflows[callee.name]
        raise self._error(
            callee.span,
            f"Unknown callable '{callee.name}'.",
            hint="Declare the tool or workflow before calling it.",
        )

    def _validate_call_arguments(
        self,
        arguments: list[CallArgument],
        signature: ToolSignature | WorkflowSignature,
        scope: Scope,
        span: SourceSpan,
    ) -> None:
        self._ensure_unique_named_arguments(arguments, span)
        named_arguments = {argument.name: argument for argument in arguments if argument.name is not None}
        positional_arguments = [argument for argument in arguments if argument.name is None]

        if len(positional_arguments) > len(signature.parameters):
            raise self._error(
                span,
                f"Too many positional arguments for '{signature.name}'.",
                hint=f"'{signature.name}' expects {len(signature.parameters)} argument(s).",
            )

        bound: dict[str, CallArgument] = {}
        for parameter, argument in zip(signature.parameters, positional_arguments):
            bound[parameter.name] = argument

        for name, argument in named_arguments.items():
            if name not in {parameter.name for parameter in signature.parameters}:
                raise self._error(
                    argument.span,
                    f"Unknown argument '{name}' for '{signature.name}'.",
                    hint="Check the tool/workflow signature for valid parameter names.",
                )
            if name in bound:
                raise self._error(
                    argument.span,
                    f"Argument '{name}' is provided more than once.",
                    hint="Pass each parameter only once.",
                )
            bound[name] = argument

        for parameter in signature.parameters:
            if parameter.name not in bound:
                raise self._error(
                    span,
                    f"Missing argument '{parameter.name}' for '{signature.name}'.",
                    hint="Provide all required parameters.",
                )
            actual_type = self._infer_expression(bound[parameter.name].value, scope)
            if not self._is_assignable(parameter.type, actual_type):
                raise self._type_error(bound[parameter.name].value.span, parameter.type, actual_type)

    def _ensure_unique_named_arguments(
        self,
        arguments: list[CallArgument],
        span: SourceSpan,
    ) -> None:
        self._named_arguments(arguments, span)

    def _named_arguments(
        self,
        arguments: list[CallArgument],
        span: SourceSpan,
    ) -> dict[str, Expression]:
        names: dict[str, Expression] = {}
        for argument in arguments:
            if argument.name is None:
                continue
            if argument.name in names:
                raise self._error(
                    span,
                    f"Named argument '{argument.name}' appears more than once.",
                    hint="Each named argument may be specified only once.",
                )
            names[argument.name] = argument.value
        return names

    def _literal_type(self, value: object) -> SemanticType:
        if isinstance(value, bool):
            return BOOL
        if isinstance(value, int):
            return INT
        if isinstance(value, float):
            return FLOAT
        if isinstance(value, str):
            return STRING
        if value is None:
            return NULL
        raise self._error(
            SourceSpan(1, 1),
            f"Unsupported literal value {value!r}.",
            hint="Teach the semantic analyzer how to type this literal.",
        )

    def _is_numeric(self, type_: SemanticType) -> bool:
        return type_ in {INT, FLOAT}

    def _is_assignable(self, expected: SemanticType, actual: SemanticType) -> bool:
        if expected == actual:
            return True
        if expected == FLOAT and actual == INT:
            return True
        if expected.name == "list" and actual.name == "list" and len(expected.arguments) == 1 and len(actual.arguments) == 1:
            return self._is_assignable(expected.arguments[0], actual.arguments[0])
        return False

    def _type_error(
        self,
        span: SourceSpan,
        expected: SemanticType,
        actual: SemanticType,
    ) -> SemanticError:
        return self._error(
            span,
            f"TypeError: Expected {expected.display()}, got {actual.display()}",
            hint="Follow the declared AgentScript types or update the annotation.",
        )

    def _error(self, span: SourceSpan, message: str, *, hint: str) -> SemanticError:
        return SemanticError(message, line=span.line, column=span.column, hint=hint)


def analyze_program(program: Program) -> SemanticModel:
    """Analyze a parsed AgentScript program."""

    return SemanticAnalyzer().analyze(program)


def analyze_source(source: str, *, filename: str = "<memory>") -> SemanticModel:
    """Parse and analyze AgentScript source text."""

    return analyze_program(parse_source(source, filename=filename))


def analyze_file(path: str | Path) -> SemanticModel:
    """Parse and analyze an AgentScript source file."""

    return analyze_program(parse_file(path))


def format_semantic_model(model: SemanticModel) -> str:
    """Render semantic model information for debugging."""

    lines: list[str] = []
    if model.type_aliases:
        lines.append("type aliases")
        for name, type_ in sorted(model.type_aliases.items()):
            lines.append(f"  {name} = {type_.display()}")

    if model.tools:
        lines.append("tools")
        for signature in model.tools.values():
            params = ", ".join(
                f"{parameter.name}: {parameter.type.display()}"
                for parameter in signature.parameters
            )
            lines.append(
                f"  {signature.name}({params}) -> {signature.return_type.display()}"
            )

    if model.workflows:
        lines.append("workflows")
        for signature in model.workflows.values():
            params = ", ".join(
                f"{parameter.name}: {parameter.type.display()}"
                for parameter in signature.parameters
            )
            lines.append(
                f"  {signature.name}({params}) -> {signature.return_type.display()}"
            )

    return "\n".join(lines)


def resolve_tool_signature(
    model: SemanticModel,
    name: str,
) -> ToolSignature | None:
    """Resolve a user-defined or built-in tool signature."""

    if name in model.tools:
        return model.tools[name]
    if name in BUILTIN_TOOL_SIGNATURES:
        return BUILTIN_TOOL_SIGNATURES[name]
    return None


def is_builtin_tool(name: str) -> bool:
    """Return whether a tool name is implemented by the runtime itself."""

    return name in BUILTIN_TOOL_SIGNATURES
