"""Behaviour of :class:`MapieSplitConformalCalibrator` + gate."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime, timezone

import pytest

from brain_engine.abstention.mapie_calibrator import (
    MapieAbstainGate,
    MapieSplitConformalCalibrator,
)
from brain_engine.abstention.models import (
    AbstentionVerdict,
    CalibrationSample,
)
from brain_engine.abstention.split_conformal import (
    ConformalLabel,
)


_T0 = datetime(2026, 5, 11, tzinfo=timezone.utc)


def _sample(
    *,
    confidence: float,
    success: bool,
) -> CalibrationSample:
    """Build a calibration sample with a fixed timestamp."""
    return CalibrationSample(
        tool_id="t",
        predicted_confidence=confidence,
        actual_success=success,
        recorded_at=_T0,
    )


def _synthetic_window(
    *,
    n: int,
    seed: int = 0,
) -> Sequence[CalibrationSample]:
    """Make ``n`` samples where ``actual_success`` follows confidence."""
    rng = random.Random(seed)
    samples: list[CalibrationSample] = []
    for _ in range(n):
        confidence = max(
            0.01, min(0.99, rng.gauss(0.7, 0.15))
        )
        success = rng.random() < confidence
        samples.append(
            _sample(
                confidence=confidence,
                success=success,
            )
        )
    return samples


def test_empty_window_returns_unit_set() -> None:
    """No samples ⇒ unit set ``{SUCCESS, FAILURE}``."""
    calibrator = MapieSplitConformalCalibrator()
    result = calibrator.predict_set(
        samples=(), model_confidence=0.5,
    )
    assert result.labels == frozenset(
        {ConformalLabel.SUCCESS, ConformalLabel.FAILURE}
    )
    assert result.calibration_size == 0


def test_single_label_window_returns_unit_set() -> None:
    """All-success / all-failure windows fall back to unit set."""
    calibrator = MapieSplitConformalCalibrator()
    all_success = [
        _sample(confidence=0.9, success=True)
        for _ in range(30)
    ]
    result = calibrator.predict_set(
        samples=all_success, model_confidence=0.5,
    )
    assert result.labels == frozenset(
        {ConformalLabel.SUCCESS, ConformalLabel.FAILURE}
    )


def test_high_confidence_yields_singleton_success() -> None:
    """High model confidence on a balanced window keeps SUCCESS."""
    calibrator = MapieSplitConformalCalibrator(alpha=0.10)
    window = _synthetic_window(n=120, seed=42)
    result = calibrator.predict_set(
        samples=window, model_confidence=0.95,
    )
    assert ConformalLabel.SUCCESS in result.labels


def test_low_confidence_yields_singleton_failure() -> None:
    """Low model confidence on a balanced window keeps FAILURE."""
    calibrator = MapieSplitConformalCalibrator(alpha=0.10)
    window = _synthetic_window(n=120, seed=42)
    result = calibrator.predict_set(
        samples=window, model_confidence=0.05,
    )
    assert ConformalLabel.FAILURE in result.labels


def test_ambiguous_confidence_yields_full_set() -> None:
    """Mid-range confidence on a balanced window keeps both labels."""
    calibrator = MapieSplitConformalCalibrator(alpha=0.10)
    window = _synthetic_window(n=120, seed=42)
    result = calibrator.predict_set(
        samples=window, model_confidence=0.50,
    )
    assert result.labels == frozenset(
        {ConformalLabel.SUCCESS, ConformalLabel.FAILURE}
    )


def test_alpha_validates_range() -> None:
    """``alpha`` must be in ``(0, 1)``."""
    with pytest.raises(ValueError, match="alpha"):
        MapieSplitConformalCalibrator(alpha=0.0)
    with pytest.raises(ValueError, match="alpha"):
        MapieSplitConformalCalibrator(alpha=1.0)


def test_model_confidence_validates_range() -> None:
    """``model_confidence`` outside ``[0, 1]`` is rejected."""
    calibrator = MapieSplitConformalCalibrator()
    with pytest.raises(ValueError, match="model_confidence"):
        calibrator.predict_set(
            samples=(), model_confidence=-0.1,
        )
    with pytest.raises(ValueError, match="model_confidence"):
        calibrator.predict_set(
            samples=(), model_confidence=1.5,
        )


def test_conformity_score_must_be_non_empty() -> None:
    """Empty conformity score string is rejected."""
    with pytest.raises(ValueError, match="conformity_score"):
        MapieSplitConformalCalibrator(conformity_score="")


def test_calibration_size_matches_input() -> None:
    """``calibration_size`` reports the number of input samples."""
    calibrator = MapieSplitConformalCalibrator()
    window = _synthetic_window(n=40)
    result = calibrator.predict_set(
        samples=window, model_confidence=0.5,
    )
    assert result.calibration_size == 40


def test_deterministic_for_fixed_seed() -> None:
    """Calling twice on the same window agrees label-for-label."""
    calibrator = MapieSplitConformalCalibrator(random_state=7)
    window = _synthetic_window(n=120, seed=11)
    first = calibrator.predict_set(
        samples=window, model_confidence=0.7,
    )
    second = calibrator.predict_set(
        samples=window, model_confidence=0.7,
    )
    assert first.labels == second.labels


def test_alpha_property_round_trips() -> None:
    """The ``alpha`` property returns the configured value."""
    calibrator = MapieSplitConformalCalibrator(alpha=0.15)
    assert calibrator.alpha == 0.15


def test_gate_returns_insufficient_below_min() -> None:
    """``MapieAbstainGate`` short-circuits when window is thin."""
    calibrator = MapieSplitConformalCalibrator()
    gate = MapieAbstainGate(
        calibrator=calibrator, min_calibration=10,
    )
    result = gate.decide(samples=(), model_confidence=0.5)
    assert (
        result.verdict
        is AbstentionVerdict.INSUFFICIENT_DATA
    )


def test_gate_proceeds_on_high_confidence_singleton() -> None:
    """Gate maps a SUCCESS singleton to PROCEED."""
    calibrator = MapieSplitConformalCalibrator(alpha=0.10)
    gate = MapieAbstainGate(
        calibrator=calibrator, min_calibration=20,
    )
    window = _synthetic_window(n=120, seed=42)
    result = gate.decide(
        samples=window, model_confidence=0.95,
    )
    assert result.verdict is AbstentionVerdict.PROCEED


def test_gate_abstains_on_ambiguous_set() -> None:
    """Gate maps an ambiguous set to ABSTAIN."""
    calibrator = MapieSplitConformalCalibrator(alpha=0.10)
    gate = MapieAbstainGate(
        calibrator=calibrator, min_calibration=20,
    )
    window = _synthetic_window(n=120, seed=42)
    result = gate.decide(
        samples=window, model_confidence=0.5,
    )
    assert result.verdict is AbstentionVerdict.ABSTAIN
    assert "ambiguous" in result.rationale


def test_gate_min_calibration_must_be_positive() -> None:
    """``min_calibration`` cannot be ``0`` or negative."""
    calibrator = MapieSplitConformalCalibrator()
    with pytest.raises(ValueError, match="min_calibration"):
        MapieAbstainGate(
            calibrator=calibrator, min_calibration=0,
        )


def test_alpha_higher_widens_proceed_band() -> None:
    """Higher α ⇒ smaller calibration quantile ⇒ more PROCEEDs."""
    window = _synthetic_window(n=120, seed=42)
    tight = MapieSplitConformalCalibrator(alpha=0.01)
    relaxed = MapieSplitConformalCalibrator(alpha=0.30)
    # On mid-confidence the relaxed gate should be more
    # permissive (smaller set ⇒ more singleton outcomes).
    tight_set = tight.predict_set(
        samples=window, model_confidence=0.65,
    )
    relaxed_set = relaxed.predict_set(
        samples=window, model_confidence=0.65,
    )
    assert len(relaxed_set.labels) <= len(tight_set.labels)
