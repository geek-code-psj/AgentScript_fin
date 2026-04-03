"""Lexer for AgentScript source files."""

from __future__ import annotations

from pathlib import Path

from agentscript.compiler.errors import LexError
from agentscript.compiler.tokens import KEYWORDS, Token, TokenType


class Lexer:
    """Converts AgentScript source text into a token stream."""

    def __init__(self, source: str, *, filename: str = "<memory>") -> None:
        self.source = source
        self.filename = filename
        self.index = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []

    def lex(self) -> list[Token]:
        while not self._is_at_end():
            self._scan_token()

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.column))
        return self.tokens

    def _scan_token(self) -> None:
        current = self._peek()

        if current in {" ", "\r", "\t"}:
            self._advance()
            return

        if current == "\n":
            self._advance()
            return

        if current == "#" or (current == "/" and self._peek_next() == "/"):
            self._skip_comment()
            return

        start_line = self.line
        start_column = self.column
        char = self._advance()

        single_char_tokens = {
            "(": TokenType.LEFT_PAREN,
            ")": TokenType.RIGHT_PAREN,
            "{": TokenType.LEFT_BRACE,
            "}": TokenType.RIGHT_BRACE,
            "[": TokenType.LEFT_BRACKET,
            "]": TokenType.RIGHT_BRACKET,
            ":": TokenType.COLON,
            ",": TokenType.COMMA,
            ".": TokenType.DOT,
            ";": TokenType.SEMICOLON,
            "+": TokenType.PLUS,
            "*": TokenType.STAR,
            "/": TokenType.SLASH,
        }

        if char in single_char_tokens:
            self._add_token(single_char_tokens[char], char, start_line, start_column)
            return

        if char == "-":
            token_type = TokenType.ARROW if self._match(">") else TokenType.MINUS
            lexeme = "->" if token_type is TokenType.ARROW else "-"
            self._add_token(token_type, lexeme, start_line, start_column)
            return

        if char == "=":
            if self._match(">"):
                self._add_token(TokenType.FAT_ARROW, "=>", start_line, start_column)
            elif self._match("="):
                self._add_token(TokenType.EQUAL, "==", start_line, start_column)
            else:
                self._add_token(TokenType.ASSIGN, "=", start_line, start_column)
            return

        if char == "!":
            if self._match("="):
                self._add_token(TokenType.NOT_EQUAL, "!=", start_line, start_column)
                return
            raise LexError(
                "Unexpected character '!'",
                line=start_line,
                column=start_column,
                hint="Did you mean '!='?",
            )

        if char == "<":
            token_type = TokenType.LESS_EQUAL if self._match("=") else TokenType.LESS
            lexeme = "<=" if token_type is TokenType.LESS_EQUAL else "<"
            self._add_token(token_type, lexeme, start_line, start_column)
            return

        if char == ">":
            token_type = (
                TokenType.GREATER_EQUAL if self._match("=") else TokenType.GREATER
            )
            lexeme = ">=" if token_type is TokenType.GREATER_EQUAL else ">"
            self._add_token(token_type, lexeme, start_line, start_column)
            return

        if char == '"':
            self._string(start_line, start_column)
            return

        if char.isdigit():
            self._number(start_line, start_column, first=char)
            return

        if char.isalpha() or char == "_":
            self._identifier(start_line, start_column, first=char)
            return

        raise LexError(
            f"Unexpected character {char!r}",
            line=start_line,
            column=start_column,
            hint="Check for unsupported punctuation or an unterminated token.",
        )

    def _identifier(self, line: int, column: int, *, first: str) -> None:
        lexeme = [first]
        while not self._is_at_end() and (self._peek().isalnum() or self._peek() == "_"):
            lexeme.append(self._advance())

        text = "".join(lexeme)
        token_type = KEYWORDS.get(text, TokenType.IDENTIFIER)
        literal = None
        if token_type is TokenType.TRUE:
            literal = True
        elif token_type is TokenType.FALSE:
            literal = False
        elif token_type is TokenType.NULL:
            literal = None

        self._add_token(token_type, text, line, column, literal=literal)

    def _number(self, line: int, column: int, *, first: str) -> None:
        lexeme = [first]
        while not self._is_at_end() and self._peek().isdigit():
            lexeme.append(self._advance())

        token_type = TokenType.INTEGER
        if not self._is_at_end() and self._peek() == "." and self._peek_next().isdigit():
            token_type = TokenType.FLOAT
            lexeme.append(self._advance())
            while not self._is_at_end() and self._peek().isdigit():
                lexeme.append(self._advance())

        text = "".join(lexeme)
        literal = float(text) if token_type is TokenType.FLOAT else int(text)
        self._add_token(token_type, text, line, column, literal=literal)

    def _string(self, line: int, column: int) -> None:
        value: list[str] = []
        escaped = False

        while not self._is_at_end():
            char = self._advance()

            if escaped:
                escape_map = {
                    '"': '"',
                    "\\": "\\",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                }
                value.append(escape_map.get(char, char))
                escaped = False
                continue

            if char == "\\":
                escaped = True
                continue

            if char == '"':
                lexeme = self.source[self._offset_from(line, column) : self.index]
                self._add_token(
                    TokenType.STRING,
                    lexeme,
                    line,
                    column,
                    literal="".join(value),
                )
                return

            if char == "\n":
                raise LexError(
                    "Unterminated string literal",
                    line=line,
                    column=column,
                    hint="String literals must close before the end of the line.",
                )

            value.append(char)

        raise LexError(
            "Unterminated string literal",
            line=line,
            column=column,
            hint='Add a closing double quote (").',
        )

    def _skip_comment(self) -> None:
        if self._peek() == "#":
            self._advance()
        else:
            self._advance()
            self._advance()

        while not self._is_at_end() and self._peek() != "\n":
            self._advance()

    def _add_token(
        self,
        token_type: TokenType,
        lexeme: str,
        line: int,
        column: int,
        *,
        literal: object | None = None,
    ) -> None:
        self.tokens.append(Token(token_type, lexeme, line, column, literal=literal))

    def _is_at_end(self) -> bool:
        return self.index >= len(self.source)

    def _peek(self) -> str:
        if self._is_at_end():
            return "\0"
        return self.source[self.index]

    def _peek_next(self) -> str:
        if self.index + 1 >= len(self.source):
            return "\0"
        return self.source[self.index + 1]

    def _advance(self) -> str:
        char = self.source[self.index]
        self.index += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def _match(self, expected: str) -> bool:
        if self._is_at_end() or self.source[self.index] != expected:
            return False
        self._advance()
        return True

    def _offset_from(self, line: int, column: int) -> int:
        current_line = 1
        current_column = 1
        for offset, char in enumerate(self.source):
            if current_line == line and current_column == column:
                return offset
            if char == "\n":
                current_line += 1
                current_column = 1
            else:
                current_column += 1
        return len(self.source)


def lex_source(source: str, *, filename: str = "<memory>") -> list[Token]:
    """Tokenize a source string."""

    return Lexer(source, filename=filename).lex()


def lex_file(path: str | Path) -> list[Token]:
    """Tokenize a source file."""

    source_path = Path(path)
    return lex_source(source_path.read_text(encoding="utf-8"), filename=str(source_path))
