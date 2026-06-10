"""Behaviour of :class:`SGDTrainer`."""

from __future__ import annotations

import math

import pytest

from brain_engine.cognition_loops.models import MemoryOpKind
from brain_engine.cognition_loops.policy import (
    LogitWeights,
    MultinomialLogitPolicy,
)
from brain_engine.cognition_loops.trainer import (
    SGDTrainer,
    TrainingMetrics,
    TrainingSample,
    iter_samples,
)


def _policy() -> MultinomialLogitPolicy:
    return MultinomialLogitPolicy(weights=LogitWeights.zero())


def test_sample_rejects_non_finite_reward() -> None:
    with pytest.raises(ValueError, match="reward"):
        TrainingSample(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=math.inf,
        )


def test_sample_rejects_non_finite_feature() -> None:
    with pytest.raises(ValueError, match="must be finite"):
        TrainingSample(
            features={"x": math.nan},
            chosen_kind=MemoryOpKind.ADD,
            reward=0.5,
        )


def test_trainer_constructor_validation() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="learning_rate"):
        SGDTrainer(policy=policy, learning_rate=0.0)
    with pytest.raises(ValueError, match="l2_lambda"):
        SGDTrainer(policy=policy, l2_lambda=-0.1)


def test_step_pushes_probability_toward_chosen() -> None:
    """One SGD step raises P(chosen | features) under positive reward."""
    policy = _policy()
    trainer = SGDTrainer(policy=policy, learning_rate=1.0)
    features = {"f": 1.0}
    before = policy.probabilities(features)[MemoryOpKind.ADD]
    trainer.step(
        TrainingSample(
            features=features,
            chosen_kind=MemoryOpKind.ADD,
            reward=1.0,
        )
    )
    after = policy.probabilities(features)[MemoryOpKind.ADD]
    assert after > before


def test_step_negative_reward_pushes_chosen_down() -> None:
    """Negative reward should *lower* P(chosen)."""
    policy = _policy()
    trainer = SGDTrainer(policy=policy, learning_rate=1.0)
    features = {"f": 1.0}
    before = policy.probabilities(features)[MemoryOpKind.ADD]
    trainer.step(
        TrainingSample(
            features=features,
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.0,
        )
    )
    after = policy.probabilities(features)[MemoryOpKind.ADD]
    assert after < before


def test_train_returns_metrics() -> None:
    """train() reports epochs / samples_seen / final_loss."""
    policy = _policy()
    trainer = SGDTrainer(policy=policy, learning_rate=0.1)
    samples = [
        TrainingSample(
            features={"a": 1.0},
            chosen_kind=MemoryOpKind.NOOP,
            reward=1.0,
        ),
    ] * 4
    metrics = trainer.train(samples, epochs=3)
    assert metrics.epochs_run == 3
    assert metrics.samples_seen == 12
    assert metrics.final_loss >= 0.0


def test_train_empty_samples_returns_zero_metrics() -> None:
    """Empty sample list → zero-metrics record without errors."""
    metrics = SGDTrainer(policy=_policy()).train([])
    assert metrics == TrainingMetrics(
        epochs_run=0,
        samples_seen=0,
        final_loss=0.0,
    )


def test_train_invalid_epochs_rejected() -> None:
    """epochs < 1 is rejected fail-fast."""
    trainer = SGDTrainer(policy=_policy())
    with pytest.raises(ValueError, match="epochs"):
        trainer.train([], epochs=0)


def test_iter_samples_helper() -> None:
    """``iter_samples`` builds a list of :class:`TrainingSample`."""
    samples = iter_samples([
        ({"x": 1.0}, MemoryOpKind.ADD, 0.5),
        ({"y": 2.0}, MemoryOpKind.DELETE, -0.3),
    ])
    assert len(samples) == 2
    assert samples[0].chosen_kind is MemoryOpKind.ADD
    assert samples[1].reward == -0.3


def test_train_learns_separable_pattern() -> None:
    """End-to-end: trained policy classifies a synthetic 2-class split."""
    policy = _policy()
    trainer = SGDTrainer(policy=policy, learning_rate=0.5)
    # When x=1 → ADD; when x=-1 → DELETE
    samples = iter_samples(
        [({"x": 1.0}, MemoryOpKind.ADD, 1.0)] * 10
        + [({"x": -1.0}, MemoryOpKind.DELETE, 1.0)] * 10
    )
    trainer.train(samples, epochs=50)
    assert policy.select({"x": 1.0}) is MemoryOpKind.ADD
    assert policy.select({"x": -1.0}) is MemoryOpKind.DELETE


def test_l2_pulls_weights_toward_zero() -> None:
    """High L2 penalty shrinks updates toward the origin."""
    policy_no_l2 = _policy()
    trainer_no_l2 = SGDTrainer(
        policy=policy_no_l2,
        learning_rate=0.5,
    )
    policy_with_l2 = _policy()
    trainer_with_l2 = SGDTrainer(
        policy=policy_with_l2,
        learning_rate=0.5,
        l2_lambda=2.0,
    )
    sample = TrainingSample(
        features={"x": 1.0},
        chosen_kind=MemoryOpKind.ADD,
        reward=1.0,
    )
    for _ in range(20):
        trainer_no_l2.step(sample)
        trainer_with_l2.step(sample)
    no_l2 = abs(
        policy_no_l2.weights.weights[MemoryOpKind.ADD]["x"]
    )
    with_l2 = abs(
        policy_with_l2.weights.weights[MemoryOpKind.ADD]["x"]
    )
    assert with_l2 < no_l2
