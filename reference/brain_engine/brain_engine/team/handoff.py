"""Team mention + handoff value objects and storage.

Two cooperative primitives the V2 mobile UI relies on for cross-team
coordination:

- :class:`Mention` — a non-blocking nudge that another teammate is
  named in the current thread (``@cleaner Ayşe please confirm``).
  Mentions never transfer ownership; they only notify.
- :class:`Handoff` — an explicit transfer of a conversation /
  decision-card from one team member to another.  Carries a
  lifecycle (``PENDING`` → ``ACCEPTED`` / ``DECLINED`` /
  ``CANCELLED``) so the UI can show the receiving member a
  pending Inbox entry until they act.

Storage follows the Protocol + InMemory pattern used by the
blocker, autonomy, and card stores.  A Postgres mirror can land
later without touching callers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable


__all__ = [
    "Handoff",
    "HandoffNotFoundError",
    "HandoffStatus",
    "HandoffStore",
    "InMemoryHandoffStore",
    "InMemoryMentionStore",
    "Mention",
    "MentionStore",
]


def _utcnow() -> datetime:
    """Return a tz-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mention:
    """A non-blocking nudge naming a teammate in a thread.

    Attributes:
        mention_id: Server-minted identifier.
        property_id: Property the mentioning thread is attached to.
        thread_id: Conversation / decision-card thread identifier.
        author_id: ``TeamMember.member_id`` of the author.
        target_id: ``TeamMember.member_id`` being notified.
        note: Optional one-line context shown in the Inbox row.
        created_at: When the mention was emitted.
    """

    mention_id: str
    property_id: str
    thread_id: str
    author_id: str
    target_id: str
    note: str = ""
    created_at: datetime = field(default_factory=_utcnow)


class MentionStore(Protocol):
    """Persistence Protocol for :class:`Mention` records."""

    async def save(self, mention: Mention) -> Mention:
        """Persist a mention and return the stored record."""
        ...

    async def list_for_target(
        self,
        target_id: str,
        *,
        limit: int = 100,
    ) -> list[Mention]:
        """Return the newest ``limit`` mentions for a teammate."""
        ...


class InMemoryMentionStore:
    """Reference :class:`MentionStore` backed by an in-process list."""

    def __init__(self) -> None:
        self._rows: list[Mention] = []

    async def save(self, mention: Mention) -> Mention:
        """Append a mention preserving insertion order."""
        self._rows.append(mention)
        return mention

    async def list_for_target(
        self,
        target_id: str,
        *,
        limit: int = 100,
    ) -> list[Mention]:
        """Return mentions for ``target_id`` newest-first."""
        matches = [m for m in self._rows if m.target_id == target_id]
        matches.sort(key=lambda m: m.created_at, reverse=True)
        return matches[:limit]


# ---------------------------------------------------------------------------
# Handoffs
# ---------------------------------------------------------------------------


class HandoffStatus(StrEnum):
    """Lifecycle states of a :class:`Handoff`."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Handoff:
    """An explicit transfer of a thread between team members.

    Attributes:
        handoff_id: Server-minted identifier.
        property_id: Property the thread is attached to.
        thread_id: Conversation / decision-card thread identifier.
        from_member_id: ``TeamMember.member_id`` initiating the
            transfer.
        to_member_id: ``TeamMember.member_id`` receiving ownership.
        reason: Optional one-line rationale shown to the receiver.
        status: Current lifecycle state.
        created_at: When the handoff was created.
        resolved_at: When ``status`` left ``PENDING``.
        resolution_note: Free-form note explaining the resolution.
    """

    handoff_id: str
    property_id: str
    thread_id: str
    from_member_id: str
    to_member_id: str
    reason: str = ""
    status: HandoffStatus = HandoffStatus.PENDING
    created_at: datetime = field(default_factory=_utcnow)
    resolved_at: datetime | None = None
    resolution_note: str | None = None


class HandoffNotFoundError(LookupError):
    """Raised when a handoff id has no matching record."""


@runtime_checkable
class HandoffStore(Protocol):
    """Persistence Protocol for :class:`Handoff` records."""

    async def save(self, handoff: Handoff) -> Handoff:
        """Persist a freshly-created handoff."""
        ...

    async def get(self, handoff_id: str) -> Handoff | None:
        """Return the handoff by id or ``None`` when absent."""
        ...

    async def list_for_property(
        self,
        property_id: str,
        *,
        status: HandoffStatus | None = None,
    ) -> list[Handoff]:
        """Return handoffs for a property, optionally filtered."""
        ...

    async def update_status(
        self,
        handoff_id: str,
        *,
        status: HandoffStatus,
        note: str | None = None,
    ) -> Handoff:
        """Transition a handoff to a new lifecycle state."""
        ...


class InMemoryHandoffStore:
    """Reference :class:`HandoffStore` backed by a dict."""

    def __init__(self) -> None:
        self._by_id: dict[str, Handoff] = {}

    async def save(self, handoff: Handoff) -> Handoff:
        """Persist ``handoff`` keyed by its id."""
        self._by_id[handoff.handoff_id] = handoff
        return handoff

    async def get(self, handoff_id: str) -> Handoff | None:
        """Return the handoff or ``None``."""
        return self._by_id.get(handoff_id)

    async def list_for_property(
        self,
        property_id: str,
        *,
        status: HandoffStatus | None = None,
    ) -> list[Handoff]:
        """Return handoffs for ``property_id`` newest-first."""
        rows = [
            h for h in self._by_id.values()
            if h.property_id == property_id
            and (status is None or h.status is status)
        ]
        rows.sort(key=lambda h: h.created_at, reverse=True)
        return rows

    async def update_status(
        self,
        handoff_id: str,
        *,
        status: HandoffStatus,
        note: str | None = None,
    ) -> Handoff:
        """Move ``handoff_id`` to ``status``.

        Raises :class:`HandoffNotFoundError` when the id is unknown.
        """
        existing = self._by_id.get(handoff_id)
        if existing is None:
            raise HandoffNotFoundError(handoff_id)
        resolved_at = (
            existing.resolved_at
            if status is HandoffStatus.PENDING
            else _utcnow()
        )
        updated = replace(
            existing,
            status=status,
            resolved_at=resolved_at,
            resolution_note=note or existing.resolution_note,
        )
        self._by_id[handoff_id] = updated
        return updated


def new_mention_id() -> str:
    """Mint a fresh mention id (hex UUID)."""
    return uuid.uuid4().hex


def new_handoff_id() -> str:
    """Mint a fresh handoff id (hex UUID)."""
    return uuid.uuid4().hex
