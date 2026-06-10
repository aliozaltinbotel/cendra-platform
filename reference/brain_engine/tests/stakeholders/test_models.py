"""Value-object invariants for the stakeholder layer."""

from __future__ import annotations

import math

import pytest

from brain_engine.stakeholders.models import (
    DEFAULT_STAKEHOLDER_PRIORITIES,
    ActionCandidate,
    BargainingSolution,
    StakeholderId,
)


def test_five_default_stakeholders() -> None:
    """The default closed set ships exactly five roles."""
    assert len(StakeholderId) == 5


def test_three_bargaining_solutions() -> None:
    """Three solution concepts ship: nash / egalitarian / utilitarian."""
    assert len(BargainingSolution) == 3


def test_default_priorities_cover_every_role() -> None:
    """Default priority map spans the entire enum."""
    assert set(DEFAULT_STAKEHOLDER_PRIORITIES.keys()) == set(
        StakeholderId
    )


def test_default_priorities_regulator_top() -> None:
    """Regulator is the highest-priority stakeholder by default."""
    top_role = max(
        DEFAULT_STAKEHOLDER_PRIORITIES,
        key=lambda k: DEFAULT_STAKEHOLDER_PRIORITIES[k],
    )
    assert top_role is StakeholderId.REGULATOR


def test_action_candidate_rejects_empty_id() -> None:
    """``action_id`` must be non-empty."""
    with pytest.raises(ValueError, match="action_id"):
        ActionCandidate(action_id="", features={})


def test_action_candidate_rejects_non_finite_feature() -> None:
    """Infinity / NaN feature values are rejected."""
    with pytest.raises(ValueError, match="must be finite"):
        ActionCandidate(
            action_id="x",
            features={"price": math.inf},
        )
    with pytest.raises(ValueError, match="must be finite"):
        ActionCandidate(
            action_id="x",
            features={"price": math.nan},
        )


def test_action_candidate_rejects_non_numeric_feature() -> None:
    """Non-numeric feature values are rejected at construction."""
    with pytest.raises(TypeError, match="must be numeric"):
        ActionCandidate(
            action_id="x",
            features={"price": "cheap"},  # type: ignore[dict-item]
        )


def test_acceptable_to_default_passes() -> None:
    """No hard-veto → every stakeholder accepts the action."""
    candidate = ActionCandidate(
        action_id="x",
        features={"a": 0.5},
    )
    for role in StakeholderId:
        assert candidate.acceptable_to(role) is True


def test_acceptable_to_with_veto() -> None:
    """A vetoing stakeholder reads False; others read True."""
    candidate = ActionCandidate(
        action_id="x",
        features={"a": 0.5},
        hard_violations=frozenset({StakeholderId.NEIGHBOR}),
    )
    assert candidate.acceptable_to(StakeholderId.NEIGHBOR) is False
    assert candidate.acceptable_to(StakeholderId.GUEST) is True
