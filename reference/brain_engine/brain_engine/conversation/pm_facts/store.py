"""Persistence Protocol for :class:`PmFact` rows."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol, runtime_checkable

from brain_engine.conversation.pm_facts.models import PmFact

__all__ = [
    "InMemoryPmFactStore",
    "PmFactStore",
]


@runtime_checkable
class PmFactStore(Protocol):
    """Persistence surface for PM-provided knowledge facts.

    Implementations must accept concurrent ``add_fact`` /
    ``list_facts`` calls — the live-chat path reads on every guest
    message while regenerate-pm-knowledge writes from the manager
    UI.  Ordering of :meth:`list_facts` is "newest first" so the
    most recent PM correction wins when an earlier answer is
    refined or replaced — the LLM weighs the top of the injected
    knowledge block, so freshest must come first.
    """

    async def add_fact(self, fact: PmFact) -> None:
        """Persist one PM-confirmed fact.

        Implementations should be idempotent on the natural key
        (``customer_id``, ``property_channel_id``, ``fact_text``)
        when the same correction is replayed — duplicates only
        bloat the prompt.
        """
        ...

    async def list_facts(
        self,
        *,
        customer_id: str,
        property_channel_id: str,
        as_of: datetime | None = None,
    ) -> list[PmFact]:
        """Return every fact in scope for one (customer, property).

        The returned list includes both property-scoped rows
        (``property_channel_id`` matches) and customer-wide rows
        (``property_channel_id == ""``) so the live-chat path can
        surface cross-property knowledge alongside listing-specific
        answers.

        Args:
            customer_id: Owning customer identifier.
            property_channel_id: Property scope.
            as_of: Optional cut-off — when provided, only facts with
                ``created_at <= as_of`` are returned.  Used by the
                temporal-recall endpoint to answer "what was the
                wifi password on April 28?" without exposing later
                corrections.  ``None`` (default) preserves the
                live-chat contract of returning every known fact.

        Returns:
            Newest-first list of facts visible at the requested
            point in time.  An ``as_of`` in the past therefore acts
            as a lossy snapshot of what the engine knew then.
        """
        ...

    async def clear_property(
        self,
        *,
        customer_id: str,
        property_channel_id: str,
    ) -> None:
        """Drop every fact for one (customer, property) pair.

        Used when a property is re-onboarded from scratch and the
        previous PM corrections are no longer authoritative.  Must
        leave customer-wide rows untouched.
        """
        ...


class InMemoryPmFactStore:
    """Dev / unit-test implementation of :class:`PmFactStore`.

    Backed by a plain list guarded by an :class:`asyncio.Lock` so
    concurrent live-chat reads cannot observe a half-written write.
    Production deployments should swap in :class:`PgPmFactStore`.
    """

    def __init__(self) -> None:
        self._rows: list[PmFact] = []
        self._lock = asyncio.Lock()

    async def add_fact(self, fact: PmFact) -> None:
        async with self._lock:
            for existing in self._rows:
                same_scope = (
                    existing.customer_id == fact.customer_id
                    and existing.property_channel_id
                    == fact.property_channel_id
                )
                if same_scope and existing.fact_text == fact.fact_text:
                    # Idempotent: the same correction replayed must
                    # not bloat the prompt.
                    return
            self._rows.append(fact)

    async def list_facts(
        self,
        *,
        customer_id: str,
        property_channel_id: str,
        as_of: datetime | None = None,
    ) -> list[PmFact]:
        rows = [
            row for row in self._rows
            if row.customer_id == customer_id
            and row.property_channel_id in (property_channel_id, "")
            and (as_of is None or row.created_at <= as_of)
        ]
        rows.sort(key=lambda row: row.created_at, reverse=True)
        return rows

    async def clear_property(
        self,
        *,
        customer_id: str,
        property_channel_id: str,
    ) -> None:
        async with self._lock:
            self._rows = [
                row for row in self._rows
                if not (
                    row.customer_id == customer_id
                    and row.property_channel_id == property_channel_id
                )
            ]
