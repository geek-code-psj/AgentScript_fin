from __future__ import annotations

import pytest

from agentscript.compiler.errors import LexError
from agentscript.compiler.lexer import lex_source
from agentscript.compiler.tokens import TokenType


def token_types(source: str) -> list[TokenType]:
    return [token.type for token in lex_source(source)]


def test_lexes_workflow_signature() -> None:
    source = "workflow legal_brief(query: string) -> Claim {}"
    assert token_types(source) == [
        TokenType.WORKFLOW,
        TokenType.IDENTIFIER,
        TokenType.LEFT_PAREN,
        TokenType.IDENTIFIER,
        TokenType.COLON,
        TokenType.IDENTIFIER,
        TokenType.RIGHT_PAREN,
        TokenType.ARROW,
        TokenType.IDENTIFIER,
        TokenType.LEFT_BRACE,
        TokenType.RIGHT_BRACE,
        TokenType.EOF,
    ]


def test_lexes_fault_primitives() -> None:
    source = "retry(3, backoff=exponential) fallback { step x using y }"
    assert token_types(source) == [
        TokenType.RETRY,
        TokenType.LEFT_PAREN,
        TokenType.INTEGER,
        TokenType.COMMA,
        TokenType.IDENTIFIER,
        TokenType.ASSIGN,
        TokenType.IDENTIFIER,
        TokenType.RIGHT_PAREN,
        TokenType.FALLBACK,
        TokenType.LEFT_BRACE,
        TokenType.STEP,
        TokenType.IDENTIFIER,
        TokenType.USING,
        TokenType.IDENTIFIER,
        TokenType.RIGHT_BRACE,
        TokenType.EOF,
    ]


def test_lexes_llm_native_type_names_as_identifiers() -> None:
    tokens = lex_source("let claim: Claim = summarize_claim(results)")
    assert [token.type for token in tokens[:8]] == [
        TokenType.LET,
        TokenType.IDENTIFIER,
        TokenType.COLON,
        TokenType.IDENTIFIER,
        TokenType.ASSIGN,
        TokenType.IDENTIFIER,
        TokenType.LEFT_PAREN,
        TokenType.IDENTIFIER,
    ]
    assert tokens[3].lexeme == "Claim"


def test_lexes_list_type_annotation() -> None:
    source = "tool search_law(query: string) -> list[Citation]"
    assert token_types(source) == [
        TokenType.TOOL,
        TokenType.IDENTIFIER,
        TokenType.LEFT_PAREN,
        TokenType.IDENTIFIER,
        TokenType.COLON,
        TokenType.IDENTIFIER,
        TokenType.RIGHT_PAREN,
        TokenType.ARROW,
        TokenType.IDENTIFIER,
        TokenType.LEFT_BRACKET,
        TokenType.IDENTIFIER,
        TokenType.RIGHT_BRACKET,
        TokenType.EOF,
    ]


@pytest.mark.parametrize(
    ("source", "expected_literal"),
    [
        ('"hello"', "hello"),
        ('"line\\nfeed"', "line\nfeed"),
        ('"quote: \\""', 'quote: "'),
    ],
)
def test_lexes_string_literals(source: str, expected_literal: str) -> None:
    tokens = lex_source(source)
    assert tokens[0].type is TokenType.STRING
    assert tokens[0].literal == expected_literal


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_literal"),
    [
        ("42", TokenType.INTEGER, 42),
        ("0.50", TokenType.FLOAT, 0.50),
    ],
)
def test_lexes_numeric_literals(
    source: str,
    expected_type: TokenType,
    expected_literal: int | float,
) -> None:
    tokens = lex_source(source)
    assert tokens[0].type is expected_type
    assert tokens[0].literal == expected_literal


def test_lexes_boolean_and_null_keywords() -> None:
    tokens = lex_source("true false null")
    assert [token.type for token in tokens[:-1]] == [
        TokenType.TRUE,
        TokenType.FALSE,
        TokenType.NULL,
    ]
    assert tokens[0].literal is True
    assert tokens[1].literal is False
    assert tokens[2].literal is None


def test_skips_hash_comments() -> None:
    tokens = lex_source("# comment\nworkflow demo {}")
    assert [token.type for token in tokens[:4]] == [
        TokenType.WORKFLOW,
        TokenType.IDENTIFIER,
        TokenType.LEFT_BRACE,
        TokenType.RIGHT_BRACE,
    ]


def test_skips_double_slash_comments() -> None:
    tokens = lex_source("// comment\nagent test {}")
    assert [token.type for token in tokens[:4]] == [
        TokenType.AGENT,
        TokenType.IDENTIFIER,
        TokenType.LEFT_BRACE,
        TokenType.RIGHT_BRACE,
    ]


def test_tracks_line_and_column_positions() -> None:
    tokens = lex_source("agent demo {\n  retry(3)\n}")
    retry_token = next(token for token in tokens if token.type is TokenType.RETRY)
    assert (retry_token.line, retry_token.column) == (2, 3)


def test_errors_on_bare_bang() -> None:
    with pytest.raises(LexError, match="Did you mean '!='"):
        lex_source("!")


def test_errors_on_unterminated_string() -> None:
    with pytest.raises(LexError, match="closing double quote"):
        lex_source('"unterminated')


def test_errors_on_unknown_character() -> None:
    with pytest.raises(LexError, match="unsupported punctuation"):
        lex_source("@")
