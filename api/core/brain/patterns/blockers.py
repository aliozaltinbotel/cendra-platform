"""Blocker domain models + engine (precondition gating).

A Blocker is a precondition that must be satisfied before a sensitive
action can proceed — "hold sensitive data until preconditions are met".
Blockers sit in the execution-priority stack *above* learned
PatternRules but *below* immutable safety rules.

Ported from the reference's ``blockers/{models,engine}.py`` @a761e29
(async → sync; structlog → stdlib logging).  Genericised per golden
rule 4: the reference's ``BlockerType`` enum (guest_count_unconfirmed,
cleaning_incomplete, …), ``ActionType`` references, and the
``DEFAULT_BLOCKER_ACTIONS`` / ``DEFAULT_SEVERITY`` mappings are
hospitality vocabulary — ``blocker_type`` and action kinds are opaque
``str`` here, the default mappings are injected into
:class:`BlockerEngine` (pack data: ``packs/hospitality/blockers.yaml``),
and the PMS-specific auto-detection rules became the injectable
:class:`ViolationDetector` seam (the reference's detector logic is
preserved verbatim in the test suite as the hospitality example until
the pack infrastructure lands in Batch 6).  :class:`BlockerSeverity`
(SOFT/HARD) is mechanism and stays.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, override, runtime_checkable

__all__ = [
    "Blocker",
    "BlockerEngine",
    "BlockerSeverity",
    "BlockerStore",
    "InMemoryBlockerStore",
    "ViolationDetector",
]


logger = logging.getLogger(__name__)


class BlockerSeverity(StrEnum):
    """How strictly the blocker enforces its precondition.

    Attributes:
        SOFT: Warn the operator but allow override.
        HARD: Block the action until the blocker is resolved.
    """

    SOFT = "soft"
    HARD = "hard"


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)


def _new_id() -> str:
    """Generate a unique blocker identifier."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Blocker:
    """An active precondition that blocks one or more actions.

    Blockers are immutable once created.  Resolution produces a *new*
    Blocker instance with ``resolved_at`` and ``resolved_by`` set
    (via ``dataclasses.replace``).

    Attributes:
        blocker_id: Unique identifier.
        blocker_type: Precondition category (opaque vertical-defined
            string, e.g. ``"payment_incomplete"``).
        severity: How strictly this blocker is enforced.
        property_id: Property this blocker applies to.
        reservation_id: Reservation this blocker is tied to (if any).
        description: Human-readable explanation of what is blocked and why.
        blocks_actions: Tuple of action-kind strings this blocker prevents.
        metadata: Additional context (PMS fields, guest info, …).
        created_at: When the blocker was created.
        resolved_at: When the blocker was resolved (None if still active).
        resolved_by: Who or what resolved the blocker.
    """

    blocker_type: str
    property_id: str
    description: str
    blocker_id: str = field(default_factory=_new_id)
    severity: BlockerSeverity = BlockerSeverity.HARD
    reservation_id: str | None = None
    blocks_actions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    resolved_at: datetime | None = None
    resolved_by: str | None = None

    @override
    def __repr__(self) -> str:
        status = "resolved" if self.is_resolved else "active"
        return (
            f"Blocker({self.blocker_type}, "
            f"property={self.property_id}, "
            f"severity={self.severity.value}, "
            f"status={status})"
        )

    def __post_init__(self) -> None:
        if not self.blocker_type:
            raise ValueError("blocker_type required")

    @property
    def is_resolved(self) -> bool:
        """Whether this blocker has been resolved."""
        return self.resolved_at is not None

    @property
    def is_active(self) -> bool:
        """Whether this blocker is still active (not resolved)."""
        return self.resolved_at is None

    @property
    def is_hard(self) -> bool:
        """Whether this is a hard (non-overridable) blocker."""
        return self.severity == BlockerSeverity.HARD

    def blocks_action(self, action_type: str) -> bool:
        """Check whether this active blocker prevents a specific action."""
        if self.is_resolved:
            return False
        return action_type in self.blocks_actions

    @property
    def age_hours(self) -> float:
        """Hours since this blocker was created."""
        delta = _utc_now() - self.created_at
        return delta.total_seconds() / 3600


