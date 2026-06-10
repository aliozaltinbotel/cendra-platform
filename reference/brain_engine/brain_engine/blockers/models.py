"""Blocker domain models.

A Blocker is a precondition that must be satisfied before a sensitive
action can proceed.  For example, access codes must not be released
until the guest count is confirmed and the guest identity is verified.

Blockers enforce the Cendra principle: "Hold sensitive data until
preconditions are met."  They sit in the execution-priority stack
*above* learned PatternRules but *below* immutable safety rules.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final

from brain_engine.approval.models import ActionType


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class BlockerType(StrEnum):
    """Precondition types that can block actions.

    Each type maps to a real operational precondition that Cendra PMs
    verify before allowing sensitive actions.
    """

    GUEST_COUNT_UNCONFIRMED = "guest_count_unconfirmed"
    PAYMENT_INCOMPLETE = "payment_incomplete"
    ID_UNVERIFIED = "id_unverified"
    PROPERTY_NOT_READY = "property_not_ready"
    APPROVAL_PENDING = "approval_pending"
    OPS_UNRESOLVED = "ops_unresolved"
    ACCESS_DATA_UNVERIFIED = "access_data_unverified"
    AGREEMENT_UNSIGNED = "agreement_unsigned"
    CLEANING_INCOMPLETE = "cleaning_incomplete"
    DAMAGE_UNINSPECTED = "damage_uninspected"
    MAINTENANCE_IN_PROGRESS = "maintenance_in_progress"


class BlockerSeverity(StrEnum):
    """How strictly the blocker enforces its precondition.

    Attributes:
        SOFT: Warn the operator but allow override.
        HARD: Block the action until the blocker is resolved.
    """

    SOFT = "soft"
    HARD = "hard"


# ---------------------------------------------------------------------------
# Default blocker → action mappings
# ---------------------------------------------------------------------------

# Which actions each blocker type prevents by default.
# Hard blockers prevent execution; soft blockers trigger warnings.
DEFAULT_BLOCKER_ACTIONS: Final[dict[BlockerType, tuple[ActionType, ...]]] = {
    BlockerType.GUEST_COUNT_UNCONFIRMED: (
        ActionType.SEND_ACCESS_CODE,
    ),
    BlockerType.PAYMENT_INCOMPLETE: (
        ActionType.SEND_ACCESS_CODE,
        ActionType.LATE_CHECKOUT,
    ),
    BlockerType.ID_UNVERIFIED: (
        ActionType.SEND_ACCESS_CODE,
    ),
    BlockerType.PROPERTY_NOT_READY: (
        ActionType.SEND_ACCESS_CODE,
        ActionType.LATE_CHECKOUT,
    ),
    BlockerType.APPROVAL_PENDING: (
        ActionType.CHARGE_GUEST,
        ActionType.SUBMIT_DAMAGE_CLAIM,
        ActionType.OFFER_DISCOUNT,
    ),
    BlockerType.CLEANING_INCOMPLETE: (
        ActionType.SEND_ACCESS_CODE,
    ),
    BlockerType.DAMAGE_UNINSPECTED: (
        ActionType.SEND_ACCESS_CODE,
    ),
    BlockerType.MAINTENANCE_IN_PROGRESS: (
        ActionType.SEND_ACCESS_CODE,
        ActionType.LATE_CHECKOUT,
    ),
}

# Default severity per blocker type.
DEFAULT_SEVERITY: Final[dict[BlockerType, BlockerSeverity]] = {
    BlockerType.GUEST_COUNT_UNCONFIRMED: BlockerSeverity.HARD,
    BlockerType.PAYMENT_INCOMPLETE: BlockerSeverity.HARD,
    BlockerType.ID_UNVERIFIED: BlockerSeverity.HARD,
    BlockerType.PROPERTY_NOT_READY: BlockerSeverity.HARD,
    BlockerType.APPROVAL_PENDING: BlockerSeverity.HARD,
    BlockerType.OPS_UNRESOLVED: BlockerSeverity.SOFT,
    BlockerType.ACCESS_DATA_UNVERIFIED: BlockerSeverity.HARD,
    BlockerType.AGREEMENT_UNSIGNED: BlockerSeverity.SOFT,
    BlockerType.CLEANING_INCOMPLETE: BlockerSeverity.HARD,
    BlockerType.DAMAGE_UNINSPECTED: BlockerSeverity.SOFT,
    BlockerType.MAINTENANCE_IN_PROGRESS: BlockerSeverity.SOFT,
}


# ---------------------------------------------------------------------------
# Blocker value object
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


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
        blocker_type: Precondition category.
        severity: How strictly this blocker is enforced.
        property_id: Property this blocker applies to.
        reservation_id: Reservation this blocker is tied to (if any).
        description: Human-readable explanation of what is blocked and why.
        blocks_actions: Tuple of ActionTypes this blocker prevents.
        metadata: Additional context (PMS fields, guest info, …).
        created_at: When the blocker was created.
        resolved_at: When the blocker was resolved (None if still active).
        resolved_by: Who or what resolved the blocker.
    """

    blocker_type: BlockerType
    property_id: str
    description: str
    blocker_id: str = field(default_factory=_new_id)
    severity: BlockerSeverity = BlockerSeverity.HARD
    reservation_id: str | None = None
    blocks_actions: tuple[ActionType, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    resolved_at: datetime | None = None
    resolved_by: str | None = None

    def __repr__(self) -> str:
        status = "resolved" if self.is_resolved else "active"
        return (
            f"Blocker({self.blocker_type.value}, "
            f"property={self.property_id}, "
            f"severity={self.severity.value}, "
            f"status={status})"
        )

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

    def blocks_action(self, action_type: ActionType) -> bool:
        """Check whether this blocker prevents a specific action.

        Args:
            action_type: The action to check.

        Returns:
            True if this active blocker prevents the action.
        """
        if self.is_resolved:
            return False
        return action_type in self.blocks_actions

    @property
    def age_hours(self) -> float:
        """Hours since this blocker was created."""
        delta = _utc_now() - self.created_at
        return delta.total_seconds() / 3600
