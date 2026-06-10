"""Behaviour of :class:`GRPOTrainer` + :class:`LookupRewardSimulator`."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import pytest

from brain_engine.cognition_loops.grpo import (
    GRPOMetrics,
    GRPOTrainer,
    LookupRewardSimulator,
)
from brain_engine.cognition_loops.models import MemoryOpKind
from brain_engine.cognition_loops.policy import (
    LogitWeights,
    MultinomialLogitPolicy,
)


def _policy() -> MultinomialLogitPolicy:
    return MultinomialLogitPolicy(weights=LogitWeights.zero())


def _build_table(
    rewards: Sequence[
        tuple[Mapping[str, float], MemoryOpKind, float]
    ],
) -> dict[
    tuple[frozenset[tuple[str, float]], MemoryOpKind], float
]:
    return {
        (frozenset(features.items()), kind): reward
        for features, kind, reward in rewards
    }


def test_lookup_simulator_returns_table_value() -> None:
    """Configured ``(features, op_kind)`` returns the stored reward."""
    table = _build_table(
        [
            ({"x": 1.0}, MemoryOpKind.ADD, 0.9),
            ({"x": 1.0}, MemoryOpKind.NOOP, 0.2),
        ]
    )
    sim = LookupRewardSimulator(table=table)
    assert sim.simulate(
        features={"x": 1.0}, op_kind=MemoryOpKind.ADD,
    ) == 0.9


def test_lookup_simulator_default_reward() -> None:
    """Missing keys fall back to the configured default."""
    sim = LookupRewardSimulator(
        table={},
        default_reward=0.42,
    )
    assert sim.simulate(
        features={"x": 0.0}, op_kind=MemoryOpKind.NOOP,
    ) == 0.42


def test_lookup_simulator_rejects_non_finite_default() -> None:
    """Inf / NaN default raises."""
    with pytest.raises(ValueError, match="default_reward"):
        LookupRewardSimulator(table={}, default_reward=math.inf)


def test_grpo_trainer_constructor_validation() -> None:
    """Non-positive lr / negative l2 raise."""
    sim = LookupRewardSimulator(table={})
    with pytest.raises(ValueError, match="learning_rate"):
        GRPOTrainer(
            policy=_policy(),
            simulator=sim,
            learning_rate=0.0,
        )
    with pytest.raises(ValueError, match="l2_lambda"):
        GRPOTrainer(
            policy=_policy(),
            simulator=sim,
            l2_lambda=-0.1,
        )


def test_grpo_step_advantage_sums_to_zero() -> None:
    """Group-relative advantages sum to ~0 (mean-baseline)."""
    table = _build_table(
        [
            ({"x": 1.0}, MemoryOpKind.ADD, 1.0),
            ({"x": 1.0}, MemoryOpKind.NOOP, 0.5),
            ({"x": 1.0}, MemoryOpKind.RETRIEVE, 0.0),
        ]
    )
    sim = LookupRewardSimulator(table=table)
    trainer = GRPOTrainer(policy=_policy(), simulator=sim)
    _, advantages = trainer.step({"x": 1.0})
    total = sum(advantages.values())
    assert abs(total) < 1e-9


def test_grpo_step_picks_highest_reward_advantage() -> None:
    """The op_kind with the highest reward has the largest advantage."""
    table = _build_table(
        [
            ({"x": 1.0}, MemoryOpKind.ADD, 1.0),
            ({"x": 1.0}, MemoryOpKind.DELETE, 0.1),
        ]
    )
    sim = LookupRewardSimulator(table=table)
    trainer = GRPOTrainer(policy=_policy(), simulator=sim)
    _, advantages = trainer.step({"x": 1.0})
    top_kind = max(advantages, key=lambda k: advantages[k])
    assert top_kind is MemoryOpKind.ADD


def test_grpo_train_objective_monotone_non_decreasing() -> None:
    """Objective stays non-decreasing in expectation across epochs."""
    table = _build_table(
        [
            ({"x": 1.0}, MemoryOpKind.ADD, 0.95),
            ({"x": 1.0}, MemoryOpKind.NOOP, 0.1),
        ]
    )
    sim = LookupRewardSimulator(table=table)
    policy = _policy()
    trainer = GRPOTrainer(
        policy=policy,
        simulator=sim,
        learning_rate=0.3,
    )

    # Capture per-epoch objective.
    objectives: list[float] = []
    for _ in range(20):
        objective, _ = trainer.step({"x": 1.0})
        objectives.append(objective)
    # Sanity: last objective >= first (strict on well-conditioned input).
    assert objectives[-1] >= objectives[0]
    # And ADD probability monotone-up over the run.
    probs = policy.probabilities({"x": 1.0})
    assert probs[MemoryOpKind.ADD] > 1.0 / len(MemoryOpKind)


def test_grpo_train_discriminates_two_contexts() -> None:
    """With one-hot features, GRPO learns conditional preferences."""
    # Context A: prefer ADD; Context B: prefer NOOP.
    table = _build_table(
        [
            ({"is_a": 1.0, "is_b": 0.0}, MemoryOpKind.ADD, 0.95),
            ({"is_a": 1.0, "is_b": 0.0}, MemoryOpKind.NOOP, 0.05),
            ({"is_a": 0.0, "is_b": 1.0}, MemoryOpKind.NOOP, 0.95),
            ({"is_a": 0.0, "is_b": 1.0}, MemoryOpKind.ADD, 0.05),
        ]
    )
    sim = LookupRewardSimulator(table=table)
    policy = _policy()
    trainer = GRPOTrainer(
        policy=policy,
        simulator=sim,
        learning_rate=0.3,
    )
    contexts = [
        {"is_a": 1.0, "is_b": 0.0},
        {"is_a": 0.0, "is_b": 1.0},
    ]
    trainer.train(contexts, iterations=80)
    # Context A should now pick ADD.
    assert policy.select(contexts[0]) is MemoryOpKind.ADD
    # Context B should pick NOOP.
    assert policy.select(contexts[1]) is MemoryOpKind.NOOP


def test_grpo_train_returns_metrics() -> None:
    """train() reports iterations / contexts_seen / mean_abs_advantage."""
    table = _build_table(
        [
            ({"x": 1.0}, MemoryOpKind.ADD, 1.0),
        ]
    )
    sim = LookupRewardSimulator(table=table)
    trainer = GRPOTrainer(policy=_policy(), simulator=sim)
    metrics = trainer.train(
        [{"x": 1.0}, {"x": 1.0}], iterations=3,
    )
    assert metrics.iterations_run == 3
    assert metrics.contexts_seen == 6
    assert metrics.mean_advantage_abs > 0.0


def test_grpo_train_empty_contexts_returns_zero() -> None:
    """Empty contexts list short-circuits to zero metrics."""
    trainer = GRPOTrainer(
        policy=_policy(),
        simulator=LookupRewardSimulator(table={}),
    )
    metrics = trainer.train([])
    assert metrics == GRPOMetrics(
        iterations_run=0,
        contexts_seen=0,
        mean_advantage_abs=0.0,
        final_objective=0.0,
    )


def test_grpo_train_invalid_iterations() -> None:
    trainer = GRPOTrainer(
        policy=_policy(),
        simulator=LookupRewardSimulator(table={}),
    )
    with pytest.raises(ValueError, match="iterations"):
        trainer.train([{"x": 1.0}], iterations=0)


def test_grpo_l2_shrinks_weights() -> None:
    """High L2 penalty keeps weight magnitudes smaller."""
    table = _build_table(
        [
            ({"x": 1.0}, MemoryOpKind.ADD, 1.0),
            ({"x": 1.0}, MemoryOpKind.NOOP, 0.0),
        ]
    )
    sim = LookupRewardSimulator(table=table)
    no_l2 = _policy()
    with_l2 = _policy()
    GRPOTrainer(
        policy=no_l2,
        simulator=sim,
        learning_rate=0.5,
    ).train([{"x": 1.0}], iterations=30)
    GRPOTrainer(
        policy=with_l2,
        simulator=sim,
        learning_rate=0.5,
        l2_lambda=2.0,
    ).train([{"x": 1.0}], iterations=30)
    no_l2_w = abs(no_l2.weights.weights[MemoryOpKind.ADD]["x"])
    with_l2_w = abs(
        with_l2.weights.weights[MemoryOpKind.ADD]["x"]
    )
    assert with_l2_w < no_l2_w
