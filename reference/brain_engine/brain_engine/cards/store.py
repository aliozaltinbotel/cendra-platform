"""Lifecycle storage for decision cards.

The :class:`DecisionCard` value object is immutable and carries no
identity — useful for composition, but the V2 UI also needs to know
*which proposed card the PM just confirmed*.  This module wraps
proposed cards in a :class:`StoredCard` (id + status + audit trail)
and offers a :class:`CardStore` Protocol with an in-memory reference
implementation.

Storage is intentionally minimal — Postgres backing can be added the
same way :mod:`brain_engine.interview.postgres_store` mirrors the
in-memory contract — but the in-memory store is the canonical
specification.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable

from brain_engine.cards.models import DecisionCard


__all__ = [
    "CardNotFoundError",
    "CardStatus",
    "CardStore",
    "InMemoryCardStore",
    "StoredCard",
]


class CardStatus(StrEnum):
    """Lifecycle state of a proposed decision card.

    - ``PENDING``: just proposed, awaiting PM action.
    - ``CONFIRMED``: PM accepted (or autopilot self-confirmed).
    - ``DISMISSED``: PM declined; the engine should not re-surface
      the same recommendation without new evidence.
    - ``EXPIRED``: the SEMI_AUTO hold window passed without action;
      treated as implicit confirmation by the action pipeline.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


def _utcnow() -> datetime:
    """Return a tz-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class StoredCard:
    """Persisted wrapper around a :class:`DecisionCard`.

    Attributes:
        card_id: Stable identifier minted at save time.
        card: The immutable card value object.
        status: Current lifecycle state.
        created_at: When the card was first stored.
        resolved_at: When ``status`` left ``PENDING`` (``None`` while
            the card is still pending).
        resolved_by: Identifier of who confirmed/dismissed the card.
        resolution_note: Free-form note explaining the resolution.
    """

    card_id: str
    card: DecisionCard
    status: CardStatus = CardStatus.PENDING
    created_at: datetime = field(default_factory=_utcnow)
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None


class CardNotFoundError(LookupError):
    """Raised when a card id has no matching :class:`StoredCard`."""


@runtime_checkable
class CardStore(Protocol):
    """Persistence Protocol for proposed decision cards.

    Any class with these four async methods satisfies the contract —
    no inheritance required.
    """

    async def save(self, card: DecisionCard) -> StoredCard:
        """Persist a freshly-proposed card and return the wrapper."""
        ...

    async def get(self, card_id: str) -> StoredCard | None:
        """Return the stored card by id, or ``None`` when absent."""
        ...

    async def list_for_property(
        self,
        property_id: str,
        *,
        status: CardStatus | None = None,
    ) -> list[StoredCard]:
        """Return stored cards for a property, optionally filtered."""
        ...

    async def update_status(
        self,
        card_id: str,
        *,
        status: CardStatus,
        resolved_by: str | None = None,
        note: str | None = None,
    ) -> StoredCard:
        """Transition a stored card to a new lifecycle state."""
        ...


class InMemoryCardStore:
    """Reference :class:`CardStore` implementation backed by a dict.

    Suitable for tests, local development, and prod until a Postgres
    implementation lands.  All access is in-process; no concurrency
    primitives are needed because the store is awaited from a single
    asyncio event loop.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, StoredCard] = {}

    async def save(self, card: DecisionCard) -> StoredCard:
        """Persist ``card`` under a freshly-minted UUID."""
        card_id = uuid.uuid4().hex
        stored = StoredCard(card_id=card_id, card=card)
        self._by_id[card_id] = stored
        return stored

    async def get(self, card_id: str) -> StoredCard | None:
        """Return the stored card by id."""
        return self._by_id.get(card_id)

    async def list_for_property(
        self,
        property_id: str,
        *,
        status: CardStatus | None = None,
    ) -> list[StoredCard]:
        """Return stored cards for ``property_id`` newest-first."""
        rows = [
            stored
            for stored in self._by_id.values()
            if stored.card.property_id == property_id
            and (status is None or stored.status is status)
        ]
        rows.sort(key=lambda s: s.created_at, reverse=True)
        return rows

    async def update_status(
        self,
        card_id: str,
        *,
        status: CardStatus,
        resolved_by: str | None = None,
        note: str | None = None,
    ) -> StoredCard:
        """Transition a stored card to a new lifecycle state.

        Raises :class:`CardNotFoundError` when ``card_id`` is unknown.
        """
        existing = self._by_id.get(card_id)
        if existing is None:
            raise CardNotFoundError(card_id)
        resolved_at = (
            existing.resolved_at
            if status is CardStatus.PENDING
            else _utcnow()
        )
        updated = replace(
            existing,
            status=status,
            resolved_at=resolved_at,
            resolved_by=resolved_by or existing.resolved_by,
            resolution_note=note or existing.resolution_note,
        )
        self._by_id[card_id] = updated
        return updated
