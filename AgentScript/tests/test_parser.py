from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from agentscript.compiler.ast import (
    AgentDeclaration,
    BinaryExpression,
    CallExpression,
    CircuitBreakerPolicy,
    FallbackPolicy,
    IdentifierExpression,
    IfStatement,
    LetStatement,
    RetryPolicy,
    StepStatement,
    ToolDeclaration,
    WorkflowDeclaration,
)
from agentscript.compiler.errors import CompilerError, ParserError
from agentscript.compiler.parser import parse_source, parse_tokens
from agentscript.compiler.printer import format_ast
from agentscript.compiler.tokens import Token, TokenType


def test_parses_full_program_shape() -> None:
    source = """
    agent legal_researcher {
      retry(3, backoff=exponential)
      fallback {
        step degraded_summary using summarize_minimally(query, mode="fast")
      }
      circuit_breaker(threshold=0.50)
    }

    tool search_law(query: string) -> list[Citation]
    workflow legal_brief(query: string) -> Claim {
      let sources: list[Citation] = search_law(query)
      return sources
    }
    """
    program = parse_source(source)

    assert len(program.declarations) == 3
    agent = program.declarations[0]
    tool = program.declarations[1]
    workflow = program.declarations[2]

    assert isinstance(agent, AgentDeclaration)
    assert isinstance(tool, ToolDeclaration)
    assert isinstance(workflow, WorkflowDeclaration)

    assert isinstance(agent.body[0], RetryPolicy)
    assert isinstance(agent.body[1], FallbackPolicy)
    assert isinstance(agent.body[2], CircuitBreakerPolicy)

    fallback_step = agent.body[1].body[0]
    assert isinstance(fallback_step, StepStatement)
    assert fallback_step.tool_name == "summarize_minimally"
    assert fallback_step.arguments[1].name == "mode"

    let_statement = workflow.body[0]
    assert isinstance(let_statement, LetStatement)
    assert let_statement.type_ref.name == "list"
    assert let_statement.type_ref.arguments[0].name == "Citation"
    assert isinstance(let_statement.value, CallExpression)


def test_parses_if_statement_and_binary_condition() -> None:
    source = """
    workflow inspect(confidence: float) -> Claim {
      if confidence < 0.8 {
        return degraded
      } else {
        return approved
      }
    }
    """
    program = parse_source(source)
    workflow = program.declarations[0]
    assert isinstance(workflow, WorkflowDeclaration)

    statement = workflow.body[0]
    assert isinstance(statement, IfStatement)
    assert isinstance(statement.condition, BinaryExpression)
    assert statement.condition.operator == "<"
    assert isinstance(statement.condition.left, IdentifierExpression)
    assert statement.else_branch is not None


def test_ast_printer_contains_key_nodes() -> None:
    output = format_ast(parse_source("tool search_law(query: string) -> list[Citation]"))
    assert "Program @" in output
    assert "ToolDeclaration" in output
    assert "TypeRef" in output


def test_parser_error_reports_line_column_and_hint() -> None:
    source = "workflow legal_brief(query: string -> Claim {}"
    with pytest.raises(ParserError) as exc_info:
        parse_source(source)

    message = str(exc_info.value)
    assert "line 1, column 36" in message
    assert "Hint:" in message
    assert "Add ')'" in message


TOKEN_FIXTURES = [
    (TokenType.AGENT, "agent", None),
    (TokenType.WORKFLOW, "workflow", None),
    (TokenType.TOOL, "tool", None),
    (TokenType.RETRY, "retry", None),
    (TokenType.FALLBACK, "fallback", None),
    (TokenType.CIRCUIT_BREAKER, "circuit_breaker", None),
    (TokenType.LET, "let", None),
    (TokenType.STEP, "step", None),
    (TokenType.USING, "using", None),
    (TokenType.RETURN, "return", None),
    (TokenType.IF, "if", None),
    (TokenType.ELSE, "else", None),
    (TokenType.IDENTIFIER, "name", None),
    (TokenType.STRING, '"text"', "text"),
    (TokenType.INTEGER, "1", 1),
    (TokenType.FLOAT, "0.5", 0.5),
    (TokenType.TRUE, "true", True),
    (TokenType.FALSE, "false", False),
    (TokenType.NULL, "null", None),
    (TokenType.LEFT_PAREN, "(", None),
    (TokenType.RIGHT_PAREN, ")", None),
    (TokenType.LEFT_BRACE, "{", None),
    (TokenType.RIGHT_BRACE, "}", None),
    (TokenType.LEFT_BRACKET, "[", None),
    (TokenType.RIGHT_BRACKET, "]", None),
    (TokenType.COLON, ":", None),
    (TokenType.COMMA, ",", None),
    (TokenType.ASSIGN, "=", None),
    (TokenType.ARROW, "->", None),
    (TokenType.LESS, "<", None),
]


@settings(max_examples=75, deadline=None)
@given(st.lists(st.sampled_from(TOKEN_FIXTURES), max_size=15))
def test_parser_fuzz_token_streams_do_not_crash(
    token_specs: list[tuple[TokenType, str, object | None]],
) -> None:
    tokens = [
        Token(token_type, lexeme, 1, index + 1, literal)
        for index, (token_type, lexeme, literal) in enumerate(token_specs)
    ]
    tokens.append(Token(TokenType.EOF, "", 1, len(tokens) + 1))

    try:
        parse_tokens(tokens)
    except CompilerError:
        pass
