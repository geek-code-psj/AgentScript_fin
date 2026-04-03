"""Compiler error types."""

from __future__ import annotations


class CompilerError(Exception):
    """Base class for compiler-facing errors with source locations."""

    def __init__(
        self,
        message: str,
        *,
        line: int,
        column: int,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.hint = hint

    def __str__(self) -> str:
        location = f"line {self.line}, column {self.column}"
        if self.hint:
            return f"{self.message} at {location}. Hint: {self.hint}"
        return f"{self.message} at {location}"


class LexError(CompilerError):
    """Raised when the lexer encounters invalid input."""


class ParserError(CompilerError):
    """Raised when the parser encounters invalid syntax."""


class SemanticError(CompilerError):
    """Raised when semantic analysis finds invalid programs."""


class IRLoweringError(CompilerError):
    """Raised when AST-to-IR lowering fails."""
