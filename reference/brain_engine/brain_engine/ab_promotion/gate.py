"""Bayesian A/B promotion gate (Sprint 10 closure, M23).

Standard Beta-Bernoulli formulation:

    p_arm | data ~ Beta(1 + successes, 1 + trials − successes)

For two arms (champion vs challenger), the probability the
challenger's true success rate exceeds the champion's is the
double integral

    P = ∫∫_{p_ch > p_c} f_c(p_c) · f_ch(p_ch) dp_c dp_ch.

We estimate it by Monte-Carlo: draw ``samples`` Beta variates
from each arm and count the fraction where the challenger
sample exceeds the champion sample.

Pure-Python via ``random.betavariate`` — no scipy dependency on
the runtime path; callers can plug a different sampler via the
``rng`` parameter (e.g. a seeded :class:`random.Random` for
deterministic CI runs).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Final

import structlog

from brain_engine.ab_promotion.models import (
    ArmStats,
    PromotionDecision,
    PromotionVerdict,
)


__all__ = [
    "BayesianPromotionGate",
    "DEFAULT_MIN_TRIALS",
    "DEFAULT_PROMOTION_SAMPLES",
    "DEFAULT_PROMOTION_THRESHOLD",
]


DEFAULT_PROMOTION_THRESHOLD: Final[float] = 0.95
DEFAULT_PROMOTION_SAMPLES: Final[int] = 20_000
DEFAULT_MIN_TRIALS: Final[int] = 30


logger = structlog.get_logger(__name__)


class BayesianPromotionGate:
    """Decide whether to promote a challenger over a champion.

    Construction takes the policy thresholds — ``threshold``
    (decision cutoff for ``P(challenger > champion)``),
    ``samples`` (Monte-Carlo budget), and ``min_trials`` (refuse
    to decide on thin data).  ``rng`` lets callers pin a seed for
    deterministic CI runs.
    """

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_PROMOTION_THRESHOLD,
        samples: int = DEFAULT_PROMOTION_SAMPLES,
        min_trials: int = DEFAULT_MIN_TRIALS,
        rng: random.Random | None = None,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(
                "threshold must be in [0.0, 1.0]"
            )
        if samples < 1:
            raise ValueError("samples must be positive")
        if min_trials < 1:
            raise ValueError("min_trials must be positive")
        self._threshold = threshold
        self._samples = samples
        self._min_trials = min_trials
        self._rng = rng or random.Random()
        self._log = logger.bind(component="bayesian_promotion")

    def evaluate(
        self,
        *,
        champion: ArmStats,
        challenger: ArmStats,
        at: datetime | None = None,
    ) -> PromotionDecision:
        """Run the Monte-Carlo check; return a structured verdict."""
        moment = self._now(at)
        if (
            champion.trials < self._min_trials
            or challenger.trials < self._min_trials
        ):
            return PromotionDecision(
                verdict=PromotionVerdict.INSUFFICIENT_DATA,
                prob_challenger_beats_champion=0.5,
                threshold=self._threshold,
                champion=champion,
                challenger=challenger,
                samples=0,
                evaluated_at=moment,
                rationale=(
                    f"min_trials={self._min_trials}; champion="
                    f"{champion.trials} challenger="
                    f"{challenger.trials}"
                ),
            )
        prob = self._monte_carlo(
            champion=champion, challenger=challenger,
        )
        if prob >= self._threshold:
            verdict = PromotionVerdict.PROMOTE
            rationale = (
                f"P(challenger > champion) = {prob:.4f} "
                f">= threshold={self._threshold:.2f}"
            )
        else:
            verdict = PromotionVerdict.KEEP_CHAMPION
            rationale = (
                f"P(challenger > champion) = {prob:.4f} "
                f"< threshold={self._threshold:.2f}"
            )
        self._log.info(
            "promotion.evaluated",
            champion=champion.name,
            challenger=challenger.name,
            verdict=verdict.value,
            probability=round(prob, 4),
        )
        return PromotionDecision(
            verdict=verdict,
            prob_challenger_beats_champion=prob,
            threshold=self._threshold,
            champion=champion,
            challenger=challenger,
            samples=self._samples,
            evaluated_at=moment,
            rationale=rationale,
        )

    # ── internals ─────────────────────────────────────────── #

    def _monte_carlo(
        self,
        *,
        champion: ArmStats,
        challenger: ArmStats,
    ) -> float:
        c_alpha = 1.0 + champion.successes
        c_beta = 1.0 + champion.trials - champion.successes
        x_alpha = 1.0 + challenger.successes
        x_beta = 1.0 + challenger.trials - challenger.successes
        wins = 0
        for _ in range(self._samples):
            c_sample = self._rng.betavariate(c_alpha, c_beta)
            x_sample = self._rng.betavariate(x_alpha, x_beta)
            if x_sample > c_sample:
                wins += 1
        return wins / self._samples

    @staticmethod
    def _now(at: datetime | None) -> datetime:
        if at is None:
            return datetime.now(timezone.utc)
        if at.tzinfo is None:
            raise ValueError("`at` must be tz-aware when provided")
        return at
