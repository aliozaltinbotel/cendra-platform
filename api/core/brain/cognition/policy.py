"""Multinomial-logit policy approximating Memory-R1 (M18).

Brain Engine's M14 :class:`InteractionProtocol` consults a
:class:`MemoryOp` whose ``kind`` is decided by the upstream
Memory-R1 policy.  v0.1 of M14 left the policy abstract — any
caller could mint a :class:`MemoryOp` with hand-picked ``kind``.
This module ships a *trainable* policy: a softmax over
:class:`MemoryOpKind` parameterised by per-feature weights and
per-kind biases.

What this is:
  * A pure-Python multinomial-logit classifier over a free-form
    feature dict; outputs probabilities per
    :class:`MemoryOpKind`; supports argmax selection.
  * Trainable via the SGD trainer in
    :mod:`core.brain.cognition.trainer`.

What this is NOT:
  * Full GRPO RL.  GRPO requires sampled trajectories +
    advantage estimates; this v0.1 ships supervised policy
    learning from logged ``(features, chosen_kind, reward)``
    triples.  The supervised version is the production middle
    ground — it captures the reward signal as a per-sample
    weight on the cross-entropy loss without the trajectory-
    sampling infrastructure.

References:
    Yan et al. (2025) — *Memory-R1*.  arXiv:2508.19828.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from core.brain.cognition.models import MemoryOpKind

__all__ = [
    "DEFAULT_L2_LAMBDA",
    "DEFAULT_LEARNING_RATE",
    "LogitWeights",
    "MultinomialLogitPolicy",
    "softmax",
]


DEFAULT_LEARNING_RATE: Final[float] = 0.05
DEFAULT_L2_LAMBDA: Final[float] = 0.0


@dataclass(slots=True)
class LogitWeights:
    """Trainable parameters of the multinomial-logit policy.

    Attributes:
        bias: Per-kind constant offset.
        weights: ``kind -> feature_name -> weight``.  Missing
            features default to zero contribution.

    The class is *mutable* by design — the SGD trainer updates
    these tables in place.  Callers wanting an immutable
    snapshot can call :meth:`copy`.
    """

    bias: dict[MemoryOpKind, float]
    weights: dict[MemoryOpKind, dict[str, float]]

    @classmethod
    def zero(cls) -> LogitWeights:
        """Return weights initialised to zero for every op kind."""
        bias: dict[MemoryOpKind, float] = dict.fromkeys(MemoryOpKind, 0.0)
        weights: dict[MemoryOpKind, dict[str, float]] = {kind: {} for kind in MemoryOpKind}
        return cls(bias=bias, weights=weights)

    def copy(self) -> LogitWeights:
        """Return a deep-copied immutable-by-convention snapshot."""
        return LogitWeights(
            bias=dict(self.bias),
            weights={kind: dict(per_feature) for kind, per_feature in self.weights.items()},
        )

    def kinds(self) -> tuple[MemoryOpKind, ...]:
        """Return the kinds the parameter table covers."""
        return tuple(self.bias.keys())


def softmax(
    scores: Mapping[MemoryOpKind, float],
) -> dict[
    MemoryOpKind,
    float,
]:
    """Numerically-stable softmax over a kind → score mapping.

    Returns a fresh dict; never mutates the input.
    """
    if not scores:
        raise ValueError("scores must be non-empty")
    top = max(scores.values())
    exps = {kind: math.exp(score - top) for kind, score in scores.items()}
    total = sum(exps.values())
    if total == 0.0:
        # Degenerate — return uniform.
        n = len(scores)
        return dict.fromkeys(scores, 1.0 / n)
    return {kind: value / total for kind, value in exps.items()}


@dataclass(frozen=True, slots=True)
class _PolicyDecision:
    """Internal probability snapshot returned by the policy."""

    probabilities: Mapping[MemoryOpKind, float]
    argmax: MemoryOpKind


class MultinomialLogitPolicy:
    """Softmax over :class:`MemoryOpKind` given a feature dict.

    The policy is read-only with respect to the weights; the
    trainer updates the weight table.  Construction freezes the
    reference but not the table contents.
    """

    def __init__(self, weights: LogitWeights) -> None:
        self._weights = weights

    @property
    def weights(self) -> LogitWeights:
        """Return the live weight table."""
        return self._weights

    def logits(
        self,
        features: Mapping[str, float],
    ) -> dict[MemoryOpKind, float]:
        """Return per-kind raw scores ``bias + Σ w_f · x_f``."""
        out: dict[MemoryOpKind, float] = {}
        for kind in self._weights.kinds():
            score = self._weights.bias[kind]
            per_feature = self._weights.weights[kind]
            for feature_name, value in features.items():
                weight = per_feature.get(feature_name, 0.0)
                score += weight * value
            out[kind] = score
        return out

    def probabilities(
        self,
        features: Mapping[str, float],
    ) -> dict[MemoryOpKind, float]:
        """Return calibrated softmax probabilities per kind."""
        return softmax(self.logits(features))

    def select(
        self,
        features: Mapping[str, float],
    ) -> MemoryOpKind:
        """Return the highest-probability :class:`MemoryOpKind`."""
        probs = self.probabilities(features)
        # ``max`` over (probability, deterministic-tie-breaker)
        # so two equal-prob kinds resolve in enum-declaration
        # order — keeps the output reproducible.
        order = list(MemoryOpKind)
        return max(
            probs,
            key=lambda k: (probs[k], -order.index(k)),
        )
