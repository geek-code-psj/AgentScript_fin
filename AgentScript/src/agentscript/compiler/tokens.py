"""Token model for AgentScript."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    EOF = auto()
    IDENTIFIER = auto()
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()

    AGENT = auto()
    WORKFLOW = auto()
    TOOL = auto()
    TYPE = auto()
    LET = auto()
    STEP = auto()
    USING = auto()
    IF = auto()
    ELSE = auto()
    RETURN = auto()
    IMPORT = auto()
    RETRY = auto()
    FALLBACK = auto()
    CIRCUIT_BREAKER = auto()
    MEMORY = auto()
    EMITS = auto()
    TRUE = auto()
    FALSE = auto()
    NULL = auto()

    LEFT_PAREN = auto()
    RIGHT_PAREN = auto()
    LEFT_BRACE = auto()
    RIGHT_BRACE = auto()
    LEFT_BRACKET = auto()
    RIGHT_BRACKET = auto()
    COLON = auto()
    COMMA = auto()
    DOT = auto()
    SEMICOLON = auto()

    ASSIGN = auto()
    EQUAL = auto()
    NOT_EQUAL = auto()
    LESS = auto()
    LESS_EQUAL = auto()
    GREATER = auto()
    GREATER_EQUAL = auto()
    ARROW = auto()
    FAT_ARROW = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()


KEYWORDS = {
    "agent": TokenType.AGENT,
    "workflow": TokenType.WORKFLOW,
    "tool": TokenType.TOOL,
    "type": TokenType.TYPE,
    "let": TokenType.LET,
    "step": TokenType.STEP,
    "using": TokenType.USING,
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "return": TokenType.RETURN,
    "import": TokenType.IMPORT,
    "retry": TokenType.RETRY,
    "fallback": TokenType.FALLBACK,
    "circuit_breaker": TokenType.CIRCUIT_BREAKER,
    "memory": TokenType.MEMORY,
    "emits": TokenType.EMITS,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "null": TokenType.NULL,
}


@dataclass(frozen=True, slots=True)
class Token:
    type: TokenType
    lexeme: str
    line: int
    column: int
    literal: object | None = None
