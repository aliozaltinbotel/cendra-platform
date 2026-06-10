"""Persistence Protocol for :class:`InterviewAnswer` records.

The interview engine writes one :class:`InterviewAnswer` per (property,
question) and overwrites on re-answer.  A Postgres-backed implementation
will land alongside the other V2 stores once the engine has a consumer
that needs cross-restart durability; for now the in-memory backend is
enough to drive the API surface and tests.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from brain_engine.interview.models import InterviewAnswer


__all__ = [
    "InMemoryInterviewAnswerStore",
    "InterviewAnswerStore",
]


@runtime_checkable
class InterviewAnswerStore(Protocol):
    """Persistence surface for :class:`InterviewAnswer` records."""

    async def get(
        self,
        *,
        property_id: str,
        qid: str,
    ) -> InterviewAnswer | None:
        """Return the stored answer, or ``None`` when absent."""
        ...

    async def put(self, answer: InterviewAnswer) -> None:
        """Upsert an answer keyed by ``(property_id, qid)``."""
        ...

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[InterviewAnswer]:
        """Return every stored answer for a property."""
        ...


class InMemoryInterviewAnswerStore:
    """Dev / test implementation of :class:`InterviewAnswerStore`."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], InterviewAnswer] = {}

    async def get(
        self,
        *,
        property_id: str,
        qid: str,
    ) -> InterviewAnswer | None:
        return self._data.get((property_id, qid))

    async def put(self, answer: InterviewAnswer) -> None:
        self._data[(answer.property_id, answer.qid)] = answer

    async def list_for_property(
        self,
        property_id: str,
    ) -> list[InterviewAnswer]:
        return [
            answer for (pid, _), answer in self._data.items()
            if pid == property_id
        ]
