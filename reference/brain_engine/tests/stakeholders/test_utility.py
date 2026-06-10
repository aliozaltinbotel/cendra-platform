"""Behaviour of :class:`LinearUtilityFunction` and :class:`UtilityRoster`."""

from __future__ import annotations

import math

import pytest

from brain_engine.stakeholders.models import (
    ActionCandidate,
    StakeholderId,
)
from brain_engine.stakeholders.utility import (
    LinearUtilityFunction,
    UtilityRoster,
)


def _action(features: dict[str, float]) -> ActionCandidate:
    return ActionCandidate(action_id="x", features=features)


def test_linear_score_simple_weighted_sum() -> None:
    """Score equals weight·feature for a single-feature model."""
    fn = LinearUtilityFunction(
        stakeholder=StakeholderId.GUEST,
        weights={"comfort": 1.0},
    )
    assert fn.score(_action({"comfort": 0.7})) == 0.7


def test_linear_score_clamps_above_one() -> None:
    """Raw scores above 1.0 clamp to 1.0."""
    fn = LinearUtilityFunction(
        stakeholder=StakeholderId.OWNER,
        weights={"revenue": 2.0},
    )
    assert fn.score(_action({"revenue": 1.0})) == 1.0


def test_linear_score_clamps_at_floor() -> None:
    """Raw scores below ``floor`` clamp to floor."""
    fn = LinearUtilityFunction(
        stakeholder=StakeholderId.OWNER,
        weights={"revenue": 1.0},
        bias=-1.0,
        floor=0.2,
    )
    assert fn.score(_action({"revenue": 0.0})) == 0.2


def test_linear_score_missing_feature_is_zero() -> None:
    """Features absent from the candidate contribute zero."""
    fn = LinearUtilityFunction(
        stakeholder=StakeholderId.OWNER,
        weights={"revenue": 1.0, "comfort": 1.0},
    )
    assert fn.score(_action({"revenue": 0.5})) == 0.5


def test_linear_floor_validation() -> None:
    """Floor outside ``[0, 1]`` is rejected."""
    with pytest.raises(ValueError, match="floor"):
        LinearUtilityFunction(
            stakeholder=StakeholderId.OWNER,
            weights={},
            floor=1.5,
        )


def test_linear_weight_finite_validation() -> None:
    """Non-finite weights are rejected at construction."""
    with pytest.raises(ValueError, match="finite"):
        LinearUtilityFunction(
            stakeholder=StakeholderId.OWNER,
            weights={"revenue": math.inf},
        )


def test_roster_for_stakeholder_returns_function() -> None:
    """Registered stakeholder round-trips through ``for_stakeholder``."""
    fn = LinearUtilityFunction(
        stakeholder=StakeholderId.GUEST,
        weights={"comfort": 1.0},
    )
    roster = UtilityRoster(functions={StakeholderId.GUEST: fn})
    assert roster.for_stakeholder(StakeholderId.GUEST) is fn


def test_roster_unknown_stakeholder_raises() -> None:
    """Unknown stakeholder raises :class:`KeyError`."""
    fn = LinearUtilityFunction(
        stakeholder=StakeholderId.GUEST,
        weights={"comfort": 1.0},
    )
    roster = UtilityRoster(functions={StakeholderId.GUEST: fn})
    with pytest.raises(KeyError, match="no utility function"):
        roster.for_stakeholder(StakeholderId.NEIGHBOR)


def test_empty_roster_rejected() -> None:
    """An empty roster is rejected at construction."""
    with pytest.raises(ValueError, match="at least one"):
        UtilityRoster(functions={})
