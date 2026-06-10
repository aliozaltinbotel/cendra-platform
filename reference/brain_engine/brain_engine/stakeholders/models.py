"""Value objects for the multi-stakeholder negotiation layer.

Five stakeholder roles ship by default — guest / owner / cleaner /
neighbor / regulator — chosen because every STR decision touches at
least one of them and most touch several.  The engine searches a
Pareto frontier across their utility models and returns one
:class:`NegotiationOutcome` carrying the selected action plus the
audit trail (per-stakeholder utility, frontier set, rationale).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


__all__ = [
    "ActionCandidate",
    "BargainingSolution",
    "DEFAULT_STAKEHOLDER_PRIORITIES",
    "NegotiationOutcome",
    "StakeholderId",
]


class StakeholderId(StrEnum):
    """Five default stakeholder roles per STR decision.

    The set is deliberately closed.  Custom roles (e.g. an
    insurance underwriter for high-value bookings) extend the enum
    only via a follow-up PR — keeping the runtime closed avoids
    silent priority drift.
    """

    GUEST = "guest"
    OWNER = "owner"
    CLEANER = "cleaner"
    NEIGHBOR = "neighbor"
    REGULATOR = "regulator"


class BargainingSolution(StrEnum):
    """Solution concepts the engine can pick from the frontier.

    - :attr:`NASH`: argmax of the product of utilities (asymmetric
      Nash-bargaining solution).  Fairness-leaning default.
    - :attr:`EGALITARIAN`: argmax of the minimum utility — picks the
      action that makes the worst-off stakeholder least bad.
    - :attr:`UTILITARIAN`: argmax of the priority-weighted sum.
      Used when an explicit ranking exists.
    """

    NASH = "nash"
    EGALITARIAN = "egalitarian"
    UTILITARIAN = "utilitarian"


DEFAULT_STAKEHOLDER_PRIORITIES: Final[
    Mapping[StakeholderId, float]
] = {
    StakeholderId.REGULATOR: 1.0,
    StakeholderId.NEIGHBOR: 0.85,
    StakeholderId.GUEST: 0.7,
    StakeholderId.OWNER: 0.7,
    StakeholderId.CLEANER: 0.5,
}


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    """One candidate action under consideration.

    Attributes:
        action_id: Stable identifier so callers can look the
            action back up after :class:`NegotiationOutcome` is
            recorded.
        features: Numeric features the utility models score.
            Keys are free-form (``"price_eur"`` /
            ``"checkout_minutes"`` / ``"noise_db"``); values must
            be finite floats.
        hard_violations: Stakeholders who *cannot* accept this
            candidate (e.g. it breaks a regulator hard-constraint).
            The engine filters these out before scoring.
    """

    action_id: str
    features: Mapping[str, float]
    hard_violations: frozenset[StakeholderId] = frozenset()

    def __post_init__(self) -> None:
        if not self.action_id:
            raise ValueError("action_id required")
        for key, value in self.features.items():
            if not isinstance(value, (int, float)):
                raise TypeError(
                    f"feature {key!r} must be numeric"
                )
            if value != value or value in (
                float("inf"),
                float("-inf"),
            ):
                raise ValueError(
                    f"feature {key!r} must be finite"
                )

    def acceptable_to(self, stakeholder: StakeholderId) -> bool:
        """Return ``True`` when ``stakeholder`` does not hard-veto."""
        return stakeholder not in self.hard_violations


@dataclass(frozen=True, slots=True)
class NegotiationOutcome:
    """Resolved multi-stakeholder negotiation result.

    Attributes:
        selected: The picked :class:`ActionCandidate`; ``None``
            when every candidate was hard-vetoed by at least one
            stakeholder.
        pareto_frontier: Ordered tuple of frontier actions.  The
            audit log records the full frontier so a regulator
            can replay the solver's working set.
        per_stakeholder_utility: Per-stakeholder utility of the
            selected action; empty when ``selected`` is ``None``.
        bargaining_solution: Solution concept used.
        rationale: One-line plain-English explanation.
    """

    selected: ActionCandidate | None
    pareto_frontier: tuple[ActionCandidate, ...]
    per_stakeholder_utility: Mapping[StakeholderId, float]
    bargaining_solution: BargainingSolution
    rationale: str