# ---------------------------------------------------------------------------
# Store protocol + in-memory implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class BlockerStore(Protocol):
    """Abstract blocker persistence."""

    def save(self, blocker: Blocker) -> str:
        """Persist a blocker, returning its blocker_id."""
        ...

    def get(self, blocker_id: str) -> Blocker | None:
        """Retrieve a single blocker by ID."""
        ...

    def get_active(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        """Return active (unresolved) blockers for a property/reservation."""
        ...

    def update(self, blocker: Blocker) -> None:
        """Replace a stored blocker (e.g. after resolution)."""
        ...


class InMemoryBlockerStore:
    """Per-process :class:`BlockerStore` for development and testing."""

    def __init__(self) -> None:
        self._blockers: dict[str, Blocker] = {}

    def save(self, blocker: Blocker) -> str:
        self._blockers[blocker.blocker_id] = blocker
        return blocker.blocker_id

    def get(self, blocker_id: str) -> Blocker | None:
        return self._blockers.get(blocker_id)

    def get_active(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        results = []
        for blocker in self._blockers.values():
            if blocker.is_resolved:
                continue
            if blocker.property_id != property_id:
                continue
            if reservation_id and blocker.reservation_id != reservation_id:
                continue
            results.append(blocker)
        return results

    def update(self, blocker: Blocker) -> None:
        self._blockers[blocker.blocker_id] = blocker


# ---------------------------------------------------------------------------
# Violation detection seam (vertical logic stays out of the kernel)
# ---------------------------------------------------------------------------


class ViolationDetector(Protocol):
    """Detect precondition violations from operational snapshots.

    Pure function seam — no I/O.  Implementations are vertical-pack
    content (the reference's PMS detector checked ``adults``,
    ``payment_status``, ``id_verified``, ``cleaning_status``); the
    kernel only runs whatever detector the caller wires in.
    """

    def __call__(
        self,
        pms_data: Mapping[str, Any],
        ops_data: Mapping[str, Any],
        existing_types: set[str],
    ) -> list[tuple[str, str]]:
        """Return ``(blocker_type, description)`` pairs for new violations."""
        ...


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class BlockerEngine:
    """Runtime engine for blocker evaluation and lifecycle management.

    Responsibilities:
    - **check_blockers**: Given a proposed action, return all blockers
      that prevent it.
    - **has_hard_blocker**: Quick boolean check for hard blockers.
    - **create_blocker**: Register a new blocker from operational signals.
    - **resolve_blocker**: Mark a blocker as resolved.
    - **auto_detect_blockers**: Run the injected detector over PMS/ops
      data and create blockers for detected precondition violations.

    The per-type default severity and blocked-action mappings are
    injected (pack / tenant data); blocker types without an entry fall
    back to ``HARD`` with no default actions, mirroring the reference's
    ``dict.get`` fallbacks.
    """

    def __init__(
        self,
        store: BlockerStore,
        *,
        default_severity: Mapping[str, BlockerSeverity] | None = None,
        default_actions: Mapping[str, tuple[str, ...]] | None = None,
        detector: ViolationDetector | None = None,
    ) -> None:
        self._store = store
        self._default_severity = dict(default_severity or {})
        self._default_actions = dict(default_actions or {})
        self._detector = detector

    def check_blockers(
        self,
        property_id: str,
        reservation_id: str | None,
        action_type: str,
    ) -> list[Blocker]:
        """Return all active blockers that prevent a specific action."""
        active = self._store.get_active(property_id, reservation_id)
        blocking = [b for b in active if b.blocks_action(action_type)]
        if blocking:
            logger.warning(
                "action_blocked action=%s property_id=%s reservation_id=%s blocker_count=%s hard_count=%s",
                action_type,
                property_id,
                reservation_id,
                len(blocking),
                sum(1 for b in blocking if b.is_hard),
            )
        return blocking

    def has_hard_blocker(
        self,
        property_id: str,
        reservation_id: str | None,
        action_type: str,
    ) -> bool:
        """Quick check: is there at least one hard blocker for this action?"""
        blockers = self.check_blockers(property_id, reservation_id, action_type)
        return any(b.is_hard for b in blockers)

    def create_blocker(
        self,
        *,
        blocker_type: str,
        property_id: str,
        description: str,
        reservation_id: str | None = None,
        severity: BlockerSeverity | None = None,
        blocks_actions: tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Blocker:
        """Create and persist a new blocker.

        Uses the injected default severity / action mappings when not
        explicitly provided.
        """
        effective_severity = severity or self._default_severity.get(blocker_type, BlockerSeverity.HARD)
        effective_actions = blocks_actions or self._default_actions.get(blocker_type, ())
        blocker = Blocker(
            blocker_type=blocker_type,
            property_id=property_id,
            description=description,
            severity=effective_severity,
            reservation_id=reservation_id,
            blocks_actions=tuple(effective_actions),
            metadata=metadata or {},
        )
        self._store.save(blocker)
        logger.info(
            "blocker_created blocker_id=%s type=%s severity=%s property_id=%s reservation_id=%s blocked_actions=%s",
            blocker.blocker_id[:8],
            blocker_type,
            effective_severity.value,
            property_id,
            reservation_id,
            list(effective_actions),
        )
        return blocker

    def resolve_blocker(
        self,
        blocker_id: str,
        resolved_by: str,
    ) -> bool:
        """Mark a blocker as resolved (idempotent on already-resolved)."""
        blocker = self._store.get(blocker_id)
        if blocker is None:
            logger.warning("blocker_not_found blocker_id=%s", blocker_id)
            return False
        if blocker.is_resolved:
            logger.debug("blocker_already_resolved blocker_id=%s", blocker_id)
            return True
        resolved = replace(
            blocker,
            resolved_at=datetime.now(UTC),
            resolved_by=resolved_by,
        )
        self._store.update(resolved)
        logger.info(
            "blocker_resolved blocker_id=%s type=%s resolved_by=%s age_hours=%s",
            blocker_id[:8],
            blocker.blocker_type,
            resolved_by,
            round(blocker.age_hours, 1),
        )
        return True

    def get_active_blockers(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        """Return all active blockers for a property/reservation."""
        return self._store.get_active(property_id, reservation_id)

    def auto_detect_blockers(
        self,
        *,
        property_id: str,
        reservation_id: str | None = None,
        pms_data: dict[str, Any],
        ops_data: dict[str, Any] | None = None,
    ) -> list[Blocker]:
        """Run the injected detector and create blockers for violations.

        Returns an empty list (and logs once at debug) when no detector
        is wired — the kernel ships no vertical detection rules.
        """
        if self._detector is None:
            logger.debug("auto_detect_skipped: no violation detector wired")
            return []
        created: list[Blocker] = []
        ops = ops_data or {}
        existing = self._store.get_active(property_id, reservation_id)
        existing_types = {b.blocker_type for b in existing}
        detections = self._detector(pms_data, ops, existing_types)
        for blocker_type, description in detections:
            blocker = self.create_blocker(
                blocker_type=blocker_type,
                property_id=property_id,
                reservation_id=reservation_id,
                description=description,
            )
            created.append(blocker)
        return created
