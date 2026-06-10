"""End-to-end behaviour of :class:`HTNPlanner`."""

from __future__ import annotations

import pytest

from brain_engine.htn.models import (
    Method,
    Operator,
    PreferredSolver,
    Task,
    TaskNetwork,
)
from brain_engine.htn.planner import (
    HTNPlanFailure,
    HTNPlanner,
)


def _op(
    name: str,
    *,
    solver: PreferredSolver = PreferredSolver.LLM,
    cost: float = 1.0,
) -> Operator:
    return Operator(
        name=name,
        preferred_solver=solver,
        cost=cost,
    )


def _net(
    *,
    operators: dict[str, Operator],
    methods: dict[str, tuple[Method, ...]],
) -> TaskNetwork:
    return TaskNetwork(operators=operators, methods=methods)


def test_single_operator_plan() -> None:
    """Task with a direct operator decomposes to one step."""
    network = _net(
        operators={"send_message": _op("send_message")},
        methods={},
    )
    planner = HTNPlanner(network=network)
    plan = planner.plan(task=Task(name="send_message"))
    assert len(plan.operators) == 1
    assert plan.operators[0].name == "send_message"


def test_method_decomposes_to_subtasks() -> None:
    """A method expands to its ordered subtasks."""
    network = _net(
        operators={
            "a": _op("a"),
            "b": _op("b"),
            "c": _op("c"),
        },
        methods={
            "compound": (
                Method(
                    name="m1",
                    task="compound",
                    subtasks=("a", "b", "c"),
                ),
            ),
        },
    )
    planner = HTNPlanner(network=network)
    plan = planner.plan(task=Task(name="compound"))
    assert [op.name for op in plan.operators] == ["a", "b", "c"]
    assert plan.chosen_methods == ("m1",)


def test_method_precondition_picks_correct_alternative() -> None:
    """First applicable method wins; later alternatives skipped."""
    network = _net(
        operators={"warn": _op("warn"), "escalate": _op("escalate")},
        methods={
            "respond": (
                Method(
                    name="warn_only",
                    task="respond",
                    subtasks=("warn",),
                    precondition=(
                        lambda s: s.get("prior_count", 0) == 0
                    ),
                ),
                Method(
                    name="escalate",
                    task="respond",
                    subtasks=("escalate",),
                    precondition=(
                        lambda s: s.get("prior_count", 0) > 0
                    ),
                ),
            ),
        },
    )
    planner = HTNPlanner(network=network)
    plan = planner.plan(
        task=Task(name="respond"),
        state={"prior_count": 0},
    )
    assert plan.chosen_methods == ("warn_only",)
    plan_repeat = planner.plan(
        task=Task(name="respond"),
        state={"prior_count": 2},
    )
    assert plan_repeat.chosen_methods == ("escalate",)


def test_unapplicable_operator_falls_back_to_method() -> None:
    """Operator with failing precondition is skipped for method."""
    network = _net(
        operators={
            "send_msg": Operator(
                name="send_msg",
                preferred_solver=PreferredSolver.LLM,
                precondition=lambda s: s.get("channel_open", False),
            ),
        },
        methods={
            "send_msg": (
                Method(
                    name="fallback",
                    task="send_msg",
                    subtasks=("noop",),
                ),
            ),
            "noop": (),
        },
        # Add a noop operator
    )
    network = TaskNetwork(
        operators={
            **network.operators,
            "noop": _op("noop", cost=0.0),
        },
        methods=network.methods,
    )
    planner = HTNPlanner(network=network)
    plan = planner.plan(
        task=Task(name="send_msg"),
        state={"channel_open": False},
    )
    assert [op.name for op in plan.operators] == ["noop"]


def test_unknown_task_raises() -> None:
    """Task with neither operator nor method raises."""
    planner = HTNPlanner(network=_net(operators={}, methods={}))
    with pytest.raises(HTNPlanFailure, match="no applicable"):
        planner.plan(task=Task(name="missing"))


def test_max_depth_guards_cycles() -> None:
    """Infinite recursion via cyclic methods raises before stack overflow."""
    network = _net(
        operators={},
        methods={
            "loop": (
                Method(
                    name="cycle",
                    task="loop",
                    subtasks=("loop",),
                ),
            ),
        },
    )
    planner = HTNPlanner(network=network, max_depth=4)
    with pytest.raises(HTNPlanFailure, match="max_depth"):
        planner.plan(task=Task(name="loop"))


def test_planner_max_depth_validation() -> None:
    """``max_depth`` < 1 is rejected at construction."""
    with pytest.raises(ValueError, match="max_depth"):
        HTNPlanner(
            network=_net(operators={}, methods={}),
            max_depth=0,
        )
