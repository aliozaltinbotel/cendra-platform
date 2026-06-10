"""Invariants of A/B promotion value objects."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.ab_promotion.models import (
    ArmStats,
    PromotionDecision,
    PromotionVerdict,
)


def _now() -> datetime:
    return datetime(2026, 5, 11, tzinfo=timezone.utc)


def _decision(**overrides: object) -> PromotionDecision:
    base: dict[str, object] = {
        "verdict": PromotionVerdict.PROMOTE,
        "prob_challenger_beats_champion": 0.9,
        "threshold": 0.95,
        "champion": ArmStats(name="c", successes=10, trials=20),
        "challenger": ArmStats(name="x", successes=15, trials=20),
        "samples": 1_000,
        "evaluated_at": _now(),
        "rationale": "ok",
    }
    base.update(overrides)
    return PromotionDecision(**base)  # type: ignore[arg-type]


def test_arm_stats_validation() -> None:
    with pytest.raises(ValueError, match="name"):
        ArmStats(name="", successes=0, trials=0)
    with pytest.raises(ValueError, match="trials"):
        ArmStats(name="x", successes=0, trials=-1)
    with pytest.raises(ValueError, match="successes"):
        ArmStats(name="x", successes=-1, trials=10)
    with pytest.raises(ValueError, match="successes"):
        ArmStats(name="x", successes=11, trials=10)


def test_arm_stats_zero_trials_ok() -> None:
    """Zero trials is valid (used at very start of a test)."""
    arm = ArmStats(name="x", successes=0, trials=0)
    assert arm.trials == 0


def test_three_verdict_values() -> None:
    assert {v.value for v in PromotionVerdict} == {
        "promote",
        "keep_champion",
        "insufficient_data",
    }


def test_decision_naive_at_rejected() -> None:
    with pytest.raises(ValueError, match="evaluated_at"):
        _decision(evaluated_at=datetime(2026, 5, 11))


def test_decision_prob_bounds() -> None:
    with pytest.raises(
        ValueError, match="prob_challenger_beats_champion"
    ):
        _decision(prob_challenger_beats_champion=1.5)


def test_decision_threshold_bounds() -> None:
    with pytest.raises(ValueError, match="threshold"):
        _decision(threshold=2.0)


def test_decision_negative_samples_rejected() -> None:
    with pytest.raises(ValueError, match="samples"):
        _decision(samples=-1)


def test_decision_empty_rationale_rejected() -> None:
    with pytest.raises(ValueError, match="rationale"):
        _decision(rationale="")
