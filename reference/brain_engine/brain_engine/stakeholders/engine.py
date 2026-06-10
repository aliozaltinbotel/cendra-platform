"""Multi-stakeholder negotiation engine.

The engine accepts a set of :class:`ActionCandidate` objects, a
:class:`UtilityRoster` (one utility function per stakeholder), and
a :class:`BargainingSolution` strategy.  It returns a
:class:`NegotiationOutcome` carrying the selected action plus the
full audit trail.

Steps:

1. Filter candidates that any stakeholder hard-vetoes.
2. Score every remaining candidate per stakeholder.
3. Compute the Pareto frontier of the score vectors.
4. Apply the chosen :class:`BargainingSolution` to the frontier:
   - ``NASH`` — argmax of the product of utilities.
   - ``EGALITARIAN`` — argmax of the minimum utility.
   - ``UTILITARIAN`` — argmax of the priority-weighted sum.
5. Return the :class:`NegotiationOutcome`.

Every step is auditable: the rationale string cites the strategy,
the frontier size, and the per-stakeholder utility of the chosen
action so the regulator can replay the working set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import prod

import structlog

from brain_engine.stakeholders.models import (
    DEFAULT_STAKEHOLDER_PRIORITIES,
    ActionCandidate,
    BargainingSolution,
    NegotiationOutcome,
    StakeholderId,
)
from brain_engine.stakeholders.pareto import pareto_frontier
from brain_engine.stakeholders.utility import UtilityRoster


__all__ = ["StakeholderNegotiationEngine"]


logger = structlog.get_logger(__name__)


class StakeholderNegotiationEngine:
    """Run a Pareto / Nash-bargaining over candidate actions.

    Construction takes a :class:`UtilityRoster` and an optional
    priority map (used by the utilitarian solver).  ``decide``
    runs one negotiation round and produces a structured outcome.
    """

    def __init__(
        self,
        *,
        roster: UtilityRoster,
        priorities: Mapping[
            StakeholderId, float,
        ] = DEFAULT_STAKEHOLDER_PRIORITIES,
    ) -> None:
        self._roster = roster
        self._priorities = dict(priorities)
        self._log = logger.bind(component="stakeholder_engine")

    def decide(
        self,
        *,
        candidates: Sequence[ActionCandidate],
        solution: BargainingSolution = BargainingSolution.NASH,
    ) -> NegotiationOutcome:
        """Return the chosen action and the audit trail."""
        survivors = self._filter_hard_vetoed(candidates)
        if not survivors:
            return _empty_outcome(
                solution=solution,
                rationale="every candidate hard-vetoed",
            )
        utilities = self._score_all(survivors)
        frontier = pareto_frontier(survivors, utilities)
        if not frontier:
            return _empty_outcome(
                solution=solution,
                rationale="empty Pareto frontier",
            )
        selected = self._select(
            frontier=frontier,
            utilities=utilities,
            solution=solution,
        )
        chosen_scores = utilities[selected.action_id]
        rationale = self._rationale(
            solution=solution,
            frontier=frontier,
            selected=selected,
            scores=chosen_scores,
        )
        self._log.info(
            "negotiation.resolved",
            action_id=selected.action_id,
            solution=solution.value,
            frontier_size=len(frontier),
        )
        return NegotiationOutcome(
            selected=selected,
            pareto_frontier=frontier,
            per_stakeholder_utility=chosen_scores,
            bargaining_solution=solution,
            rationale=rationale,
        )

    # ── internals ─────────────────────────────────────────────── #

    def _filter_hard_vetoed(
        self,
        candidates: Sequence[ActionCandidate],
    ) -> list[ActionCandidate]:
        return [
            c for c in candidates
            if all(
                c.acceptable_to(s)
                for s in self._roster.stakeholders()
            )
        ]

    def _score_all(
        self,
        candidates: Sequence[ActionCandidate],
    ) -> dict[str, dict[StakeholderId, float]]:
        scores: dict[str, dict[StakeholderId, float]] = {}
        for candidate in candidates:
            scores[candidate.action_id] = {
                stakeholder: self._roster.for_stakeholder(
                    stakeholder,
                ).score(candidate)
                for stakeholder in self._roster.stakeholders()
            }
        return scores

    def _select(
        self,
        *,
        frontier: Sequence[ActionCandidate],
        utilities: Mapping[
            str, Mapping[StakeholderId, float],
        ],
        solution: BargainingSolution,
    ) -> ActionCandidate:
        if solution is BargainingSolution.NASH:
            return max(
                frontier,
                key=lambda c: prod(utilities[c.action_id].values()),
            )
        if solution is BargainingSolution.EGALITARIAN:
            return max(
                frontier,
                key=lambda c: min(
                    utilities[c.action_id].values()
                ),
            )
        return max(
            frontier,
            key=lambda c: self._weighted_sum(
                utilities[c.action_id],
            ),
        )

    def _weighted_sum(
        self,
        scores: Mapping[StakeholderId, float],
    ) -> float:
        total = 0.0
        for stakeholder, score in scores.items():
            total += score * self._priorities.get(
                stakeholder, 0.0,
            )
        return total

    @staticmethod
    def _rationale(
        *,
        solution: BargainingSolution,
        frontier: Sequence[ActionCandidate],
        selected: ActionCandidate,
        scores: Mapping[StakeholderId, float],
    ) -> str:
        bottom = min(scores.values())
        return (
            f"{solution.value} solution over {len(frontier)} "
            f"frontier action(s); selected {selected.action_id!r}; "
            f"min stakeholder utility = {bottom:.3f}"
        )


def _empty_outcome(
    *,
    solution: BargainingSolution,
    rationale: str,
) -> NegotiationOutcome:
    return NegotiationOutcome(
        selected=None,
        pareto_frontier=(),
        per_stakeholder_utility={},
        bargaining_solution=solution,
        rationale=rationale,
    )
