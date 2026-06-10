"""Behaviour of :class:`BayesianPromotionGate`."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from brain_engine.ab_promotion.gate import BayesianPromotionGate
from brain_engine.ab_promotion.models import (
    ArmStats,
    PromotionVerdict,
)


@pytest.fixture
def gate() -> BayesianPromotionGate:
    return BayesianPromotionGate(
        threshold=0.95,
        samples=4_000,
        min_trials=30,
        rng=random.Random(42),
    )


def _now() -> datetime:
    return datetime(2026, 5, 11, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"threshold": 1.5}, "threshold"),
        ({"threshold": -0.1}, "threshold"),
        ({"samples": 0}, "samples"),
        ({"min_trials": 0}, "min_trials"),
    ],
    ids=[
        "threshold_above_one",
        "threshold_below_zero",
        "zero_samples",
        "zero_min_trials",
    ],
)
def test_constructor_validation(
    override: dict[str, float],
    match: str,
) -> None:
    """Each invariant is enforced fail-fast."""
    defaults: dict[str, float] = {
        "threshold": 0.5,
        "samples": 100,
        "min_trials": 10,
    }
    defaults.update(override)
    with pytest.raises(ValueError, match=match):
        BayesianPromotionGate(**defaults)  # type: ignore[arg-type]


def test_thin_data_returns_insufficient(
    gate: BayesianPromotionGate,
) -> None:
    """Below ``min_trials`` returns INSUFFICIENT_DATA."""
    decision = gate.evaluate(
        champion=ArmStats(name="c", successes=5, trials=10),
        challenger=ArmStats(name="x", successes=8, trials=10),
        at=_now(),
    )
    assert decision.verdict is PromotionVerdict.INSUFFICIENT_DATA
    assert decision.samples == 0


def test_clear_improvement_promotes(
    gate: BayesianPromotionGate,
) -> None:
    """60% vs 80% on N=100 → PROMOTE with high probability."""
    decision = gate.evaluate(
        champion=ArmStats(name="c", successes=60, trials=100),
        challenger=ArmStats(name="x", successes=80, trials=100),
        at=_now(),
    )
    assert decision.verdict is PromotionVerdict.PROMOTE
    assert decision.prob_challenger_beats_champion >= 0.95


def test_marginal_keeps_champion(
    gate: BayesianPromotionGate,
) -> None:
    """Tiny edge under noise → KEEP_CHAMPION."""
    decision = gate.evaluate(
        champion=ArmStats(name="c", successes=70, trials=100),
        challenger=ArmStats(name="x", successes=72, trials=100),
        at=_now(),
    )
    assert decision.verdict is PromotionVerdict.KEEP_CHAMPION
    assert decision.prob_challenger_beats_champion < 0.95


def test_challenger_worse_keeps_champion(
    gate: BayesianPromotionGate,
) -> None:
    """If challenger has worse rate the gate clearly keeps champion."""
    decision = gate.evaluate(
        champion=ArmStats(name="c", successes=80, trials=100),
        challenger=ArmStats(name="x", successes=50, trials=100),
        at=_now(),
    )
    assert decision.verdict is PromotionVerdict.KEEP_CHAMPION
    assert decision.prob_challenger_beats_champion < 0.05


def test_decision_metadata_complete(
    gate: BayesianPromotionGate,
) -> None:
    """The decision carries every input the audit log needs."""
    champion = ArmStats(name="c", successes=60, trials=100)
    challenger = ArmStats(name="x", successes=80, trials=100)
    decision = gate.evaluate(
        champion=champion,
        challenger=challenger,
        at=_now(),
    )
    assert decision.champion is champion
    assert decision.challenger is challenger
    assert decision.threshold == 0.95
    assert decision.samples == 4_000
    assert decision.evaluated_at == _now()


def test_naive_at_rejected(
    gate: BayesianPromotionGate,
) -> None:
    """Caller-supplied tz-naive timestamp is rejected."""
    with pytest.raises(ValueError, match="tz-aware"):
        gate.evaluate(
            champion=ArmStats(name="c", successes=60, trials=100),
            challenger=ArmStats(name="x", successes=80, trials=100),
            at=datetime(2026, 5, 11),
        )


def test_default_at_is_aware(
    gate: BayesianPromotionGate,
) -> None:
    """The default clock yields a tz-aware UTC instant."""
    decision = gate.evaluate(
        champion=ArmStats(name="c", successes=60, trials=100),
        challenger=ArmStats(name="x", successes=80, trials=100),
    )
    assert decision.evaluated_at.tzinfo is timezone.utc


def test_seeded_rng_reproducible() -> None:
    """Same seed + inputs produce the same probability estimate."""
    a = BayesianPromotionGate(
        samples=2_000, min_trials=10, rng=random.Random(7),
    )
    b = BayesianPromotionGate(
        samples=2_000, min_trials=10, rng=random.Random(7),
    )
    champion = ArmStats(name="c", successes=60, trials=100)
    challenger = ArmStats(name="x", successes=80, trials=100)
    pa = a.evaluate(
        champion=champion, challenger=challenger, at=_now(),
    )
    pb = b.evaluate(
        champion=champion, challenger=challenger, at=_now(),
    )
    assert (
        pa.prob_challenger_beats_champion
        == pb.prob_challenger_beats_champion
    )


def test_zero_trials_short_circuits_to_insufficient(
    gate: BayesianPromotionGate,
) -> None:
    """Empty arms → INSUFFICIENT_DATA without dividing by zero."""
    decision = gate.evaluate(
        champion=ArmStats(name="c", successes=0, trials=0),
        challenger=ArmStats(name="x", successes=0, trials=0),
        at=_now(),
    )
    assert decision.verdict is PromotionVerdict.INSUFFICIENT_DATA
