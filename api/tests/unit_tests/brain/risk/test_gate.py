"""Behaviour of :class:`RiskGate`."""

from __future__ import annotations

import pytest

from core.brain.risk.gate import RiskGate
from core.brain.risk.models import OutcomeSample, RiskVerdict


@pytest.fixture
def gate() -> RiskGate:
    return RiskGate(
        cvar_threshold=100.0,
        alpha=0.05,
        min_samples=32,
    )


def test_thin_window_returns_insufficient(
    gate: RiskGate,
) -> None:
    samples = [OutcomeSample(loss=1.0) for _ in range(5)]
    decision = gate.decide(samples)
    assert decision.verdict is RiskVerdict.INSUFFICIENT_DATA
    assert decision.estimate is None


def test_low_cvar_proceeds(gate: RiskGate) -> None:
    """Tame distribution proceeds."""
    samples = [OutcomeSample(loss=10.0) for _ in range(40)]
    decision = gate.decide(samples)
    assert decision.verdict is RiskVerdict.PROCEED
    assert decision.estimate is not None
    assert decision.estimate.cvar <= 100.0


def test_high_cvar_abstains(gate: RiskGate) -> None:
    """Distribution with heavy tail abstains."""
    samples = [OutcomeSample(loss=10.0) for _ in range(35)] + [OutcomeSample(loss=500.0) for _ in range(5)]
    decision = gate.decide(samples)
    assert decision.verdict is RiskVerdict.ABSTAIN
    assert decision.estimate is not None
    assert decision.estimate.cvar > 100.0


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"alpha": 0.0}, "alpha"),
        ({"alpha": 1.0}, "alpha"),
        ({"min_samples": 0}, "min_samples"),
    ],
    ids=["alpha_zero", "alpha_one", "zero_min"],
)
def test_gate_construction_validation(
    override: dict[str, float],
    match: str,
) -> None:
    defaults: dict[str, float] = {
        "cvar_threshold": 50.0,
        "alpha": 0.05,
        "min_samples": 16,
    }
    defaults.update(override)
    with pytest.raises(ValueError, match=match):
        RiskGate(**defaults)  # type: ignore[arg-type]
