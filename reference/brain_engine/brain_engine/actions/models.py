"""Action envelope value objects.

An :class:`ActionEnvelope` wraps a concrete action the engine has
decided to execute.  The envelope carries everything the runtime
needs to both *run* and later *undo* the action:

- the action type + payload (what to do),
- reversibility tier (how Undo behaves),
- optional compensating payload (for AMBER actions),
- audit metadata (who / when / why),
- lifecycle status.

Envelopes are immutable — state transitions produce a new instance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from brain_engine.cards.models import ReversibilityTier


class ActionStatus(StrEnum):
    """Lifecycle states of an :class:`ActionEnvelope`."""

    PENDING = "pending"
    EXECUTED = "executed"
    UNDONE = "undone"
    FAILED = "failed"


_GREEN_UNDO_SECONDS = 60
_AMBER_UNDO_SECONDS = 10 * 60


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Generate a unique action identifier."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    """Immutable record of a decided action.

    Attributes:
        action_id: Stable identifier (generated on construction).
        property_id: Property the action targets.
        workflow: Workflow name (free text, e.g. ``"send_access_code"``).
        action_type: Concrete action taxonomy value.
        payload: Action parameters passed to the executor.
        reversibility: Undo tier — gates UndoExecutor behavior.
        compensating_payload: Optional payload for AMBER rollback.
        status: Lifecycle state.
        requested_by: Who initiated the action (PM / system / autopilot).
        executed_at: When execution finished.
        undone_at: When the action was reversed.
        undo_reason: Free-text reason for reversal.
        external_reference: Opaque handle returned by the downstream
            system (e.g. PMS message id) — required for compensating
            calls on AMBER actions.
    """

    property_id: str
    workflow: str
    action_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    reversibility: ReversibilityTier = ReversibilityTier.AMBER
    compensating_payload: dict[str, Any] | None = None
    action_id: str = field(default_factory=_new_id)
    status: ActionStatus = ActionStatus.PENDING
    requested_by: str = "system"
    created_at: datetime = field(default_factory=_utc_now)
    executed_at: datetime | None = None
    undone_at: datetime | None = None
    undo_reason: str | None = None
    external_reference: str | None = None

    @property
    def undo_window_seconds(self) -> int:
        """Hard window after execution during which Undo is allowed."""
        if self.reversibility is ReversibilityTier.GREEN:
            return _GREEN_UNDO_SECONDS
        if self.reversibility is ReversibilityTier.AMBER:
            return _AMBER_UNDO_SECONDS
        return 0

    @property
    def is_executed(self) -> bool:
        """Whether the action has been executed."""
        return self.status is ActionStatus.EXECUTED

    @property
    def is_undone(self) -> bool:
        """Whether the action has already been reversed."""
        return self.status is ActionStatus.UNDONE

    def within_undo_window(self, *, now: datetime | None = None) -> bool:
        """Whether the GREEN/AMBER undo window is still open."""
        if not self.is_executed or self.executed_at is None:
            return False
        window = self.undo_window_seconds
        if window <= 0:
            return False
        reference = now or _utc_now()
        elapsed = (reference - self.executed_at).total_seconds()
        return elapsed <= window
