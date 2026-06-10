"""End-to-end behaviour of :class:`StakeholderNegotiationEngine`."""

from __future__ import annotations

import pytest

from brain_engine.stakeholders.engine import (
    StakeholderNegotiationEngine,
)
from brain_engine.stakeholders.models import (
    ActionCandidate,
    BargainingSolution,
    StakeholderId,
)
from brain_engine.stakeholders.utility import (
    LinearUtilityFunction,
    UtilityRoster,
)


def _roster() -> UtilityRoster:
    return UtilityRoster(
        functions={
            StakeholderId.GUEST: LinearUtilityFunction(
                stakeholder=StakeholderId.GUEST,
                weights={"comfort": 1.0},
            ),
            StakeholderId.OWNER: LinearUtilityFunction(
                stakeholder=StakeholderId.OWNER,
                weights={"revenue": 1.0},
            ),
        }
    )


@pytest.fixture
def engine() -> StakeholderNegotiationEngine:
    return StakeholderNegotiationEngine(roster=_roster())


def test_picks_pareto_dominant_when_unanimous(
    engine: StakeholderNegotiationEngine,
) -> None:
    """A clearly best action wins under any solution concept."""
    candidates = [
        ActionCandidate(
            action_id="best",
            features={"comfort": 0.9, "revenue": 0.9},
        ),
        ActionCandidate(
            action_id="okay",
            features={"comfort": 0.5, "revenue": 0.5},
        ),
    ]
    for solution in BargainingSolution:
        outcome = engine.decide(
            candidates=candidates,
            solution=solution,
        )
        assert outcome.selected is not None
        assert outcome.selected.action_id == "best"
        assert "best" in [c.action_id for c in outcome.pareto_frontier]


def test_egalitarian_picks_balanced_action() -> None:
    """Egalitarian maximises the worst-off stakeholder's utility."""
    engine = StakeholderNegotiationEngine(roster=_roster())
    candidates = [
        ActionCandidate(
            action_id="extreme",
            features={"comfort": 1.0, "revenue": 0.1},
        ),
        ActionCandidate(
            action_id="balanced",
            features={"comfort": 0.5, "revenue": 0.5},
        ),
    ]
    outcome = engine.decide(
        candidates=candidates,
        solution=BargainingSolution.EGALITARIAN,
    )
    assert outcome.selected is not None
    assert outcome.selected.action_id == "balanced"


def test_nash_balances_via_product() -> None:
    """Nash-bargaining picks the best product-of-utilities action."""
    engine = StakeholderNegotiationEngine(roster=_roster())
    candidates = [
        ActionCandidate(
            action_id="extreme",
            features={"comfort": 1.0, "revenue": 0.1},
        ),  # product = 0.1
        ActionCandidate(
            action_id="balanced",
            features={"comfort": 0.5, "revenue": 0.5},
        ),  # product = 0.25
    ]
    outcome = engine.decide(
        candidates=candidates,
        solution=BargainingSolution.NASH,
    )
    assert outcome.selected is not None
    assert outcome.selected.action_id == "balanced"


def test_hard_veto_filters_candidate(
    engine: StakeholderNegotiationEngine,
) -> None:
    """Vetoed actions are excluded from consideration."""
    candidates = [
        ActionCandidate(
            action_id="vetoed",
            features={"comfort": 1.0, "revenue": 1.0},
            hard_violations=frozenset({StakeholderId.GUEST}),
        ),
        ActionCandidate(
            action_id="ok",
            features={"comfort": 0.5, "revenue": 0.5},
        ),
    ]
    outcome = engine.decide(candidates=candidates)
    assert outcome.selected is not None
    assert outcome.selected.action_id == "ok"


def test_all_vetoed_returns_empty_outcome(
    engine: StakeholderNegotiationEngine,
) -> None:
    """When every action is hard-vetoed the outcome is empty."""
    candidates = [
        ActionCandidate(
            action_id="x",
            features={"comfort": 0.9, "revenue": 0.9},
            hard_violations=frozenset({StakeholderId.GUEST}),
        ),
    ]
    outcome = engine.decide(candidates=candidates)
    assert outcome.selected is None
    assert outcome.pareto_frontier == ()
    assert outcome.per_stakeholder_utility == {}
    assert "hard-vetoed" in outcome.rationale


def test_outcome_carries_per_stakeholder_utility(
    engine: StakeholderNegotiationEngine,
) -> None:
    """The outcome records the chosen action's per-role utilities."""
    candidates = [
        ActionCandidate(
            action_id="balanced",
            features={"comfort": 0.6, "revenue": 0.7},
        ),
    ]
    outcome = engine.decide(candidates=candidates)
    assert outcome.selected is not None
    assert outcome.per_stakeholder_utility[StakeholderId.GUEST] == 0.6
    assert outcome.per_stakeholder_utility[StakeholderId.OWNER] == 0.7
