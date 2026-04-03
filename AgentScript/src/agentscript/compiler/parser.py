"""Recursive-descent parser for AgentScript."""

from __future__ import annotations

from pathlib import Path

from agentscript.compiler.ast import (
    AgentDeclaration,
    BinaryExpression,
    CallArgument,
    CallExpression,
    CircuitBreakerPolicy,
    Declaration,
    Expression,
    ExpressionStatement,
    FallbackPolicy,
    IdentifierExpression,
    IfStatement,
    ImportDeclaration,
    LetStatement,
    LiteralExpression,
    MemberExpression,
    Parameter,
    Program,
    ReturnStatement,
    RetryPolicy,
    SourceSpan,
    Statement,
    StepStatement,
    ToolDeclaration,
    TypeDeclaration,
    TypeRef,
    UnaryExpression,
    WorkflowDeclaration,
)
from agentscript.compiler.errors import ParserError
from agentscript.compiler.lexer import lex_file, lex_source
from agentscript.compiler.tokens import Token, TokenType


class Parser:
    """Parses a token stream into an AgentScript AST."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def parse(self) -> Program:
        declarations: list[Declaration] = []
        start = self._span(self._peek())

        while not self._is_at_end():
            declarations.append(self._declaration())

        return Program(span=start, declarations=declarations)

    def _declaration(self) -> Declaration:
        if self._match(TokenType.IMPORT):
            return self._import_declaration(self._previous())
        if self._match(TokenType.TYPE):
            return self._type_declaration(self._previous())
        if self._match(TokenType.AGENT):
            return self._agent_declaration(self._previous())
        if self._match(TokenType.TOOL):
            return self._tool_declaration(self._previous())
        if self._match(TokenType.WORKFLOW):
            return self._workflow_declaration(self._previous())

        token = self._peek()
        raise self._error(
            token,
            f"Unexpected token {self._format_token(token)} at the top level.",
            hint=(
                "Top-level declarations must start with 'import', 'type', "
                "'agent', 'tool', or 'workflow'."
            ),
        )

    def _import_declaration(self, keyword: Token) -> ImportDeclaration:
        path = self._consume(
            TokenType.STRING,
            "Expected a string literal after 'import'.",
            hint='Try: import "stdlib.as"',
        )
        return ImportDeclaration(span=self._span(keyword), path=str(path.literal))

    def _type_declaration(self, keyword: Token) -> TypeDeclaration:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a type name after 'type'.",
            hint="Try: type SearchResults = list[Citation]",
        )
        self._consume(
            TokenType.ASSIGN,
            "Expected '=' in type declaration.",
            hint="Type aliases use the form 'type Name = ExistingType'.",
        )
        target = self._parse_type_ref()
        return TypeDeclaration(span=self._span(keyword), name=name.lexeme, target=target)

    def _agent_declaration(self, keyword: Token) -> AgentDeclaration:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected an agent name after 'agent'.",
            hint="Try: agent legal_researcher { ... }",
        )
        self._consume(
            TokenType.LEFT_BRACE,
            "Expected '{' to start the agent body.",
            hint="Add '{' after the agent name.",
        )

        body = []
        while not self._check(TokenType.RIGHT_BRACE) and not self._is_at_end():
            body.append(self._agent_item())

        self._consume(
            TokenType.RIGHT_BRACE,
            "Expected '}' to close the agent body.",
            hint="Add '}' after the last policy block.",
        )
        return AgentDeclaration(span=self._span(keyword), name=name.lexeme, body=body)

    def _agent_item(self):
        if self._match(TokenType.RETRY):
            keyword = self._previous()
            return RetryPolicy(
                span=self._span(keyword),
                arguments=self._parse_call_arguments_for(
                    "Expected '(' after 'retry'.",
                    "Add '(' after 'retry' to declare retry behavior.",
                ),
            )
        if self._match(TokenType.CIRCUIT_BREAKER):
            keyword = self._previous()
            return CircuitBreakerPolicy(
                span=self._span(keyword),
                arguments=self._parse_call_arguments_for(
                    "Expected '(' after 'circuit_breaker'.",
                    "Add '(' after 'circuit_breaker' to configure the threshold.",
                ),
            )
        if self._match(TokenType.FALLBACK):
            keyword = self._previous()
            body = self._parse_block(
                "Expected '{' to start the fallback block.",
                "Add '{' after 'fallback' to declare degraded behavior.",
            )
            return FallbackPolicy(span=self._span(keyword), body=body)

        token = self._peek()
        raise self._error(
            token,
            f"Unexpected token {self._format_token(token)} inside agent body.",
            hint="Valid agent items are 'retry', 'fallback', and 'circuit_breaker'.",
        )

    def _tool_declaration(self, keyword: Token) -> ToolDeclaration:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a tool name after 'tool'.",
            hint="Try: tool search_law(query: string) -> list[Citation]",
        )
        parameters = self._parse_parameters()
        return_type = self._parse_return_type()
        return ToolDeclaration(
            span=self._span(keyword),
            name=name.lexeme,
            parameters=parameters,
            return_type=return_type,
        )

    def _workflow_declaration(self, keyword: Token) -> WorkflowDeclaration:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a workflow name after 'workflow'.",
            hint="Try: workflow legal_brief(query: string) -> Claim { ... }",
        )
        parameters = self._parse_parameters()
        return_type = self._parse_return_type()
        body = self._parse_block(
            "Expected '{' to start the workflow body.",
            "Add '{' after the workflow signature.",
        )
        return WorkflowDeclaration(
            span=self._span(keyword),
            name=name.lexeme,
            parameters=parameters,
            return_type=return_type,
            body=body,
        )

    def _parse_parameters(self) -> list[Parameter]:
        self._consume(
            TokenType.LEFT_PAREN,
            "Expected '(' to start the parameter list.",
            hint="Function-like declarations use parentheses around parameters.",
        )
        parameters: list[Parameter] = []

        if not self._check(TokenType.RIGHT_PAREN):
            while True:
                parameters.append(self._parse_parameter())
                if not self._match(TokenType.COMMA):
                    break

        self._consume(
            TokenType.RIGHT_PAREN,
            "Expected ')' after parameter list.",
            hint=f"Add ')' before {self._format_token(self._peek())}.",
        )
        return parameters

    def _parse_parameter(self) -> Parameter:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a parameter name.",
            hint="Parameters use the form 'name: Type'.",
        )
        self._consume(
            TokenType.COLON,
            "Expected ':' after parameter name.",
            hint="Parameters use the form 'name: Type'.",
        )
        type_ref = self._parse_type_ref()
        return Parameter(span=self._span(name), name=name.lexeme, type_ref=type_ref)

    def _parse_return_type(self) -> TypeRef:
        self._consume(
            TokenType.ARROW,
            "Expected '->' before the return type.",
            hint="Tool and workflow signatures require an explicit return type.",
        )
        return self._parse_type_ref()

    def _parse_type_ref(self) -> TypeRef:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a type name.",
            hint="Examples: string, Claim, list[Citation]",
        )
        arguments: list[TypeRef] = []

        if self._match(TokenType.LEFT_BRACKET):
            while True:
                arguments.append(self._parse_type_ref())
                if not self._match(TokenType.COMMA):
                    break
            self._consume(
                TokenType.RIGHT_BRACKET,
                "Expected ']' after type arguments.",
                hint="Generic types use the form list[Citation].",
            )

        return TypeRef(span=self._span(name), name=name.lexeme, arguments=arguments)

    def _parse_block(self, message: str, hint: str) -> list[Statement]:
        self._consume(TokenType.LEFT_BRACE, message, hint=hint)
        statements: list[Statement] = []
        while not self._check(TokenType.RIGHT_BRACE) and not self._is_at_end():
            statements.append(self._statement())
        self._consume(
            TokenType.RIGHT_BRACE,
            "Expected '}' to close the block.",
            hint="Add '}' after the last statement in the block.",
        )
        return statements

    def _statement(self) -> Statement:
        if self._match(TokenType.LET):
            return self._let_statement(self._previous())
        if self._match(TokenType.RETURN):
            return self._return_statement(self._previous())
        if self._match(TokenType.STEP):
            return self._step_statement(self._previous())
        if self._match(TokenType.IF):
            return self._if_statement(self._previous())

        expression = self._expression()
        self._match(TokenType.SEMICOLON)
        return ExpressionStatement(span=expression.span, expression=expression)

    def _let_statement(self, keyword: Token) -> LetStatement:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a variable name after 'let'.",
            hint="Let bindings use the form 'let value: Type = expression'.",
        )
        self._consume(
            TokenType.COLON,
            "Expected ':' after variable name.",
            hint="Let bindings use the form 'let value: Type = expression'.",
        )
        type_ref = self._parse_type_ref()
        self._consume(
            TokenType.ASSIGN,
            "Expected '=' after type annotation.",
            hint="Let bindings assign with '='.",
        )
        value = self._expression()
        self._match(TokenType.SEMICOLON)
        return LetStatement(
            span=self._span(keyword),
            name=name.lexeme,
            type_ref=type_ref,
            value=value,
        )

    def _return_statement(self, keyword: Token) -> ReturnStatement:
        value = self._expression()
        self._match(TokenType.SEMICOLON)
        return ReturnStatement(span=self._span(keyword), value=value)

    def _step_statement(self, keyword: Token) -> StepStatement:
        name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a step name after 'step'.",
            hint="Try: step fetch_sources using search_law(query)",
        )
        self._consume(
            TokenType.USING,
            "Expected 'using' after step name.",
            hint="Step syntax is 'step name using tool_name(...)'.",
        )
        tool_name = self._consume(
            TokenType.IDENTIFIER,
            "Expected a tool name after 'using'.",
            hint="Step syntax is 'step name using tool_name(...)'.",
        )
        arguments = []
        if self._check(TokenType.LEFT_PAREN):
            arguments = self._parse_call_arguments_for(
                f"Expected '(' after tool name '{tool_name.lexeme}'.",
                "Call-style steps use parentheses for arguments.",
            )
        self._match(TokenType.SEMICOLON)
        return StepStatement(
            span=self._span(keyword),
            name=name.lexeme,
            tool_name=tool_name.lexeme,
            arguments=arguments,
        )

    def _if_statement(self, keyword: Token) -> IfStatement:
        condition = self._expression()
        then_branch = self._parse_block(
            "Expected '{' after if condition.",
            "Wrap the true branch in braces.",
        )
        else_branch = None
        if self._match(TokenType.ELSE):
            else_branch = self._parse_block(
                "Expected '{' after 'else'.",
                "Wrap the else branch in braces.",
            )
        return IfStatement(
            span=self._span(keyword),
            condition=condition,
            then_branch=then_branch,
            else_branch=else_branch,
        )

    def _expression(self) -> Expression:
        return self._equality()

    def _equality(self) -> Expression:
        expression = self._comparison()
        while self._match(TokenType.EQUAL, TokenType.NOT_EQUAL):
            operator = self._previous()
            right = self._comparison()
            expression = BinaryExpression(
                span=expression.span,
                left=expression,
                operator=operator.lexeme,
                right=right,
            )
        return expression

    def _comparison(self) -> Expression:
        expression = self._term()
        while self._match(
            TokenType.LESS,
            TokenType.LESS_EQUAL,
            TokenType.GREATER,
            TokenType.GREATER_EQUAL,
        ):
            operator = self._previous()
            right = self._term()
            expression = BinaryExpression(
                span=expression.span,
                left=expression,
                operator=operator.lexeme,
                right=right,
            )
        return expression

    def _term(self) -> Expression:
        expression = self._factor()
        while self._match(TokenType.PLUS, TokenType.MINUS):
            operator = self._previous()
            right = self._factor()
            expression = BinaryExpression(
                span=expression.span,
                left=expression,
                operator=operator.lexeme,
                right=right,
            )
        return expression

    def _factor(self) -> Expression:
        expression = self._unary()
        while self._match(TokenType.STAR, TokenType.SLASH):
            operator = self._previous()
            right = self._unary()
            expression = BinaryExpression(
                span=expression.span,
                left=expression,
                operator=operator.lexeme,
                right=right,
            )
        return expression

    def _unary(self) -> Expression:
        if self._match(TokenType.MINUS):
            operator = self._previous()
            operand = self._unary()
            return UnaryExpression(
                span=self._span(operator),
                operator=operator.lexeme,
                operand=operand,
            )
        return self._postfix()

    def _postfix(self) -> Expression:
        expression = self._primary()

        while True:
            if self._check(TokenType.LEFT_PAREN):
                arguments = self._parse_call_arguments_for(
                    "Expected '(' to start call arguments.",
                    "Call expressions use parentheses around arguments.",
                )
                expression = CallExpression(
                    span=expression.span,
                    callee=expression,
                    arguments=arguments,
                )
                continue

            if self._match(TokenType.DOT):
                attribute = self._consume(
                    TokenType.IDENTIFIER,
                    "Expected an attribute name after '.'.",
                    hint="Member access uses the form object.field.",
                )
                expression = MemberExpression(
                    span=expression.span,
                    object=expression,
                    attribute=attribute.lexeme,
                )
                continue

            return expression

    def _primary(self) -> Expression:
        if self._match(TokenType.IDENTIFIER):
            token = self._previous()
            return IdentifierExpression(span=self._span(token), name=token.lexeme)

        if self._match(TokenType.INTEGER, TokenType.FLOAT, TokenType.STRING):
            token = self._previous()
            return LiteralExpression(span=self._span(token), value=token.literal)

        if self._match(TokenType.TRUE, TokenType.FALSE, TokenType.NULL):
            token = self._previous()
            return LiteralExpression(span=self._span(token), value=token.literal)

        if self._match(TokenType.LEFT_PAREN):
            expression = self._expression()
            self._consume(
                TokenType.RIGHT_PAREN,
                "Expected ')' after grouped expression.",
                hint="Add ')' to close the expression.",
            )
            return expression

        token = self._peek()
        raise self._error(
            token,
            f"Expected an expression, but found {self._format_token(token)}.",
            hint="Expressions can be identifiers, literals, grouped expressions, or calls.",
        )

    def _parse_call_arguments_for(self, message: str, hint: str) -> list[CallArgument]:
        self._consume(TokenType.LEFT_PAREN, message, hint=hint)
        arguments: list[CallArgument] = []

        if not self._check(TokenType.RIGHT_PAREN):
            while True:
                arguments.append(self._call_argument())
                if not self._match(TokenType.COMMA):
                    break

        self._consume(
            TokenType.RIGHT_PAREN,
            "Expected ')' after argument list.",
            hint=f"Add ')' before {self._format_token(self._peek())}.",
        )
        return arguments

    def _call_argument(self) -> CallArgument:
        if self._check(TokenType.IDENTIFIER) and self._check_next(TokenType.ASSIGN):
            name = self._advance()
            self._advance()
            value = self._expression()
            return CallArgument(span=self._span(name), name=name.lexeme, value=value)

        value = self._expression()
        return CallArgument(span=value.span, value=value)

    def _consume(self, token_type: TokenType, message: str, *, hint: str) -> Token:
        if self._check(token_type):
            return self._advance()
        raise self._error(self._peek(), message, hint=hint)

    def _match(self, *token_types: TokenType) -> bool:
        for token_type in token_types:
            if self._check(token_type):
                self._advance()
                return True
        return False

    def _check(self, token_type: TokenType) -> bool:
        if self._is_at_end():
            return token_type is TokenType.EOF
        return self._peek().type is token_type

    def _check_next(self, token_type: TokenType) -> bool:
        if self.index + 1 >= len(self.tokens):
            return False
        return self.tokens[self.index + 1].type is token_type

    def _advance(self) -> Token:
        if not self._is_at_end():
            self.index += 1
        return self._previous()

    def _is_at_end(self) -> bool:
        return self._peek().type is TokenType.EOF

    def _peek(self) -> Token:
        return self.tokens[self.index]

    def _previous(self) -> Token:
        return self.tokens[self.index - 1]

    def _span(self, token: Token) -> SourceSpan:
        return SourceSpan(line=token.line, column=token.column)

    def _format_token(self, token: Token) -> str:
        if token.type is TokenType.EOF:
            return "end of input"
        if token.lexeme:
            return repr(token.lexeme)
        return token.type.name

    def _error(self, token: Token, message: str, *, hint: str) -> ParserError:
        return ParserError(message, line=token.line, column=token.column, hint=hint)


def parse_tokens(tokens: list[Token]) -> Program:
    """Parse a token stream into a program AST."""

    return Parser(tokens).parse()


def parse_source(source: str, *, filename: str = "<memory>") -> Program:
    """Parse an AgentScript source string."""

    return parse_tokens(lex_source(source, filename=filename))


def parse_file(path: str | Path) -> Program:
    """Parse an AgentScript source file."""

    return parse_tokens(lex_file(path))
