"""Blocker engine — evaluates and manages active blockers.

BlockerEngine is the runtime component that:
1. Checks whether an action is blocked before execution.
2. Creates new blockers from operational signals (PMS, calendar, ops).
3. Resolves blockers when preconditions are satisfied.

It sits in the execution-priority stack between immutable safety rules
(above) and learned PatternRules (below), ensuring that sensitive
actions never proceed while hard preconditions remain unmet.

Design:
- Protocol-based storage (``BlockerStore``) for DIP compliance.
- InMemoryBlockerStore for development; Postgres/Redis for production.
- All public methods are async to support networked stores.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import structlog

from brain_engine.approval.models import ActionType
from brain_engine.blockers.models import (
    Blocker,
    BlockerSeverity,
    BlockerType,
    DEFAULT_BLOCKER_ACTIONS,
    DEFAULT_SEVERITY,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Storage protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class BlockerStore(Protocol):
    """Abstract storage for blocker persistence.

    Any class implementing these four async methods satisfies the
    protocol — no inheritance required.
    """

    async def save(self, blocker: Blocker) -> str:
        """Persist a blocker, returning its ID."""
        ...

    async def get(self, blocker_id: str) -> Blocker | None:
        """Retrieve a single blocker by ID."""
        ...

    async def get_active(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        """Return all active (unresolved) blockers for a scope."""
        ...

    async def update(self, blocker: Blocker) -> None:
        """Update a blocker (e.g. after resolution)."""
        ...


# ---------------------------------------------------------------------------
# In-memory store (dev / test)
# ---------------------------------------------------------------------------

class InMemoryBlockerStore:
    """Thread-safe in-memory blocker store for development and testing.

    Not suitable for production — blockers are lost on restart.

    Attributes:
        _blockers: Dict mapping blocker_id → Blocker.
    """

    def __init__(self) -> None:
        self._blockers: dict[str, Blocker] = {}

    async def save(self, blocker: Blocker) -> str:
        """Store a blocker and return its ID.

        Args:
            blocker: The blocker to persist.

        Returns:
            The blocker's unique identifier.
        """
        self._blockers[blocker.blocker_id] = blocker
        return blocker.blocker_id

    async def get(self, blocker_id: str) -> Blocker | None:
        """Retrieve a blocker by ID.

        Args:
            blocker_id: Unique identifier.

        Returns:
            The blocker or None if not found.
        """
        return self._blockers.get(blocker_id)

    async def get_active(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        """Return all active blockers for a property/reservation.

        Args:
            property_id: Property identifier.
            reservation_id: Optional reservation filter.

        Returns:
            List of active (unresolved) blockers.
        """
        results: list[Blocker] = []
        for blocker in self._blockers.values():
            if not blocker.is_active:
                continue
            if blocker.property_id != property_id:
                continue
            if reservation_id and blocker.reservation_id != reservation_id:
                continue
            results.append(blocker)
        return results

    async def update(self, blocker: Blocker) -> None:
        """Update a blocker in the store.

        Args:
            blocker: Updated blocker instance.
        """
        self._blockers[blocker.blocker_id] = blocker


# ---------------------------------------------------------------------------
# BlockerEngine
# ---------------------------------------------------------------------------

class BlockerEngine:
    """Runtime engine for blocker evaluation and lifecycle management.

    Responsibilities:
    - **check_blockers**: Given a proposed action, return all blockers
      that prevent it.
    - **has_hard_blocker**: Quick boolean check for hard blockers.
    - **create_blocker**: Register a new blocker from operational signals.
    - **resolve_blocker**: Mark a blocker as resolved.
    - **auto_detect_blockers**: Scan PMS/ops data and create blockers
      for detected precondition violations.

    Attributes:
        _store: Protocol-based blocker persistence.
        _log: Bound structured logger.
    """

    def __init__(self, store: BlockerStore) -> None:
        self._store = store
        self._log = logger.bind(component="blocker_engine")

    async def check_blockers(
        self,
        property_id: str,
        reservation_id: str | None,
        action_type: ActionType,
    ) -> list[Blocker]:
        """Return all active blockers that prevent a specific action.

        Args:
            property_id: Property identifier.
            reservation_id: Reservation identifier (if applicable).
            action_type: The action to check.

        Returns:
            List of blockers preventing this action (may be empty).
        """
        active = await self._store.get_active(property_id, reservation_id)
        blocking = [b for b in active if b.blocks_action(action_type)]

        if blocking:
            self._log.warning(
                "action_blocked",
                action=action_type.value,
                property_id=property_id,
                reservation_id=reservation_id,
                blocker_count=len(blocking),
                hard_count=sum(1 for b in blocking if b.is_hard),
            )
        return blocking

    async def has_hard_blocker(
        self,
        property_id: str,
        reservation_id: str | None,
        action_type: ActionType,
    ) -> bool:
        """Quick check: is there at least one hard blocker for this action?

        Args:
            property_id: Property identifier.
            reservation_id: Reservation identifier.
            action_type: The action to check.

        Returns:
            True if at least one hard blocker exists.
        """
        blockers = await self.check_blockers(
            property_id, reservation_id, action_type,
        )
        return any(b.is_hard for b in blockers)

    async def create_blocker(
        self,
        *,
        blocker_type: BlockerType,
        property_id: str,
        description: str,
        reservation_id: str | None = None,
        severity: BlockerSeverity | None = None,
        blocks_actions: tuple[ActionType, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Blocker:
        """Create and persist a new blocker.

        Uses default severity and action mappings from
        ``blockers.models`` if not explicitly provided.

        Args:
            blocker_type: Precondition type.
            property_id: Property identifier.
            description: Human-readable explanation.
            reservation_id: Reservation identifier (if applicable).
            severity: Override default severity.
            blocks_actions: Override default blocked actions.
            metadata: Additional context data.

        Returns:
            The persisted Blocker instance.
        """
        effective_severity = severity or DEFAULT_SEVERITY.get(
            blocker_type, BlockerSeverity.HARD,
        )
        effective_actions = blocks_actions or DEFAULT_BLOCKER_ACTIONS.get(
            blocker_type, (),
        )

        blocker = Blocker(
            blocker_type=blocker_type,
            property_id=property_id,
            description=description,
            severity=effective_severity,
            reservation_id=reservation_id,
            blocks_actions=effective_actions,
            metadata=metadata or {},
        )

        await self._store.save(blocker)

        self._log.info(
            "blocker_created",
            blocker_id=blocker.blocker_id[:8],
            type=blocker_type.value,
            severity=effective_severity.value,
            property_id=property_id,
            reservation_id=reservation_id,
            blocked_actions=[a.value for a in effective_actions],
        )
        return blocker

    async def resolve_blocker(
        self,
        blocker_id: str,
        resolved_by: str,
    ) -> bool:
        """Mark a blocker as resolved.

        Uses ``dataclasses.replace`` to produce an updated immutable
        Blocker instance.

        Args:
            blocker_id: Identifier of the blocker to resolve.
            resolved_by: Who or what resolved it (PM name, system, …).

        Returns:
            True if the blocker was found and resolved, False otherwise.
        """
        blocker = await self._store.get(blocker_id)
        if blocker is None:
            self._log.warning("blocker_not_found", blocker_id=blocker_id)
            return False

        if blocker.is_resolved:
            self._log.debug("blocker_already_resolved", blocker_id=blocker_id)
            return True

        resolved = replace(
            blocker,
            resolved_at=datetime.now(timezone.utc),
            resolved_by=resolved_by,
        )
        await self._store.update(resolved)

        self._log.info(
            "blocker_resolved",
            blocker_id=blocker_id[:8],
            type=blocker.blocker_type.value,
            resolved_by=resolved_by,
            age_hours=round(blocker.age_hours, 1),
        )
        return True

    async def get_active_blockers(
        self,
        property_id: str,
        reservation_id: str | None = None,
    ) -> list[Blocker]:
        """Return all active blockers for a property/reservation.

        Args:
            property_id: Property identifier.
            reservation_id: Optional reservation filter.

        Returns:
            List of active blockers.
        """
        return await self._store.get_active(property_id, reservation_id)

    async def auto_detect_blockers(
        self,
        *,
        property_id: str,
        reservation_id: str | None = None,
        pms_data: dict[str, Any],
        ops_data: dict[str, Any] | None = None,
    ) -> list[Blocker]:
        """Scan PMS and ops data for precondition violations.

        Automatically creates blockers for detected issues:
        - Guest count listed as 0 or missing → GUEST_COUNT_UNCONFIRMED
        - Payment status not 'paid' → PAYMENT_INCOMPLETE
        - ID not verified → ID_UNVERIFIED
        - Cleaning status not 'completed' → CLEANING_INCOMPLETE

        Args:
            property_id: Property identifier.
            reservation_id: Reservation identifier.
            pms_data: Current PMS reservation data.
            ops_data: Current operational data.

        Returns:
            List of newly created blockers.
        """
        created: list[Blocker] = []
        ops = ops_data or {}

        existing = await self._store.get_active(property_id, reservation_id)
        existing_types = {b.blocker_type for b in existing}

        detections = self._detect_violations(pms_data, ops, existing_types)

        for blocker_type, description in detections:
            blocker = await self.create_blocker(
                blocker_type=blocker_type,
                property_id=property_id,
                reservation_id=reservation_id,
                description=description,
            )
            created.append(blocker)

        return created

    def _detect_violations(
        self,
        pms_data: dict[str, Any],
        ops_data: dict[str, Any],
        existing_types: set[BlockerType],
    ) -> list[tuple[BlockerType, str]]:
        """Detect precondition violations from operational data.

        Pure function — no I/O.  Returns a list of (type, description)
        pairs for each detected violation that does not already have an
        active blocker.

        Args:
            pms_data: PMS reservation data.
            ops_data: Operational data.
            existing_types: Already-active blocker types (to avoid dupes).

        Returns:
            List of (BlockerType, description) pairs.
        """
        results: list[tuple[BlockerType, str]] = []

        guests = int(pms_data.get("adults", 0) or 0)
        if guests == 0 and BlockerType.GUEST_COUNT_UNCONFIRMED not in existing_types:
            results.append((
                BlockerType.GUEST_COUNT_UNCONFIRMED,
                "Guest count is 0 or missing — must be confirmed before "
                "releasing access codes.",
            ))

        payment = str(pms_data.get("payment_status", "")).lower()
        if payment not in {"paid", "completed"} and BlockerType.PAYMENT_INCOMPLETE not in existing_types:
            results.append((
                BlockerType.PAYMENT_INCOMPLETE,
                f"Payment status is '{payment}' — must be completed before "
                "access code release.",
            ))

        id_verified = pms_data.get("id_verified", False)
        if not id_verified and BlockerType.ID_UNVERIFIED not in existing_types:
            results.append((
                BlockerType.ID_UNVERIFIED,
                "Guest identity not verified — required before access "
                "code release.",
            ))

        cleaning = str(ops_data.get("cleaning_status", "")).lower()
        not_clean = cleaning and cleaning not in {"completed", "done", "clean"}
        if not_clean and BlockerType.CLEANING_INCOMPLETE not in existing_types:
            results.append((
                BlockerType.CLEANING_INCOMPLETE,
                f"Cleaning status is '{cleaning}' — property not ready "
                "for guest entry.",
            ))

        return results
