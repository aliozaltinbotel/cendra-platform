"""Per-workflow autonomy value objects.

V2 replaces the single property-wide autonomy slider with a
*per-workflow* progression: each repeatable action ("send access
code", "reply check-in ETA", "charge security deposit") has its own
state machine.  Three states:

- ``OBSERVE`` — Brain Engine drafts; the PM always confirms.
- ``SEMI_AUTO`` — Brain Engine executes after a short hold window
  during which the PM can cancel.
- ``AUTOPILOT`` — Brain Engine executes immediately; the PM receives
  only a digest.

State transitions are gated on five reliability metrics (see
:pyattr:`PromotionGate.required_metrics`) rather than on a single
ratio — this keeps a noisy success stream from pushing a workflow
into autopilot on thin evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final


class AutonomyState(StrEnum):
    """Per-workflow autonomy progression.

    Ordering is meaningful: ``OBSERVE`` < ``SEMI_AUTO`` < ``AUTOPILOT``.
    """

    OBSERVE = "observe"
    SEMI_AUTO = "semi_auto"
    AUTOPILOT = "autopilot"


_STATE_ORDER: Final[dict[AutonomyState, int]] = {
    AutonomyState.OBSERVE: 0,
    AutonomyState.SEMI_AUTO: 1,
    AutonomyState.AUTOPILOT: 2,
}


def state_rank(state: AutonomyState) -> int:
    """Return the monotonic rank of an autonomy state."""
    return _STATE_ORDER[state]


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorkflowMetrics:
    """Observed reliability metrics for one property/workflow pair.

    ``success_rate`` is the Wilson-lower-bound style reliability
    (passed in by the caller; this object does not compute it).
    ``override_rate`` is PM-cancel / PM-correction frequency.
    ``incidents`` counts post-action user complaints.

    All fields are counts or rates — the gate decides thresholds.
    """

    sample_size: int = 0
    success_rate: float = 0.0
    override_rate: float = 0.0
    incidents: int = 0
    mean_latency_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class WorkflowAutonomy:
    """State of a single workflow for one property.

    Value object — transitions produce a *new* instance via
    :func:`dataclasses.replace`.  The engine writes the audit trail
    (``changed_at`` / ``changed_by`` / ``reason``) every transition
    so downstream UI can explain "why is this on autopilot?".
    """

    property_id: str
    workflow: str
    state: AutonomyState = AutonomyState.OBSERVE
    metrics: WorkflowMetrics = field(default_factory=WorkflowMetrics)
    hold_seconds: int = 60
    changed_at: datetime = field(default_factory=_utc_now)
    changed_by: str = "system"
    reason: str = "initialized"

    @property
    def allows_immediate_execution(self) -> bool:
        """Whether the workflow may run without any PM touch."""
        return self.state is AutonomyState.AUTOPILOT

    @property
    def requires_confirmation(self) -> bool:
        """Whether the workflow must wait for PM approval."""
        return self.state is AutonomyState.OBSERVE

    @property
    def has_hold_window(self) -> bool:
        """Whether the PM can still cancel after proposal."""
        return self.state is AutonomyState.SEMI_AUTO
