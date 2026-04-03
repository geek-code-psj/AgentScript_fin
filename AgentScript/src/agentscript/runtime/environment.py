"""Environment chain for AgentScript runtime execution."""

from __future__ import annotations

from dataclasses import dataclass, field


_MISSING = object()


@dataclass(slots=True)
class Environment:
    """Nested environment used for workflow frames and temporaries."""

    parent: "Environment | None" = None
    values: dict[str, object] = field(default_factory=dict)

    def define(self, name: str, value: object) -> None:
        self.values[name] = value

    def get(self, name: str) -> object:
        value = self.resolve(name)
        if value is _MISSING:
            raise KeyError(name)
        return value

    def resolve(self, name: str) -> object:
        if name in self.values:
            return self.values[name]
        if self.parent is None:
            return _MISSING
        return self.parent.resolve(name)
