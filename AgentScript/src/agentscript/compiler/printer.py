"""AST pretty printer for AgentScript."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any


def format_ast(node: Any) -> str:
    """Render an AST node as a readable tree."""

    lines: list[str] = []
    _render_value(node, lines, indent=0, label=None)
    return "\n".join(lines)


def _render_value(value: Any, lines: list[str], *, indent: int, label: str | None) -> None:
    prefix = "  " * indent

    if _is_ast_node(value):
        header = value.__class__.__name__
        span = getattr(value, "span", None)
        if span is not None:
            header = f"{header} @{span.line}:{span.column}"
        lines.append(f"{prefix}{f'{label}: ' if label else ''}{header}")
        for field in fields(value):
            if field.name == "span":
                continue
            _render_value(
                getattr(value, field.name),
                lines,
                indent=indent + 1,
                label=field.name,
            )
        return

    if isinstance(value, list):
        if not value:
            title = f"{label}: []" if label else "[]"
            lines.append(f"{prefix}{title}")
            return
        title = f"{label}:" if label else "items:"
        lines.append(f"{prefix}{title}")
        for item in value:
            _render_value(item, lines, indent=indent + 1, label="-")
        return

    lines.append(f"{prefix}{f'{label}: ' if label else ''}{value!r}")


def _is_ast_node(value: Any) -> bool:
    return is_dataclass(value) and value.__class__.__name__ != "SourceSpan"
