"""Output value object for the Planner layer style selector."""

from __future__ import annotations

from dataclasses import dataclass

from brain_engine.planner.styles import (
    PlannerStyleId,
    PlannerStyleSpec,
)


__all__ = ["PlannerDecision"]


@dataclass(frozen=True, slots=True)
class PlannerDecision:
    """The style picked for one decision plus the rationale.

    Attributes:
        style_id: Identifier of the picked style.
        spec: Resolved spec carrying the constraint envelope.
        rationale: One-line plain-English explanation of the pick;
            consumed by the audit log so the regulator can verify
            *why* a particular style was applied.
    """

    style_id: PlannerStyleId
    spec: PlannerStyleSpec
    rationale: str
