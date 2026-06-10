"""Pareto-frontier helpers."""

from __future__ import annotations

import pytest

from brain_engine.stakeholders.models import (
    ActionCandidate,
    StakeholderId,
)
from brain_engine.stakeholders.pareto import (
    dominates,
    pareto_frontier,
)


def test_dominates_strictly_better() -> None:
    """``a`` strictly Pareto-dominates ``b`` when ≥ on all + > on one."""
    a = {
        StakeholderId.GUEST: 0.8,
        StakeholderId.OWNER: 0.7,
    }
    b = {
        StakeholderId.GUEST: 0.7,
        StakeholderId.OWNER: 0.7,
    }
    assert dominates(a, b) is True
    assert dominates(b, a) is False


def test_dominates_equal_returns_false() -> None:
    """Identical utility maps do not dominate each other."""
    a = {
        StakeholderId.GUEST: 0.5,
        StakeholderId.OWNER: 0.5,
    }
    assert dominates(a, dict(a)) is False


def test_dominates_mismatched_keys_raises() -> None:
    """Maps with different keys raise :class:`KeyError`."""
    with pytest.raises(KeyError, match="same stakeholder"):
        dominates(
            {StakeholderId.GUEST: 0.5},
            {StakeholderId.OWNER: 0.5},
        )


def test_pareto_frontier_drops_dominated() -> None:
    """Dominated candidates are excluded from the frontier."""
    candidates = [
        ActionCandidate(action_id="a", features={}),
        ActionCandidate(action_id="b", features={}),
        ActionCandidate(action_id="c", features={}),
    ]
    utilities = {
        "a": {
            StakeholderId.GUEST: 0.8,
            StakeholderId.OWNER: 0.5,
        },
        "b": {
            StakeholderId.GUEST: 0.6,
            StakeholderId.OWNER: 0.4,
        },  # dominated by a
        "c": {
            StakeholderId.GUEST: 0.4,
            StakeholderId.OWNER: 0.9,
        },
    }
    frontier = pareto_frontier(candidates, utilities)
    ids = [c.action_id for c in frontier]
    assert "a" in ids
    assert "c" in ids
    assert "b" not in ids


def test_pareto_frontier_preserves_input_order() -> None:
    """Frontier output keeps input candidate order."""
    candidates = [
        ActionCandidate(action_id="z", features={}),
        ActionCandidate(action_id="a", features={}),
    ]
    utilities = {
        "z": {StakeholderId.GUEST: 0.5},
        "a": {StakeholderId.GUEST: 0.4},
    }
    frontier = pareto_frontier(candidates, utilities)
    assert [c.action_id for c in frontier] == ["z"]
