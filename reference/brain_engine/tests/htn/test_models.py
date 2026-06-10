"""Invariants of HTN value objects."""

from __future__ import annotations

import pytest

from brain_engine.htn.models import (
    HTNPlan,
    Method,
    Operator,
    PreferredSolver,
    Task,
)


def _op(
    name: str = "op-1",
    *,
    cost: float = 1.0,
) -> Operator:
    return Operator(
        name=name,
        preferred_solver=PreferredSolver.LLM,
        cost=cost,
    )


def test_task_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        Task(name="")


def test_operator_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        Operator(
            name="",
            preferred_solver=PreferredSolver.LLM,
        )


def test_operator_rejects_negative_cost() -> None:
    with pytest.raises(ValueError, match="cost"):
        Operator(
            name="op",
            preferred_solver=PreferredSolver.LLM,
            cost=-0.1,
        )


def test_method_requires_subtasks() -> None:
    with pytest.raises(ValueError, match="subtasks"):
        Method(name="m", task="t", subtasks=())


def test_method_requires_task() -> None:
    with pytest.raises(ValueError, match="task"):
        Method(name="m", task="", subtasks=("a",))


def test_default_precondition_is_always_true() -> None:
    op = _op()
    assert op.applicable({}) is True


def test_operator_precondition_is_consulted() -> None:
    op = Operator(
        name="op",
        preferred_solver=PreferredSolver.LLM,
        precondition=lambda s: s.get("ready", False),
    )
    assert op.applicable({"ready": True}) is True
    assert op.applicable({"ready": False}) is False


def test_plan_total_cost_sums_operators() -> None:
    plan = HTNPlan(
        operators=(_op(cost=1.0), _op(cost=2.5), _op(cost=0.5)),
    )
    assert plan.total_cost == pytest.approx(4.0)


def test_plan_solver_mix_preserves_order() -> None:
    plan = HTNPlan(
        operators=(
            Operator(
                name="a",
                preferred_solver=PreferredSolver.UTILITY,
            ),
            Operator(
                name="b",
                preferred_solver=PreferredSolver.SMT,
            ),
        ),
    )
    assert plan.solver_mix == (
        PreferredSolver.UTILITY,
        PreferredSolver.SMT,
    )


def test_five_solver_kinds_ship() -> None:
    assert len(PreferredSolver) == 5
