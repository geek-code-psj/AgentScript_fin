"""AST model for AgentScript."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SourceSpan:
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Program:
    span: SourceSpan
    declarations: list["Declaration"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TypeRef:
    span: SourceSpan
    name: str
    arguments: list["TypeRef"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Parameter:
    span: SourceSpan
    name: str
    type_ref: TypeRef


@dataclass(frozen=True, slots=True)
class ImportDeclaration:
    span: SourceSpan
    path: str


@dataclass(frozen=True, slots=True)
class TypeDeclaration:
    span: SourceSpan
    name: str
    target: TypeRef


@dataclass(frozen=True, slots=True)
class AgentDeclaration:
    span: SourceSpan
    name: str
    body: list["AgentItem"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    span: SourceSpan
    name: str
    parameters: list[Parameter] = field(default_factory=list)
    return_type: TypeRef | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDeclaration:
    span: SourceSpan
    name: str
    parameters: list[Parameter] = field(default_factory=list)
    return_type: TypeRef | None = None
    body: list["Statement"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    span: SourceSpan
    arguments: list["CallArgument"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    span: SourceSpan
    arguments: list["CallArgument"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    span: SourceSpan
    body: list["Statement"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LetStatement:
    span: SourceSpan
    name: str
    type_ref: TypeRef
    value: "Expression"


@dataclass(frozen=True, slots=True)
class ReturnStatement:
    span: SourceSpan
    value: "Expression"


@dataclass(frozen=True, slots=True)
class StepStatement:
    span: SourceSpan
    name: str
    tool_name: str
    arguments: list["CallArgument"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class IfStatement:
    span: SourceSpan
    condition: "Expression"
    then_branch: list["Statement"] = field(default_factory=list)
    else_branch: list["Statement"] | None = None


@dataclass(frozen=True, slots=True)
class ExpressionStatement:
    span: SourceSpan
    expression: "Expression"


@dataclass(frozen=True, slots=True)
class IdentifierExpression:
    span: SourceSpan
    name: str


@dataclass(frozen=True, slots=True)
class LiteralExpression:
    span: SourceSpan
    value: str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class MemberExpression:
    span: SourceSpan
    object: "Expression"
    attribute: str


@dataclass(frozen=True, slots=True)
class UnaryExpression:
    span: SourceSpan
    operator: str
    operand: "Expression"


@dataclass(frozen=True, slots=True)
class BinaryExpression:
    span: SourceSpan
    left: "Expression"
    operator: str
    right: "Expression"


@dataclass(frozen=True, slots=True)
class CallArgument:
    span: SourceSpan
    value: "Expression"
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CallExpression:
    span: SourceSpan
    callee: "Expression"
    arguments: list[CallArgument] = field(default_factory=list)


Declaration = (
    ImportDeclaration
    | TypeDeclaration
    | AgentDeclaration
    | ToolDeclaration
    | WorkflowDeclaration
)

AgentItem = RetryPolicy | CircuitBreakerPolicy | FallbackPolicy

Statement = (
    LetStatement
    | ReturnStatement
    | StepStatement
    | IfStatement
    | ExpressionStatement
)

Expression = (
    IdentifierExpression
    | LiteralExpression
    | MemberExpression
    | UnaryExpression
    | BinaryExpression
    | CallExpression
)
