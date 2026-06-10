"""Persistence Protocol for :class:`UnansweredThread` rows."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from brain_engine.sandbox.models import UnansweredThread

__all__ = [
    "InMemoryUnansweredThreadStore",
    "UnansweredThreadStore",
]


@runtime_checkable
class UnansweredThreadStore(Protocol):
    """Persistence surface for sandbox rows."""

    async def put(self, thread: UnansweredThread) -> None:
        """Upsert a row keyed by ``conversation_id``."""
        ...

    async def get(self, conversation_id: str) -> UnansweredThread | None:
        """Return the row for one conversation, or ``None``."""
        ...

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[UnansweredThread]:
        """Return every sandbox row for ``property_id``.

        Ordered by ``last_guest_sent_at`` descending — the most
        recent unanswered thread is the one the UI surfaces first.
        """
        ...

    async def clear_property(self, property_id: str) -> None:
        """Delete every row for ``property_id``.

        Used before a fresh harvest so stale rows do not linger when
        the PM finally replies inside the PMS and the thread drops
        off the unanswered list.
        """
        ...


class InMemoryUnansweredThreadStore:
    """Dev / test implementation of :class:`UnansweredThreadStore`."""

    def __init__(self) -> None:
        self._data: dict[str, UnansweredThread] = {}
        self._lock = asyncio.Lock()

    async def put(self, thread: UnansweredThread) -> None:
        async with self._lock:
            self._data[thread.conversation_id] = thread

    async def get(self, conversation_id: str) -> UnansweredThread | None:
        return self._data.get(conversation_id)

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[UnansweredThread]:
        rows = [
            thread for thread in self._data.values()
            if thread.property_id == property_id
        ]
        rows.sort(key=lambda row: row.last_guest_sent_at, reverse=True)
        return rows

    async def clear_property(self, property_id: str) -> None:
        async with self._lock:
            stale = [
                conv_id for conv_id, thread in self._data.items()
                if thread.property_id == property_id
            ]
            for conv_id in stale:
                self._data.pop(conv_id, None)
