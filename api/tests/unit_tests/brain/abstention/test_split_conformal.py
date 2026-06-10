"""Behaviour of :mod:`core.brain.abstention.split_conformal` (M24)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.brain.abstention.models import (
    AbstentionVerdict,
    CalibrationSample,
)
from core.brain.abstention.split_conformal import (
    ConformalAbstainGate,
    ConformalLabel,
    SplitConformalCalibrator,
    binary_inverse_confidence,
    empirical_conformal_quantile,
)


def _now() -> datetime:
    return datetime(2026, 5, 11, tzinfo=UTC)


def _samples(
    *,
    successes_at: list[float],
    failures_at: list[float],
) -> list[CalibrationSample]:
    out: list[CalibrationSample] = []
    for c in successes_at:
        out.append(
            CalibrationSample(
                tool_id="t",
                predicted_confidence=c,
                actual_success=True,
                recorded_at=_now(),
            )
        )
    for c in failures_at:
        out.append(
            CalibrationSample(
                tool_id="t",
                predicted_confidence=c,
                actual_success=False,
                recorded_at=_now(),
            )
        )
    return out


# ── binary_inverse_confidence ────────────────────────────── #


def test_binary_inverse_confidence_success_path() -> None:
    """Success → s = 1 - confidence."""
    sample = CalibrationSample(
        tool_id="t",
        predicted_confidence=0.85,
        actual_success=True,
        recorded_at=_now(),
    )
    assert binary_inverse_confidence(sample) == pytest.approx(0.15)


def test_binary_inverse_confidence_failure_path() -> None:
    """Failure → s = confidence (high confidence on wrong answer)."""
    sample = CalibrationSample(
        tool_id="t",
        predicted_confidence=0.6,
        actual_success=False,
        recorded_at=_now(),
    )
    assert binary_inverse_confidence(sample) == pytest.approx(0.6)


# ── empirical_conformal_quantile ─────────────────────────── #


def test_empirical_quantile_alpha_validation() -> None:
    with pytest.raises(ValueError, match="alpha"):
        empirical_conformal_quantile([0.1, 0.2], alpha=0.0)
    with pytest.raises(ValueError, match="alpha"):
        empirical_conformal_quantile([0.1, 0.2], alpha=1.0)


def test_empirical_quantile_empty_returns_one() -> None:
    """Empty input → 1.0 (no calibration → accept all candidates)."""
    assert empirical_conformal_quantile([], alpha=0.1) == 1.0


def test_empirical_quantile_all_zero() -> None:
    """All scores zero → quantile is zero."""
    assert (
        empirical_conformal_quantile(
            [0.0] * 50,
            alpha=0.1,
        )
        == 0.0
    )


def test_empirical_quantile_uniform_distribution() -> None:
    """For uniform [0, 1, ..., 99] with α=0.1, q ≈ 0.9-quantile."""
    scores = [i / 100 for i in range(100)]
    q = empirical_conformal_quantile(scores, alpha=0.1)
    # rank = ceil((100+1)*0.9) = 91; ordered[90] = 0.90.
    assert q == pytest.approx(0.90)


# ── SplitConformalCalibrator ─────────────────────────────── #


def test_calibrator_alpha_validation() -> None:
    with pytest.raises(ValueError, match="alpha"):
        SplitConformalCalibrator(alpha=0.0)


def test_calibrator_min_calibration_validation() -> None:
    with pytest.raises(ValueError, match="min_calibration"):
        SplitConformalCalibrator(min_calibration=0)


def test_calibrator_threshold_uses_non_conformity_scorer() -> None:
    """Threshold = empirical (1-α) quantile of non-conformity scores."""
    samples = _samples(
        successes_at=[0.95] * 35,
        failures_at=[0.55] * 5,
    )
    calibrator = SplitConformalCalibrator(alpha=0.1)
    threshold = calibrator.threshold(samples)
    # Non-conformity scores: 35 × 0.05 + 5 × 0.55.
    # Quantile rank = ceil((40+1)*0.9) = 37; ordered[36] = 0.55.
    assert threshold == pytest.approx(0.55)


def test_calibrator_predict_set_singleton_success() -> None:
    """Confidence well above threshold → singleton {success}."""
    samples = _samples(
        successes_at=[0.95] * 35,
        failures_at=[0.55] * 5,
    )
    calibrator = SplitConformalCalibrator(alpha=0.1)
    conformal = calibrator.predict_set(
        samples=samples,
        model_confidence=0.95,
    )
    assert conformal.is_singleton
    assert ConformalLabel.SUCCESS in conformal.labels


def test_calibrator_predict_set_ambiguous() -> None:
    """Confidence near boundary → both labels survive → ambiguous."""
    samples = _samples(
        successes_at=[0.95] * 35,
        failures_at=[0.55] * 5,
    )
    calibrator = SplitConformalCalibrator(alpha=0.1)
    conformal = calibrator.predict_set(
        samples=samples,
        model_confidence=0.5,
    )
    # At c=0.5: both 1-0.5=0.5 and 0.5 are below threshold 0.55.
    assert len(conformal.labels) == 2
    assert ConformalLabel.SUCCESS in conformal.labels
    assert ConformalLabel.FAILURE in conformal.labels


def test_calibrator_predict_set_singleton_failure() -> None:
    """Low confidence → singleton {failure}."""
    samples = _samples(
        successes_at=[0.95] * 35,
        failures_at=[0.55] * 5,
    )
    calibrator = SplitConformalCalibrator(alpha=0.1)
    conformal = calibrator.predict_set(
        samples=samples,
        model_confidence=0.1,
    )
    assert conformal.is_singleton
    assert ConformalLabel.FAILURE in conformal.labels


def test_calibrator_predict_set_validates_confidence() -> None:
    """``model_confidence`` outside ``[0, 1]`` is rejected."""
    samples = _samples(successes_at=[0.9], failures_at=[])
    calibrator = SplitConformalCalibrator()
    with pytest.raises(ValueError, match="model_confidence"):
        calibrator.predict_set(
            samples=samples,
            model_confidence=1.5,
        )


# ── ConformalAbstainGate ─────────────────────────────────── #


@pytest.fixture
def gate() -> ConformalAbstainGate:
    return ConformalAbstainGate(
        calibrator=SplitConformalCalibrator(alpha=0.1),
        min_calibration=20,
    )


def test_gate_thin_data_returns_insufficient(
    gate: ConformalAbstainGate,
) -> None:
    samples = _samples(successes_at=[0.9] * 5, failures_at=[])
    result = gate.decide(
        samples=samples,
        model_confidence=0.9,
    )
    assert result.verdict is AbstentionVerdict.INSUFFICIENT_DATA


def test_gate_singleton_proceeds(
    gate: ConformalAbstainGate,
) -> None:
    samples = _samples(
        successes_at=[0.95] * 35,
        failures_at=[0.55] * 5,
    )
    result = gate.decide(
        samples=samples,
        model_confidence=0.95,
    )
    assert result.verdict is AbstentionVerdict.PROCEED
    assert "success" in result.rationale


def test_gate_ambiguous_abstains(
    gate: ConformalAbstainGate,
) -> None:
    samples = _samples(
        successes_at=[0.95] * 35,
        failures_at=[0.55] * 5,
    )
    result = gate.decide(
        samples=samples,
        model_confidence=0.5,
    )
    assert result.verdict is AbstentionVerdict.ABSTAIN
    assert "ambiguous" in result.rationale


def test_gate_min_calibration_validation() -> None:
    with pytest.raises(ValueError, match="min_calibration"):
        ConformalAbstainGate(
            calibrator=SplitConformalCalibrator(),
            min_calibration=0,
        )


def test_gate_empty_set_abstains() -> None:
    """Both non-conformity scores above threshold → empty set → ABSTAIN."""
    # Build samples whose non-conformity is small for both
    # success and failure paths so threshold is very low; then
    # query with mid-range confidence to land outside the small
    # threshold for both sides.
    samples = _samples(
        successes_at=[0.99] * 20,
        failures_at=[0.01] * 5,
    )
    gate = ConformalAbstainGate(
        calibrator=SplitConformalCalibrator(alpha=0.1),
        min_calibration=20,
    )
    result = gate.decide(
        samples=samples,
        model_confidence=0.5,
    )
    assert result.verdict is AbstentionVerdict.ABSTAIN
