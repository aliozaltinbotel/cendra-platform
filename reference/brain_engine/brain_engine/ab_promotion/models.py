"""Value objects for the Bayesian A/B promotion gate (M23)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


__all__ = [
    "ArmStats",
    "PromotionDecision",
    "PromotionVerdict",
]


class PromotionVerdict(StrEnum):
    """Three-valued result of one A/B promotion check.

    - ``PROMOTE``: ``P(challenger > champion) >= threshold``;
      challenger replaces champion.
    - ``KEEP_CHAMPION``: posterior favours the champion (or
      shows no significant edge).
    - ``INSUFFICIENT_DATA``: at least one arm has fewer than the
      configured ``min_trials`` observations.  The gate refuses
      to decide.
    """

    PROMOTE = "promote"
    KEEP_CHAMPION = "keep_champion"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class ArmStats:
    """Observed (successes, trials) of one A/B arm.

    Attributes:
        name: Caller-defined arm label (e.g. ``"champion"`` or
            ``"challenger_v3"``).
        successes: Number of positive outcomes.  Non-negative,
            ``<= trials``.
        trials: Total observations.  Non-negative.
    """

    name: str
    successes: int
    trials: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name required")
        if self.trials < 0:
            raise ValueError("trials must be non-negative")
        if self.successes < 0:
            raise ValueError("successes must be non-negative")
        if self.successes > self.trials:
            raise ValueError("successes cannot exceed trials")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Structured outcome of one promotion evaluation.

    Attributes:
        verdict: One of :class:`PromotionVerdict`.
        prob_challenger_beats_champion: Estimated
            ``P(p_challenger > p_champion | data)`` in the closed
            interval ``[0.0, 1.0]``.  ``0.5`` when neither arm
            has any data.
        threshold: The decision threshold used.
        champion: ``ArmStats`` snapshot of the champion at
            decision time.
        challenger: ``ArmStats`` snapshot of the challenger.
        samples: Number of Monte-Carlo samples used to estimate
            the probability.
        evaluated_at: tz-aware UTC instant the gate decided.
        rationale: One-line plain-English summary; consumed by
            the audit log.
    """

    verdict: PromotionVerdict
    prob_challenger_beats_champion: float
    threshold: float
    champion: ArmStats
    challenger: ArmStats
    samples: int
    evaluated_at: datetime
    rationale: str = field(default="")

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        prob = self.prob_challenger_beats_champion
        if not 0.0 <= prob <= 1.0:
            raise ValueError(
                "prob_challenger_beats_champion must be in [0, 1]"
            )
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(
                "threshold must be in [0.0, 1.0]"
            )
        if self.samples < 0:
            raise ValueError("samples must be non-negative")
        if not self.rationale:
            raise ValueError("rationale required")
