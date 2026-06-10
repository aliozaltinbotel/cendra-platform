"""Decision rule of :class:`AbstentionGate`."""

from __future__ import annotations

import pytest

from core.brain.abstention.calibrator import ConformalCalibrator
from core.brain.abstention.gate import AbstentionGate
from core.brain.abstention.models import (
    AbstentionVerdict,
    CalibrationSample,
)
from core.brain.abstention.protocols import (
    InMemoryCalibrationStore,
)


def _seed_successes(
    calibrator: ConformalCalibrator,
    *,
    tool_id: str,
    n: int,
    confidence: float,
) -> None:
    for _ in range(n):
        calibrator.record(
            CalibrationSample.now(
                tool_id=tool_id,
                predicted_confidence=confidence,
                actual_success=True,
            )
        )


def _seed_failures(
    calibrator: ConformalCalibrator,
    *,
    tool_id: str,
    n: int,
    confidence: float,
) -> None:
    for _ in range(n):
        calibrator.record(
            CalibrationSample.now(
                tool_id=tool_id,
                predicted_confidence=confidence,
                actual_success=False,
            )
        )


@pytest.fixture
def calibrator() -> ConformalCalibrator:
    return ConformalCalibrator(store=InMemoryCalibrationStore())


@pytest.fixture
def gate(calibrator: ConformalCalibrator) -> AbstentionGate:
    return AbstentionGate(
        calibrator=calibrator,
        wilson_threshold=0.60,
        min_samples=30,
        alpha=0.10,
    )


def test_empty_window_returns_insufficient_data(
    gate: AbstentionGate,
) -> None:
    decision = gate.decide(
        tool_id="t",
        model_confidence=0.9,
    )
    assert decision.verdict is AbstentionVerdict.INSUFFICIENT_DATA
    assert decision.sample_size == 0


def test_high_confidence_high_success_rate_proceeds(
    calibrator: ConformalCalibrator,
    gate: AbstentionGate,
) -> None:
    _seed_successes(
        calibrator,
        tool_id="t",
        n=40,
        confidence=0.9,
    )
    decision = gate.decide(
        tool_id="t",
        model_confidence=0.95,
    )
    assert decision.verdict is AbstentionVerdict.PROCEED
    assert decision.wilson_lb >= 0.60


def test_low_wilson_lb_abstains(
    calibrator: ConformalCalibrator,
    gate: AbstentionGate,
) -> None:
    _seed_successes(
        calibrator,
        tool_id="bad",
        n=15,
        confidence=0.7,
    )
    _seed_failures(
        calibrator,
        tool_id="bad",
        n=20,
        confidence=0.7,
    )
    decision = gate.decide(
        tool_id="bad",
        model_confidence=0.99,
    )
    assert decision.verdict is AbstentionVerdict.ABSTAIN
    assert "wilson_lb" in decision.rationale


def test_below_conformal_threshold_abstains(
    calibrator: ConformalCalibrator,
    gate: AbstentionGate,
) -> None:
    _seed_successes(
        calibrator,
        tool_id="t",
        n=35,
        confidence=0.95,
    )
    _seed_failures(
        calibrator,
        tool_id="t",
        n=5,
        confidence=0.6,
    )
    decision = gate.decide(
        tool_id="t",
        model_confidence=0.55,
    )
    assert decision.verdict is AbstentionVerdict.ABSTAIN
    assert "conformal_threshold" in decision.rationale


def test_decision_carries_all_evidence_for_audit(
    calibrator: ConformalCalibrator,
    gate: AbstentionGate,
) -> None:
    _seed_successes(
        calibrator,
        tool_id="t",
        n=40,
        confidence=0.9,
    )
    decision = gate.decide(
        tool_id="t",
        model_confidence=0.85,
    )
    assert decision.tool_id == "t"
    assert decision.model_confidence == 0.85
    assert 0.0 <= decision.wilson_lb <= 1.0
    assert decision.sample_size == 40
    assert decision.rationale


@pytest.mark.parametrize(
    ("invalid_kwargs", "match"),
    [
        ({"wilson_threshold": -0.1}, "wilson_threshold"),
        ({"wilson_threshold": 1.1}, "wilson_threshold"),
        ({"min_samples": 0}, "min_samples"),
        ({"alpha": 0.0}, "alpha"),
        ({"alpha": 1.0}, "alpha"),
    ],
    ids=[
        "wilson_below_zero",
        "wilson_above_one",
        "zero_min_samples",
        "alpha_at_zero",
        "alpha_at_one",
    ],
)
def test_gate_construction_validates_params(
    calibrator: ConformalCalibrator,
    invalid_kwargs: dict[str, float],
    match: str,
) -> None:
    """Constructor rejects out-of-range thresholds (fail-fast)."""
    defaults: dict[str, float] = {
        "wilson_threshold": 0.5,
        "min_samples": 10,
        "alpha": 0.1,
    }
    defaults.update(invalid_kwargs)
    with pytest.raises(ValueError, match=match):
        AbstentionGate(calibrator=calibrator, **defaults)  # type: ignore[arg-type]


def test_decide_validates_model_confidence(
    gate: AbstentionGate,
) -> None:
    """``decide`` rejects out-of-range confidences."""
    with pytest.raises(ValueError, match="model_confidence"):
        gate.decide(tool_id="t", model_confidence=1.5)
    with pytest.raises(ValueError, match="model_confidence"):
        gate.decide(tool_id="t", model_confidence=-0.1)
