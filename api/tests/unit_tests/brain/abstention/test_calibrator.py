"""Behaviour of :class:`ConformalCalibrator` and the in-memory store."""

from __future__ import annotations

import pytest

from core.brain.abstention.calibrator import ConformalCalibrator
from core.brain.abstention.models import CalibrationSample
from core.brain.abstention.protocols import (
    InMemoryCalibrationStore,
)


def _seed(
    calibrator: ConformalCalibrator,
    *,
    tool_id: str,
    successes_at: list[float],
    failures_at: list[float],
) -> None:
    """Push the given confidence/outcome pairs into the calibrator."""
    for confidence in successes_at:
        calibrator.record(
            CalibrationSample.now(
                tool_id=tool_id,
                predicted_confidence=confidence,
                actual_success=True,
            )
        )
    for confidence in failures_at:
        calibrator.record(
            CalibrationSample.now(
                tool_id=tool_id,
                predicted_confidence=confidence,
                actual_success=False,
            )
        )


@pytest.fixture
def calibrator() -> ConformalCalibrator:
    """Fresh calibrator backed by an in-memory store."""
    return ConformalCalibrator(store=InMemoryCalibrationStore())


def test_empty_window_reports_zero_wilson(
    calibrator: ConformalCalibrator,
) -> None:
    """No samples → Wilson LB is ``0.0`` (no evidence)."""
    assert calibrator.wilson_lb("t") == 0.0
    assert calibrator.sample_size("t") == 0


def test_wilson_lb_grows_with_consistent_successes(
    calibrator: ConformalCalibrator,
) -> None:
    """100 successes → Wilson LB > 90 successes."""
    _seed(
        calibrator,
        tool_id="big",
        successes_at=[0.9] * 100,
        failures_at=[],
    )
    big_lb = calibrator.wilson_lb("big")
    _seed(
        calibrator,
        tool_id="small",
        successes_at=[0.9] * 5,
        failures_at=[],
    )
    small_lb = calibrator.wilson_lb("small")
    assert big_lb > small_lb


def test_conformal_threshold_returns_none_without_failures(
    calibrator: ConformalCalibrator,
) -> None:
    """No failed samples → conformal threshold is ``None``."""
    _seed(
        calibrator,
        tool_id="t",
        successes_at=[0.8] * 50,
        failures_at=[],
    )
    assert calibrator.conformal_threshold("t") is None


def test_conformal_threshold_at_alpha_quantile(
    calibrator: ConformalCalibrator,
) -> None:
    """Threshold sits within the failed-confidence range."""
    failures_at = [0.4, 0.5, 0.6, 0.7, 0.8]
    _seed(
        calibrator,
        tool_id="t",
        successes_at=[0.95] * 40,
        failures_at=failures_at,
    )
    threshold = calibrator.conformal_threshold("t", alpha=0.5)
    assert threshold is not None
    assert min(failures_at) <= threshold <= max(failures_at)


def test_conformal_threshold_alpha_validation(
    calibrator: ConformalCalibrator,
) -> None:
    """Alpha outside ``(0, 1)`` raises :class:`ValueError`."""
    _seed(
        calibrator,
        tool_id="t",
        successes_at=[],
        failures_at=[0.5],
    )
    with pytest.raises(ValueError, match="alpha"):
        calibrator.conformal_threshold("t", alpha=0.0)
    with pytest.raises(ValueError, match="alpha"):
        calibrator.conformal_threshold("t", alpha=1.0)


def test_in_memory_store_window_caps_size() -> None:
    """Store enforces ``window_size`` cap on per-tool samples."""
    store = InMemoryCalibrationStore(window_size=3)
    for confidence in (0.1, 0.2, 0.3, 0.4, 0.5):
        store.record(
            CalibrationSample.now(
                tool_id="t",
                predicted_confidence=confidence,
                actual_success=True,
            )
        )
    samples = store.samples_for("t")
    assert len(samples) == 3
    # oldest values dropped — the deque keeps the last three
    assert [s.predicted_confidence for s in samples] == ([0.3, 0.4, 0.5])


def test_in_memory_store_clear_one_tool() -> None:
    """Clearing one tool leaves others intact."""
    store = InMemoryCalibrationStore()
    store.record(
        CalibrationSample.now(
            tool_id="a",
            predicted_confidence=0.9,
            actual_success=True,
        )
    )
    store.record(
        CalibrationSample.now(
            tool_id="b",
            predicted_confidence=0.9,
            actual_success=True,
        )
    )
    store.clear("a")
    assert store.samples_for("a") == ()
    assert len(store.samples_for("b")) == 1


def test_in_memory_store_window_size_validation() -> None:
    """Non-positive ``window_size`` is rejected."""
    with pytest.raises(ValueError, match="window_size"):
        InMemoryCalibrationStore(window_size=0)
    with pytest.raises(ValueError, match="window_size"):
        InMemoryCalibrationStore(window_size=-1)
