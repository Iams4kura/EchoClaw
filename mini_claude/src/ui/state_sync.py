"""State to UI bindings - keeps display in sync with AppState."""

import asyncio
from typing import Callable, Awaitable, Optional

from ..models.state import AppState


class StateSync:
    """Bridges AppState changes to UI updates.

    Simple observer pattern: register a callback and it's called
    whenever state is updated through update().
    """

    def __init__(self, state: AppState):
        self._state = state
        self._lock = asyncio.Lock()
        self._on_change: Optional[Callable[[], Awaitable[None]]] = None

    @property
    def state(self) -> AppState:
        return self._state

    def on_change(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._on_change = callback

    async def update(self, updater: Callable[[AppState], None]) -> None:
        async with self._lock:
            updater(self._state)
            if self._on_change:
                await self._on_change()

    async def set_streaming(self, is_streaming: bool) -> None:
        async with self._lock:
            self._state.is_streaming = is_streaming
            if self._on_change:
                await self._on_change()

    async def append_to_buffer(self, text: str) -> None:
        async with self._lock:
            self._state.stream_buffer += text
            if self._on_change:
                await self._on_change()

    async def clear_buffer(self) -> None:
        async with self._lock:
            self._state.stream_buffer = ""
            if self._on_change:
                await self._on_change()
