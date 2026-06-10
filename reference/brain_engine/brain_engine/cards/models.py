"""Decision card value objects.

A *decision card* is Brain Engine's canonical explanation unit shown
in the V2 UI.  Every card exposes five slots:

1. ``context_tag`` — one-line situation label (e.g. "Late checkout
   requested", "Access code hold").
2. ``title`` — plain-English headline the PM can act on.
3. ``reasoning`` — ordered ``ReasoningRow`` list: what rule / case /
   blocker / preference led to the recommendation.
4. ``action`` — :class:`PreparedAction` ready to execute, with its
   reversibility tier so the UI can render the correct Undo affordance.
5. ``trust_footer`` — one sentence about autonomy state and the
   hold-window if ``SEMI_AUTO``.

Cards are immutable; the builder constructs one per propose cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from brain_engine.autonomy.models import AutonomyState


class ReversibilityTier(StrEnum):
    """Undo affordance tier.

    - ``GREEN``: fully reversible within 60 s.
    - ``AMBER``: reversible via compensating action within 10 min.
    - ``RED``: effectively irreversible; audit-log only.
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class EvidenceKind(StrEnum):
    """Source of a single reasoning row."""

    RULE = "rule"
    CASE = "case"
    BLOCKER = "blocker"
    PREFERENCE = "preference"
    HEURISTIC = "heuristic"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ReasoningRow:
    """One evidence line on the decision card."""

    kind: EvidenceKind
    label: str
    weight: float = 1.0
    reference_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedAction:
    """A fully-described action the UI may execute on confirmation."""

    action_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    reversibility: ReversibilityTier = ReversibilityTier.AMBER
    undo_window_seconds: int = 60


@dataclass(frozen=True, slots=True)
class DecisionCard:
    """Canonical five-slot decision card rendered by the V2 UI."""

    property_id: str
    workflow: str
    context_tag: str
    title: str
    reasoning: tuple[ReasoningRow, ...]
    action: PreparedAction
    trust_footer: str
    autonomy_state: AutonomyState
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @property
    def is_actionable(self) -> bool:
        """Whether the card carries a non-empty action payload."""
        return bool(self.action.action_type)

    @property
    def has_blockers(self) -> bool:
        """Whether any reasoning row is a blocker."""
        return any(r.kind is EvidenceKind.BLOCKER for r in self.reasoning)
