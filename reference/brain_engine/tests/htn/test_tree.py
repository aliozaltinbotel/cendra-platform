"""Behaviour of the LATS-style partial-plan tree adapter."""

from __future__ import annotations

import pytest

from brain_engine.htn.models import (
    HTNPlan,
    Operator,
    PreferredSolver,
)
from brain_engine.htn.tree import (
    PlanTreeNode,
    PlanTreeStatus,
    tree_from_plan,
)


def _op(name: str) -> Operator:
    return Operator(
        name=name,
        preferred_solver=PreferredSolver.LLM,
    )


def test_empty_plan_returns_evaluated_root() -> None:
    """Empty plan → single evaluated leaf root."""
    tree = tree_from_plan(HTNPlan(operators=()))
    assert tree.depth == 0
    assert tree.status is PlanTreeStatus.EVALUATED
    assert tree.is_leaf is True


def test_linear_plan_builds_chain() -> None:
    """N-operator plan builds an N-deep chain."""
    plan = HTNPlan(
        operators=(_op("a"), _op("b"), _op("c")),
    )
    root = tree_from_plan(plan)
    assert root.depth == 0
    assert len(root.operators) == 1
    assert root.operators[0].name == "a"
    leaf_depth = 0
    cursor = root
    while cursor.children:
        cursor = cursor.children[0]
        leaf_depth += 1
    assert leaf_depth == 2  # three operators → depth 0, 1, 2


def test_node_validation_rejects_negative_depth() -> None:
    """``depth`` < 0 is rejected."""
    with pytest.raises(ValueError, match="depth"):
        PlanTreeNode(depth=-1, operators=())


def test_node_validation_rejects_negative_visits() -> None:
    """``visits`` < 0 is rejected."""
    with pytest.raises(ValueError, match="visits"):
        PlanTreeNode(depth=0, operators=(), visits=-1)


def test_status_enum_values() -> None:
    """Four lifecycle states ship."""
    assert {s.value for s in PlanTreeStatus} == {
        "unexpanded",
        "expanded",
        "evaluated",
        "pruned",
    }
