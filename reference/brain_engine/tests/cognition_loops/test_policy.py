"""Behaviour of :class:`MultinomialLogitPolicy` + helpers."""

from __future__ import annotations

import math

import pytest

from brain_engine.cognition_loops.models import MemoryOpKind
from brain_engine.cognition_loops.policy import (
    LogitWeights,
    MultinomialLogitPolicy,
    softmax,
)


def test_logit_weights_zero_covers_every_kind() -> None:
    """Factory zero-init covers every :class:`MemoryOpKind`."""
    weights = LogitWeights.zero()
    assert set(weights.bias.keys()) == set(MemoryOpKind)
    assert set(weights.weights.keys()) == set(MemoryOpKind)
    for bias in weights.bias.values():
        assert bias == 0.0


def test_logit_weights_copy_is_independent() -> None:
    """``copy`` returns a deep duplicate."""
    weights = LogitWeights.zero()
    weights.bias[MemoryOpKind.ADD] = 1.0
    weights.weights[MemoryOpKind.ADD]["feat"] = 2.0
    snapshot = weights.copy()
    weights.bias[MemoryOpKind.ADD] = 99.0
    weights.weights[MemoryOpKind.ADD]["feat"] = 99.0
    assert snapshot.bias[MemoryOpKind.ADD] == 1.0
    assert snapshot.weights[MemoryOpKind.ADD]["feat"] == 2.0


def test_softmax_empty_input_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        softmax({})


def test_softmax_uniform_for_equal_scores() -> None:
    """Equal scores → uniform probability."""
    scores = {kind: 1.0 for kind in MemoryOpKind}
    probs = softmax(scores)
    expected = 1.0 / len(MemoryOpKind)
    for p in probs.values():
        assert p == pytest.approx(expected)


def test_softmax_sums_to_one() -> None:
    """Probabilities sum to 1.0 (within FP epsilon)."""
    scores = {
        MemoryOpKind.ADD: 0.5,
        MemoryOpKind.NOOP: -0.3,
        MemoryOpKind.RETRIEVE: 0.1,
        MemoryOpKind.SUMMARIZE: 1.2,
        MemoryOpKind.UPDATE: 0.0,
        MemoryOpKind.DELETE: -2.0,
    }
    probs = softmax(scores)
    assert sum(probs.values()) == pytest.approx(1.0)


def test_softmax_numerically_stable_for_large_values() -> None:
    """Large positive scores do not overflow."""
    scores = {kind: 1000.0 for kind in MemoryOpKind}
    probs = softmax(scores)
    for p in probs.values():
        assert math.isfinite(p)


def test_policy_logits_use_bias_and_weights() -> None:
    """logits = bias + Σ w_f · x_f for each kind."""
    weights = LogitWeights.zero()
    weights.bias[MemoryOpKind.ADD] = 0.5
    weights.weights[MemoryOpKind.ADD]["x"] = 2.0
    weights.weights[MemoryOpKind.ADD]["y"] = -1.0
    policy = MultinomialLogitPolicy(weights=weights)
    out = policy.logits({"x": 3.0, "y": 1.0})
    # ADD: 0.5 + 2*3 + (-1)*1 = 5.5
    assert out[MemoryOpKind.ADD] == pytest.approx(5.5)
    # Other kinds: zero bias + zero weights → 0
    for kind in MemoryOpKind:
        if kind is MemoryOpKind.ADD:
            continue
        assert out[kind] == 0.0


def test_policy_probabilities_via_softmax() -> None:
    """probabilities = softmax(logits)."""
    weights = LogitWeights.zero()
    weights.bias[MemoryOpKind.NOOP] = 5.0
    policy = MultinomialLogitPolicy(weights=weights)
    probs = policy.probabilities({})
    # NOOP dominates after softmax.
    assert probs[MemoryOpKind.NOOP] > 0.9


def test_policy_select_returns_argmax() -> None:
    """select() picks the highest-probability kind."""
    weights = LogitWeights.zero()
    weights.bias[MemoryOpKind.DELETE] = 10.0
    policy = MultinomialLogitPolicy(weights=weights)
    assert policy.select({}) is MemoryOpKind.DELETE


def test_policy_select_tie_breaks_by_enum_order() -> None:
    """Equal probabilities → enum declaration order wins."""
    weights = LogitWeights.zero()
    policy = MultinomialLogitPolicy(weights=weights)
    # All zero-bias / zero-weight → uniform.  Earlier enum value
    # wins by the tie-breaker.
    assert policy.select({}) is next(iter(MemoryOpKind))


def test_policy_missing_feature_contributes_zero() -> None:
    """Features absent from the query contribute zero to logits."""
    weights = LogitWeights.zero()
    weights.weights[MemoryOpKind.ADD]["only_this"] = 100.0
    policy = MultinomialLogitPolicy(weights=weights)
    # Query with a different feature name — ADD logit stays 0.
    out = policy.logits({"unrelated": 50.0})
    assert out[MemoryOpKind.ADD] == 0.0
