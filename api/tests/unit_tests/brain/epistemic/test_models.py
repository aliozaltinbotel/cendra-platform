"""Invariants of :class:`Observation` / :class:`Belief` / :class:`Provenance`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from core.brain.epistemic.models import (
    Belief,
    Observation,
    Provenance,
    ProvenanceKind,
    canonical_observation_payload,
    observation_integrity_hash,
)


def _prov() -> Provenance:
    return Provenance(
        kind=ProvenanceKind.SENSOR,
        source_id="sensor-1",
    )


def _now() -> datetime:
    return datetime(2026, 5, 10, 12, 0, tzinfo=UTC)


def _observation(**overrides: Any) -> Observation:
    base: dict[str, Any] = {
        "observation_id": "obs-1",
        "subject": "property:p1:noise_db",
        "value": 42.0,
        "recorded_at": _now(),
        "provenance": _prov(),
    }
    base.update(overrides)
    base["integrity_hex"] = base.get(
        "integrity_hex",
        observation_integrity_hash(
            observation_id=base["observation_id"],
            subject=base["subject"],
            value=base["value"],
            recorded_at=base["recorded_at"],
            provenance=base["provenance"],
        ),
    )
    return Observation(**base)


def test_provenance_requires_source_id() -> None:
    with pytest.raises(ValueError, match="source_id"):
        Provenance(kind=ProvenanceKind.SENSOR, source_id="")


def test_observation_immutable() -> None:
    obs = _observation()
    with pytest.raises((AttributeError, TypeError)):
        obs.observation_id = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"observation_id": ""}, "observation_id"),
        ({"subject": ""}, "subject"),
        ({"recorded_at": datetime(2026, 5, 10)}, "recorded_at"),
        ({"integrity_hex": "abc"}, "integrity_hex"),
    ],
    ids=[
        "empty_id",
        "empty_subject",
        "naive_recorded_at",
        "short_integrity",
    ],
)
def test_observation_validation(
    override: dict[str, Any],
    match: str,
) -> None:
    """Each invariant is enforced at construction."""
    base: dict[str, Any] = {
        "observation_id": "obs-1",
        "subject": "subj",
        "value": 1,
        "recorded_at": _now(),
        "provenance": _prov(),
        "integrity_hex": "0" * 64,
    }
    base.update(override)
    with pytest.raises(ValueError, match=match):
        Observation(**base)


def test_canonical_payload_is_deterministic() -> None:
    """Two calls with identical inputs return identical bytes."""
    a = canonical_observation_payload(
        observation_id="o",
        subject="s",
        value=1,
        recorded_at=_now(),
        provenance=_prov(),
    )
    b = canonical_observation_payload(
        observation_id="o",
        subject="s",
        value=1,
        recorded_at=_now(),
        provenance=_prov(),
    )
    assert a == b


def test_integrity_hash_64_hex() -> None:
    """Integrity hash is 64-char BLAKE2B-256 hex."""
    digest = observation_integrity_hash(
        observation_id="o",
        subject="s",
        value=1,
        recorded_at=_now(),
        provenance=_prov(),
    )
    assert len(digest) == 64
    int(digest, 16)


def test_integrity_hash_changes_with_payload() -> None:
    """Mutating any input produces a different hash."""
    a = observation_integrity_hash(
        observation_id="o",
        subject="s",
        value=1,
        recorded_at=_now(),
        provenance=_prov(),
    )
    b = observation_integrity_hash(
        observation_id="o",
        subject="s",
        value=2,
        recorded_at=_now(),
        provenance=_prov(),
    )
    assert a != b


def test_belief_validation() -> None:
    """Each Belief invariant is enforced."""
    base: dict[str, Any] = {
        "belief_id": "b1",
        "subject": "s",
        "promoted_value": "x",
        "wilson_lb": 0.7,
        "sample_size": 40,
        "supporting_observation_ids": ("o1",),
        "promoted_at": _now(),
        "promoted_by": "system",
    }
    Belief(**base)  # baseline ok
    with pytest.raises(ValueError, match="wilson_lb"):
        Belief(**{**base, "wilson_lb": 1.1})
    with pytest.raises(ValueError, match="sample_size"):
        Belief(**{**base, "sample_size": -1})
    with pytest.raises(ValueError, match="promoted_by"):
        Belief(**{**base, "promoted_by": ""})
    with pytest.raises(ValueError, match="promoted_at"):
        Belief(**{**base, "promoted_at": datetime(2026, 5, 10)})
