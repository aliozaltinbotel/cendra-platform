"""SGD trainer for the Memory-R1 multinomial-logit policy (M18).

Closes the M14 deferred TODO ("GRPO trainer wiring") *partially*.
This trainer ships supervised cross-entropy learning weighted by
the per-sample reward; full GRPO RL with advantage estimates
remains a v1.0 follow-up.

Algorithm per training sample ``(features, chosen_kind, reward)``:

    logits[k]      = bias[k] + Σ_f weights[k][f] · features[f]
    probs[k]       = softmax(logits)[k]
    grad bias[k]   = reward · (1{k = chosen} − probs[k])
    grad w[k][f]   = reward · features[f] · (1{k = chosen} − probs[k])
    bias[k]       += lr · grad bias[k] − lr · l2 · bias[k]
    w[k][f]       += lr · grad w[k][f] − lr · l2 · w[k][f]

Reward acts as the loss weight; positive rewards pull the chosen
kind up, negative rewards push it down.  L2 regularisation is
configurable but defaults to zero so callers see vanilla SGD until
they opt in.

Pure-Python; no torch / NumPy / autograd.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from core.brain.cognition.models import MemoryOpKind
from core.brain.cognition.policy import (
    DEFAULT_L2_LAMBDA,
    DEFAULT_LEARNING_RATE,
    LogitWeights,
    MultinomialLogitPolicy,
    softmax,
)

__all__ = [
    "SGDTrainer",
    "TrainingMetrics",
    "TrainingSample",
]


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TrainingSample:
    """One ``(features, chosen, reward)`` triple.

    Attributes:
        features: Free-form feature map (numeric values).
        chosen_kind: The :class:`MemoryOpKind` that ran.
        reward: Caller-supplied finite reward; positive values
            pull ``chosen_kind`` up, negative push down.
    """

    features: Mapping[str, float]
    chosen_kind: MemoryOpKind
    reward: float

    def __post_init__(self) -> None:
        if self.reward != self.reward or self.reward in (
            float("inf"),
            float("-inf"),
        ):
            raise ValueError("reward must be finite")
        for name, value in self.features.items():
            if value != value or value in (
                float("inf"),
                float("-inf"),
            ):
                raise ValueError(f"feature {name!r} must be finite")


@dataclass(frozen=True, slots=True)
class TrainingMetrics:
    """Summary of one training run.

    Attributes:
        epochs_run: Number of epochs completed.
        samples_seen: Total ``len(samples) * epochs_run``.
        final_loss: Sum of weighted negative-log-likelihood over
            the last epoch.
    """

    epochs_run: int
    samples_seen: int
    final_loss: float


class SGDTrainer:
    """Train a :class:`MultinomialLogitPolicy` via reward-weighted SGD."""

    def __init__(
        self,
        *,
        policy: MultinomialLogitPolicy,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        l2_lambda: float = DEFAULT_L2_LAMBDA,
    ) -> None:
        if learning_rate <= 0.0:
            raise ValueError("learning_rate must be positive")
        if l2_lambda < 0.0:
            raise ValueError("l2_lambda must be non-negative")
        self._policy = policy
        self._lr = learning_rate
        self._l2 = l2_lambda

    def step(self, sample: TrainingSample) -> float:
        """Apply one SGD update; return the per-sample loss."""
        weights = self._policy.weights
        probs = softmax(self._policy.logits(sample.features))
        loss = self._loss(probs=probs, sample=sample)
        for kind in weights.kinds():
            indicator = 1.0 if kind is sample.chosen_kind else 0.0
            error = indicator - probs[kind]
            scaled = self._lr * sample.reward * error
            new_bias = weights.bias[kind] + scaled - self._lr * self._l2 * weights.bias[kind]
            weights.bias[kind] = new_bias
            self._update_feature_weights(
                kind=kind,
                features=sample.features,
                scaled=scaled,
                weights=weights,
            )
        return loss

    def train(
        self,
        samples: Sequence[TrainingSample],
        *,
        epochs: int = 1,
    ) -> TrainingMetrics:
        """Run multi-epoch SGD over ``samples`` (in-given order)."""
        if epochs < 1:
            raise ValueError("epochs must be >= 1")
        if not samples:
            return TrainingMetrics(
                epochs_run=0,
                samples_seen=0,
                final_loss=0.0,
            )
        last_epoch_loss = 0.0
        for epoch in range(epochs):
            last_epoch_loss = 0.0
            for sample in samples:
                last_epoch_loss += self.step(sample)
            logger.info(
                "policy.epoch epoch=%s loss=%s",
                epoch + 1,
                round(last_epoch_loss, 6),
            )
        return TrainingMetrics(
            epochs_run=epochs,
            samples_seen=epochs * len(samples),
            final_loss=last_epoch_loss,
        )

    # ── internals ─────────────────────────────────────────── #

    def _update_feature_weights(
        self,
        *,
        kind: MemoryOpKind,
        features: Mapping[str, float],
        scaled: float,
        weights: LogitWeights,
    ) -> None:
        per_feature = weights.weights[kind]
        for name, value in features.items():
            current = per_feature.get(name, 0.0)
            grad = scaled * value
            penalty = self._lr * self._l2 * current
            per_feature[name] = current + grad - penalty

    @staticmethod
    def _loss(
        *,
        probs: Mapping[MemoryOpKind, float],
        sample: TrainingSample,
    ) -> float:
        """Reward-weighted negative log-likelihood."""
        prob_chosen = max(probs[sample.chosen_kind], 1e-12)
        from math import log

        return -sample.reward * log(prob_chosen)


def iter_samples(
    rows: Iterable[tuple[Mapping[str, float], MemoryOpKind, float]],
) -> list[TrainingSample]:
    """Convenience: build :class:`TrainingSample` list from raw tuples."""
    return [
        TrainingSample(
            features=features,
            chosen_kind=kind,
            reward=reward,
        )
        for features, kind, reward in rows
    ]
