"""Input value object for the Planner layer style selector."""

from __future__ import annotations

from dataclasses import dataclass

from brain_engine.cards.action_kinds import CardActionKind


__all__ = ["PlannerContext"]


@dataclass(frozen=True, slots=True)
class PlannerContext:
    """Inputs the :class:`StyleSelector` consults to pick a style.

    Attributes:
        property_id: Property the decision concerns.
        owner_id: Owner of the property — DSL-pinned styles are
            keyed by this.
        action_kind: Action class under consideration; included so
            future selectors may scope their pick by action class
            (e.g. "compliance_strict only for financial actions").
        jurisdiction: City / region code (e.g. ``"BCN"``, ``"NYC"``,
            ``"PAR"``) used by the safety default for regulated
            jurisdictions.  ``None`` skips the jurisdiction step.
        severity: Free-form severity hint (``"info"`` / ``"warn"`` /
            ``"critical"``).  ``warn`` and ``critical`` fall back to
            ``DEFENSIVE`` when no other rule applies.
    """

    property_id: str
    owner_id: str
    action_kind: CardActionKind
    jurisdiction: str | None = None
    severity: str = "info"
