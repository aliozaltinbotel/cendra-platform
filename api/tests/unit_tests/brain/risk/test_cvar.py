"""Behaviour of :func:`compute_risk` and value objects."""

from __future__ import annotations

import math

import pytest

from core.brain.risk.cvar import compute_risk
from core.brain.risk.models import OutcomeSample


def test_outcome_sample_rejects_inf() -> None:
    """Infinite losses are rejected at construction."""
    with pytest.raises(ValueError, match="finite"):
        OutcomeSample(loss=math.inf)


def test_outcome_sample_rejects_negative_weight() -> None:
    """Negative weights are rejected."""
    with pytest.raises(ValueError, match="non-negative"):
        OutcomeSample(loss=1.0, weight=-0.5)


def test_compute_risk_empty_samples() -> None:
    """Empty sample list raises :class:`ValueError`."""
    with pytest.raises(ValueError, match="at least one"):
        compute_risk([])


def test_compute_risk_alpha_validation() -> None:
    """Alpha outside ``(0, 1)`` raises."""
    samples = [OutcomeSample(loss=1.0)]
    with pytest.raises(ValueError, match="alpha"):
        compute_risk(samples, alpha=0.0)
    with pytest.raises(ValueError, match="alpha"):
        compute_risk(samples, alpha=1.0)


def test_compute_risk_uniform_distribution() -> None:
    """Uniform losses → EV equals mean, CVaR equals upper-tail mean."""
    losses = [10.0, 20.0, 30.0, 40.0, 50.0]
    samples = [OutcomeSample(loss=v) for v in losses]
    estimate = compute_risk(samples, alpha=0.4)
    assert estimate.sample_size == 5
    assert estimate.ev == pytest.approx(30.0)
    # Worst 40% = top 2 of 5 = mean(40, 50) = 45
    assert estimate.cvar == pytest.approx(45.0)


def test_compute_risk_weighted() -> None:
    """Weighted samples produce a weighted EV."""
    samples = [
        OutcomeSample(loss=0.0, weight=9.0),
        OutcomeSample(loss=100.0, weight=1.0),
    ]
    estimate = compute_risk(samples, alpha=0.05)
    # EV = 0 * 0.9 + 100 * 0.1 = 10
    assert estimate.ev == pytest.approx(10.0)
    # CVaR is dominated by the 100 sample
    assert estimate.cvar == pytest.approx(100.0)


def test_compute_risk_zero_total_weight() -> None:
    """Total weight of zero raises."""
    with pytest.raises(ValueError, match="total weight"):
        compute_risk([OutcomeSample(loss=1.0, weight=0.0)])


def test_compute_risk_returns_sample_size() -> None:
    """``sample_size`` reflects input length."""
    samples = [OutcomeSample(loss=float(i)) for i in range(7)]
    estimate = compute_risk(samples)
    assert estimate.sample_size == 7
    assert estimate.alpha == pytest.approx(0.05)
