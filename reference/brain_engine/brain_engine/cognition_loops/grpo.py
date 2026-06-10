"""Group Relative Policy Optimization for Memory-R1 (M20).

Closes M14's deferred ``GRPO trainer`` TODO on the v0.1 level —
the policy + gradient mechanism is fully implemented; what is
left for v1.0 is wiring a real environment behind the
:class:`RewardSimulator` Protocol (today the caller plugs a
synthetic or replay-backed reward function).

Algorithm
---------

For each training context ``features``:

1. Enumerate every :class:`MemoryOpKind` (the action space is
   small and closed — 6 values — so we don't need stochastic
   sampling; we evaluate every action directly).
2. Ask the :class:`RewardSimulator` for the realised reward of
   each action under the same context.
3. Compute the group-relative advantage
   ``A_k = r_k − mean(r_k for all k)``.
4. Update the policy parameters with the policy-gradient form
   for a multinomial-logit policy.  For an enumerable action
   space with a *mean baseline*, the score-function gradient
   collapses to::

       ∇bias[k]      = A_k
       ∇weights[k][f] = features[f] · A_k

   (sum over k of A_k vanishes because the baseline is the
   group mean — derivation in the module docstring of
   :mod:`brain_engine.cognition_loops.trainer`).

Honest scope
------------

  * Pure-Python; no torch.  Builds on the
    :class:`MultinomialLogitPolicy` + :class:`LogitWeights` from
    M18.
  * Reward source is pluggable via :class:`RewardSimulator`.
    v0.1 ships :class:`LookupRewardSimulator` for tests + replay
    workloads; v1.0 wires a real rollout-based simulator.
  * Optimiser is plain SGD with optional L2.  Momentum / Adam
    is intentionally omitted to keep the moat-claim small and
    easy to read.

Reference: Yan et al. (2025) — *Memory-R1*.  arXiv:2508.19828.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

import structlog

from brain_engine.cognition_loops.models import MemoryOpKind
from brain_engine.cognition_loops.policy import (
    DEFAULT_L2_LAMBDA,
    DEFAULT_LEARNING_RATE,
    LogitWeights,
    MultinomialLogitPolicy,
)


__all__ = [
    "DEFAULT_GRPO_LEARNING_RATE",
    "GRPOMetrics",
    "GRPOTrainer",
    "LookupRewardSimulator",
    "RewardSimulator",
]


DEFAULT_GRPO_LEARNING_RATE: Final[float] = 0.1


logger = structlog.get_logger(__name__)


class RewardSimulator(Protocol):
    """Per-(features, op_kind) reward source.

    Implementations may:

    - read from a replay buffer keyed off ``(features, op_kind)``;
    - call out to a Property Twin (M13 / M17) for an imagined
      rollout reward;
    - hit a production env directly during shadow-mode training.

    The Protocol intentionally hides which path; the trainer
    just asks for a finite reward.
    """

    def simulate(
        self,
        *,
        features: Mapping[str, float],
        op_kind: MemoryOpKind,
    ) -> float:
        """Return the realised reward of ``op_kind`` under ``features``."""
        ...


class LookupRewardSimulator:
    """In-memory :class:`RewardSimulator` driven by a key-value table.

    Useful for tests + replay workloads.  The lookup key is a
    ``frozenset`` over ``features.items()`` so the order of
    feature insertion is not significant.  When the lookup miss-
    es, the simulator returns :attr:`default_reward` (configurable;
    ``0.0`` by default — "we have not observed this combination").
    """

    def __init__(
        self,
        *,
        table: Mapping[
            tuple[frozenset[tuple[str, float]], MemoryOpKind],
            float,
        ],
        default_reward: float = 0.0,
    ) -> None:
        if (
            default_reward != default_reward
            or default_reward in (float("inf"), float("-inf"))
        ):
            raise ValueError("default_reward must be finite")
        self._table = dict(table)
        self._default = default_reward

    def simulate(
        self,
        *,
        features: Mapping[str, float],
        op_kind: MemoryOpKind,
    ) -> float:
        key = (frozenset(features.items()), op_kind)
        return self._table.get(key, self._default)


@dataclass(frozen=True, slots=True)
class GRPOMetrics:
    """Summary of one GRPO training run."""

    iterations_run: int
    contexts_seen: int
    mean_advantage_abs: float
    final_objective: float


class GRPOTrainer:
    """Group Relative Policy Optimization over a logit policy."""

    def __init__(
        self,
        *,
        policy: MultinomialLogitPolicy,
        simulator: RewardSimulator,
        learning_rate: float = DEFAULT_GRPO_LEARNING_RATE,
        l2_lambda: float = DEFAULT_L2_LAMBDA,
    ) -> None:
        if learning_rate <= 0.0:
            raise ValueError("learning_rate must be positive")
        if l2_lambda < 0.0:
            raise ValueError("l2_lambda must be non-negative")
        self._policy = policy
        self._simulator = simulator
        self._lr = learning_rate
        self._l2 = l2_lambda
        self._log = logger.bind(component="memory_r1_grpo")

    def step(
        self,
        features: Mapping[str, float],
    ) -> tuple[float, dict[MemoryOpKind, float]]:
        """Run one GRPO update from ``features``.

        Returns ``(objective, advantages)`` where:

        - ``objective`` is the per-context policy objective
          ``Σ_k π(k|x) · r_k`` (caller-facing, monotone-up).
        - ``advantages`` is the per-kind advantage map.
        """
        weights = self._policy.weights
        probs = self._policy.probabilities(features)
        rewards = {
            kind: self._simulator.simulate(
                features=features, op_kind=kind,
            )
            for kind in weights.kinds()
        }
        baseline = sum(rewards.values()) / len(rewards)
        advantages = {
            kind: rewards[kind] - baseline
            for kind in rewards
        }
        for kind in weights.kinds():
            advantage = advantages[kind]
            scaled = self._lr * advantage
            weights.bias[kind] = (
                weights.bias[kind]
                + scaled
                - self._lr * self._l2 * weights.bias[kind]
            )
            self._update_feature_weights(
                kind=kind,
                features=features,
                scaled=scaled,
                weights=weights,
            )
        objective = sum(
            probs[kind] * rewards[kind] for kind in rewards
        )
        return objective, advantages

    def train(
        self,
        contexts: Sequence[Mapping[str, float]],
        *,
        iterations: int = 1,
    ) -> GRPOMetrics:
        """Run ``iterations`` epochs over ``contexts``."""
        if iterations < 1:
            raise ValueError("iterations must be >= 1")
        if not contexts:
            return GRPOMetrics(
                iterations_run=0,
                contexts_seen=0,
                mean_advantage_abs=0.0,
                final_objective=0.0,
            )
        last_objective = 0.0
        total_advantage = 0.0
        advantage_count = 0
        for epoch in range(iterations):
            last_objective = 0.0
            for features in contexts:
                objective, advantages = self.step(features)
                last_objective += objective
                for value in advantages.values():
                    total_advantage += abs(value)
                    advantage_count += 1
            self._log.info(
                "grpo.epoch",
                epoch=epoch + 1,
                objective=round(last_objective, 6),
            )
        mean_abs = (
            total_advantage / advantage_count
            if advantage_count
            else 0.0
        )
        return GRPOMetrics(
            iterations_run=iterations,
            contexts_seen=iterations * len(contexts),
            mean_advantage_abs=mean_abs,
            final_objective=last_objective,
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
