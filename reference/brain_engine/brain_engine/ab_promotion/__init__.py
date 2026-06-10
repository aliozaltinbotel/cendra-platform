"""Bayesian A/B promotion gate (M23, Sprint 10 closure).

Pure-Python Beta-Bernoulli A/B test for promotion decisions
(challenger replaces champion when ``P(challenger > champion)``
exceeds the configured threshold).

Public surface:

  * :class:`PromotionVerdict` — three-valued result
    (PROMOTE / KEEP_CHAMPION / INSUFFICIENT_DATA).
  * :class:`ArmStats` — frozen dataclass: ``(name, successes,
    trials)`` with fail-fast validation.
  * :class:`PromotionDecision` — frozen aggregate carrying the
    probability estimate, threshold, both arm snapshots, sample
    count, tz-aware ``evaluated_at`` and rationale.
  * :class:`BayesianPromotionGate` — runtime façade with
    Monte-Carlo Beta sampler, configurable threshold (default
    0.95), MC samples (default 20 000), and min_trials floor
    (default 30).  Optional ``rng`` parameter for deterministic
    CI runs.

Closes Sprint 10 from latest_research §6 ("A/B Bayesian
sequential framework").  No new external dependency — uses
``random.betavariate`` from stdlib.
"""

from __future__ import annotations

from brain_engine.ab_promotion.gate import (
    DEFAULT_MIN_TRIALS,
    DEFAULT_PROMOTION_SAMPLES,
    DEFAULT_PROMOTION_THRESHOLD,
    BayesianPromotionGate,
)
from brain_engine.ab_promotion.models import (
    ArmStats,
    PromotionDecision,
    PromotionVerdict,
)


__all__ = [
    "ArmStats",
    "BayesianPromotionGate",
    "DEFAULT_MIN_TRIALS",
    "DEFAULT_PROMOTION_SAMPLES",
    "DEFAULT_PROMOTION_THRESHOLD",
    "PromotionDecision",
    "PromotionVerdict",
]
