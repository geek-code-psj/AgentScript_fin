"""Clock abstractions for live execution and replay."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import Protocol


class Clock(Protocol):
    """Clock interface used by the runtime and replay engine."""

    def now(self) -> float:
        """Return the current timestamp in seconds."""

    async def sleep(self, seconds: float) -> None:
        """Suspend or virtualize time for the requested duration."""


@dataclass(slots=True)
class SystemClock:
    """Wall-clock implementation for live execution."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def now(self) -> float:
        return time.time()


@dataclass(slots=True)
class FunctionalClock:
    """Adapter that lets tests inject custom sleep and now functions."""

    sleep_fn: Callable[[float], Awaitable[None]]
    now_fn: Callable[[], float] = time.time

    async def sleep(self, seconds: float) -> None:
        await self.sleep_fn(seconds)

    def now(self) -> float:
        return float(self.now_fn())


@dataclass(slots=True)
class ReplayClock:
    """Virtual clock that reuses recorded timestamps during replay."""

    timestamps: tuple[float, ...]
    _index: int = 0
    _current: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.timestamps:
            self._current = self.timestamps[0]

    async def sleep(self, seconds: float) -> None:
        self._current += seconds

    def now(self) -> float:
        if self._index < len(self.timestamps):
            self._current = self.timestamps[self._index]
            self._index += 1
        return self._current
