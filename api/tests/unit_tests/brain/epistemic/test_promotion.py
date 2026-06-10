"""Behaviour of :class:`BeliefPromotionGate`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.brain.epistemic.models import (
    Observation,
    Provenance,
    ProvenanceKind,
    observation_integrity_hash,
)
from core.brain.epistemic.promotion import (
    BeliefPromotionGate,
    PromotionRefusal,
    predicate_truthy,
)


def _obs(*, subject: str, value: object, idx: int) -> Observation:
    moment = datetime(2026, 5, 10, tzinfo=UTC)
    prov = Provenance(
        kind=ProvenanceKind.SENSOR,
        source_id="sensor",
    )
    digest = observation_integrity_hash(
        observation_id=f"obs-{idx}",
        subject=subject,
        value=value,
        recorded_at=moment,
        provenance=prov,
    )
    return Observation(
        observation_id=f"obs-{idx}",
        subject=subject,
        value=value,
        recorded_at=moment,
        provenance=prov,
        integrity_hex=digest,
    )


def _seed(
    *,
    subject: str,
    successes: int,
    failures: int,
) -> list[Observation]:
    out: list[Observation] = []
    counter = 0
    for _ in range(successes):
        out.append(_obs(subject=subject, value=True, idx=counter))
        counter += 1
    for _ in range(failures):
        out.append(_obs(subject=subject, value=False, idx=counter))
        counter += 1
    return out


@pytest.fixture
def gate() -> BeliefPromotionGate:
    return BeliefPromotionGate(
        min_samples=30,
        wilson_threshold=0.6,
    )


def test_thin_window_refused(gate: BeliefPromotionGate) -> None:
    """Sample size below the floor refuses promotion."""
    obs = _seed(subject="s", successes=5, failures=0)
    with pytest.raises(PromotionRefusal, match="min_samples"):
        gate.promote(
            subject="s",
            observations=obs,
            promoted_value="x",
        )


def test_low_wilson_refused(gate: BeliefPromotionGate) -> None:
    """Wilson LB below the threshold refuses promotion."""
    obs = _seed(subject="s", successes=18, failures=22)
    with pytest.raises(PromotionRefusal, match="wilson_lb"):
        gate.promote(
            subject="s",
            observations=obs,
            promoted_value="x",
        )


def test_strong_window_promotes(
    gate: BeliefPromotionGate,
) -> None:
    """Enough successes + samples promote a belief."""
    obs = _seed(subject="s", successes=35, failures=0)
    belief = gate.promote(
        subject="s",
        observations=obs,
        promoted_value="reliable",
    )
    assert belief.subject == "s"
    assert belief.sample_size == 35
    assert belief.promoted_value == "reliable"
    assert belief.wilson_lb >= 0.6


def test_subject_mismatch_raises(
    gate: BeliefPromotionGate,
) -> None:
    """Observation pointing at a different subject errors out."""
    obs = _seed(subject="s1", successes=30, failures=0)
    with pytest.raises(ValueError, match="subject"):
        gate.promote(
            subject="s2",
            observations=obs,
            promoted_value="x",
        )


def test_evaluate_returns_size_and_wilson(
    gate: BeliefPromotionGate,
) -> None:
    """evaluate() returns the diagnostic pair."""
    obs = _seed(subject="s", successes=40, failures=0)
    sample_size, wilson_lb = gate.evaluate(obs)
    assert sample_size == 40
    assert wilson_lb >= 0.6


def test_predicate_truthy_default() -> None:
    """predicate_truthy maps zero / False / None to failure."""
    moment = datetime(2026, 5, 10, tzinfo=UTC)
    prov = Provenance(
        kind=ProvenanceKind.SENSOR,
        source_id="x",
    )
    digest = observation_integrity_hash(
        observation_id="o",
        subject="s",
        value=False,
        recorded_at=moment,
        provenance=prov,
    )
    obs = Observation(
        observation_id="o",
        subject="s",
        value=False,
        recorded_at=moment,
        provenance=prov,
        integrity_hex=digest,
    )
    assert predicate_truthy(obs) is False


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"min_samples": 0}, "min_samples"),
        ({"wilson_threshold": 1.5}, "wilson_threshold"),
        ({"z": 0.0}, "z"),
    ],
    ids=["zero_min", "wilson_above_one", "zero_z"],
)
def test_constructor_validation(
    override: dict[str, float],
    match: str,
) -> None:
    """Out-of-range constructor params fail fast."""
    defaults: dict[str, float] = {
        "min_samples": 10,
        "wilson_threshold": 0.5,
        "z": 1.96,
    }
    defaults.update(override)
    with pytest.raises(ValueError, match=match):
        BeliefPromotionGate(**defaults)  # type: ignore[arg-type]
