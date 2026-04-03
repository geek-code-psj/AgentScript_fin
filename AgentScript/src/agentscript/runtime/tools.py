"""Tool registry for AgentScript runtime integration."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agentscript.runtime.errors import ToolNotRegisteredError


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    name: str
    function: Callable[..., object]
    schema: dict[str, object]


class ToolRegistry:
    """Registry that exposes Python callables to AgentScript workflows."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def tool(self, name: str | None = None):
        """Decorator for registering a tool."""

        def decorator(function: Callable[..., object]) -> Callable[..., object]:
            self.register(function, name=name)
            return function

        return decorator

    def register(
        self,
        function: Callable[..., object],
        *,
        name: str | None = None,
    ) -> RegisteredTool:
        tool_name = name or function.__name__
        tool = RegisteredTool(
            name=tool_name,
            function=function,
            schema=_build_schema(tool_name, function),
        )
        self._tools[tool_name] = tool
        return tool

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise ToolNotRegisteredError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    async def invoke(self, name: str, **kwargs: object) -> object:
        tool = self.get(name)
        result = tool.function(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def schema(self, name: str) -> dict[str, object]:
        return self.get(name).schema


def _build_schema(name: str, function: Callable[..., object]) -> dict[str, object]:
    signature = inspect.signature(function)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for parameter in signature.parameters.values():
        properties[parameter.name] = {
            "type": _json_type_name(parameter.annotation),
        }
        if parameter.default is inspect._empty:
            required.append(parameter.name)

    return {
        "name": name,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "returns": _json_type_name(signature.return_annotation),
    }


def _json_type_name(annotation: object) -> str:
    if annotation in {str, "str"}:
        return "string"
    if annotation in {int, "int"}:
        return "integer"
    if annotation in {float, "float"}:
        return "number"
    if annotation in {bool, "bool"}:
        return "boolean"
    return "object"
