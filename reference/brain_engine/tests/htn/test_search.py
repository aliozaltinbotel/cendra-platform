"""Behaviour of :class:`LATSSearch` (LATS MCTS expansion)."""

from __future__ import annotations

import random

import pytest

from brain_engine.htn.models import (
    HTNPlan,
    Method,
    Operator,
    PreferredSolver,
    Task,
    TaskNetwork,
)
from brain_engine.htn.search import (
    LATSSearch,
    SearchStatistics,
    default_reward,
)


def _op(
    name: str,
    *,
    cost: float = 1.0,
    solver: PreferredSolver = PreferredSolver.LLM,
) -> Operator:
    return Operator(
        name=name, preferred_solver=solver, cost=cost,
    )


def _three_method_network() -> TaskNetwork:
    """Network with three competing methods of different costs."""
    return TaskNetwork(
        operators={
            "send": _op("send", cost=1.0),
            "check": _op("check", cost=0.3),
            "log": _op("log", cost=0.1),
            "escalate": _op("escalate", cost=2.5),
        },
        methods={
            "resolve": (
                Method(
                    name="cheap",
                    task="resolve",
                    subtasks=("send", "log"),
                ),
                Method(
                    name="thorough",
                    task="resolve",
                    subtasks=("check", "send", "log"),
                ),
                Method(
                    name="expensive",
                    task="resolve",
                    subtasks=("escalate",),
                ),
            ),
        },
    )


@pytest.fixture
def network() -> TaskNetwork:
    return _three_method_network()


def test_default_reward_inverts_cost() -> None:
    """``default_reward`` is monotonically decreasing in cost."""
    cheap_plan = HTNPlan(operators=(_op("a", cost=1.0),))
    expensive_plan = HTNPlan(operators=(_op("a", cost=5.0),))
    assert default_reward(cheap_plan) > default_reward(expensive_plan)
    assert 0.0 < default_reward(cheap_plan) <= 1.0


def test_search_returns_min_cost_plan(
    network: TaskNetwork,
) -> None:
    """LATS converges on the minimum-cost method given enough budget."""
    search = LATSSearch(network=network, rng=random.Random(42))
    plan, stats = search.search(
        task=Task(name="resolve"),
        iterations=200,
    )
    assert plan.chosen_methods == ("cheap",)
    assert plan.total_cost == pytest.approx(1.1)
    assert stats.iterations_run == 200
    assert stats.failures == 0


def test_search_explores_all_alternatives(
    network: TaskNetwork,
) -> None:
    """UCB1 visits every applicable method at least once."""
    search = LATSSearch(network=network, rng=random.Random(7))
    _, stats = search.search(
        task=Task(name="resolve"),
        iterations=60,
    )
    by_method = stats.method_tally["resolve"]
    assert set(by_method) == {"cheap", "thorough", "expensive"}
    for method, visits in by_method.items():
        assert visits > 0, f"{method!r} not visited"


def test_search_invalid_iterations_rejected(
    network: TaskNetwork,
) -> None:
    """Non-positive ``iterations`` is rejected fail-fast."""
    search = LATSSearch(network=network)
    with pytest.raises(ValueError, match="iterations"):
        search.search(task=Task(name="resolve"), iterations=0)


def test_search_constructor_validation() -> None:
    """Negative exploration / non-positive max_depth fail fast."""
    network = _three_method_network()
    with pytest.raises(ValueError, match="exploration"):
        LATSSearch(network=network, exploration=-0.1)
    with pytest.raises(ValueError, match="max_depth"):
        LATSSearch(network=network, max_depth=0)


def test_search_falls_back_to_planner_when_one_method() -> None:
    """Single-method tasks resolve like the deterministic planner."""
    network = TaskNetwork(
        operators={"send": _op("send"), "log": _op("log")},
        methods={
            "do": (
                Method(
                    name="only",
                    task="do",
                    subtasks=("send", "log"),
                ),
            ),
        },
    )
    search = LATSSearch(network=network, rng=random.Random(1))
    plan, stats = search.search(
        task=Task(name="do"),
        iterations=10,
    )
    assert plan.chosen_methods == ("only",)
    assert [op.name for op in plan.operators] == ["send", "log"]
    # No branching → method_tally for ``do`` is empty (the engine
    # only records stats when more than one candidate competes).
    assert "do" not in stats.method_tally


def test_search_returns_empty_plan_when_unreachable() -> None:
    """Task with no operator + no method → empty plan + failures > 0."""
    network = TaskNetwork(operators={}, methods={})
    search = LATSSearch(network=network, rng=random.Random(0))
    plan, stats = search.search(
        task=Task(name="unreachable"),
        iterations=4,
    )
    assert plan.operators == ()
    assert stats.failures == 4
    assert stats.plan_reward == 0.0


def test_custom_reward_function_is_used() -> None:
    """A non-default reward function influences the chosen method."""
    network = _three_method_network()

    # Reward = +1 for "expensive", 0 otherwise — should win.
    def biased_reward(plan: HTNPlan) -> float:
        if "escalate" in [op.name for op in plan.operators]:
            return 1.0
        return 0.0

    search = LATSSearch(
        network=network,
        reward_fn=biased_reward,
        rng=random.Random(13),
    )
    plan, _ = search.search(
        task=Task(name="resolve"),
        iterations=120,
    )
    assert "escalate" in [op.name for op in plan.operators]


def test_search_statistics_is_immutable() -> None:
    """:class:`SearchStatistics` is a frozen dataclass."""
    stats = SearchStatistics(
        iterations_run=10,
        plan_total_cost=1.0,
        plan_reward=0.5,
    )
    with pytest.raises((AttributeError, TypeError)):
        stats.iterations_run = 99  # type: ignore[misc]
