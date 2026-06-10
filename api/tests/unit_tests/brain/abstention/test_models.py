"""Value-object invariants for the abstention layer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.brain.abstention.models import (
    AbstentionDecision,
    AbstentionVerdict,
    CalibrationSample,
)


def test_calibration_sample_rejects_out_of_range_confidence() -> None:
    """Confidence outside ``[0, 1]`` raises :class:`ValueError`."""
    with pytest.raises(ValueError, match="predicted_confidence"):
        CalibrationSample(
            tool_id="t",
            predicted_confidence=1.5,
            actual_success=True,
            recorded_at=datetime.now(UTC),
        )


def test_calibration_sample_accepts_boundary_values() -> None:
    """Confidence values 0.0 and 1.0 are valid."""
    for value in (0.0, 1.0):
        sample = CalibrationSample(
            tool_id="t",
            predicted_confidence=value,
            actual_success=False,
            recorded_at=datetime.now(UTC),
        )
        assert sample.predicted_confidence == value


def test_calibration_sample_now_stamps_utc() -> None:
    """``CalibrationSample.now`` records a tz-aware UTC timestamp."""
    sample = CalibrationSample.now(
        tool_id="t",
        predicted_confidence=0.5,
        actual_success=True,
    )
    assert sample.recorded_at.tzinfo is UTC


def test_abstention_decision_is_immutable() -> None:
    """The decision is a frozen dataclass."""
    decision = AbstentionDecision(
        tool_id="t",
        verdict=AbstentionVerdict.PROCEED,
        model_confidence=0.9,
        wilson_lb=0.8,
        sample_size=42,
        conformal_threshold=0.5,
        rationale="ok",
    )
    with pytest.raises((AttributeError, TypeError)):
        decision.verdict = AbstentionVerdict.ABSTAIN  # type: ignore[misc]


@pytest.mark.parametrize(
    "verdict",
    list(AbstentionVerdict),
    ids=lambda v: v.value,
)
def test_every_verdict_value_is_defined(
    verdict: AbstentionVerdict,
) -> None:
    """Every enum member has a string value."""
    assert isinstance(verdict.value, str)
    assert verdict.value
