"""Behaviour of in-memory observation / belief stores."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.epistemic.models import (
    Belief,
    Observation,
    Provenance,
    ProvenanceKind,
    observation_integrity_hash,
)
from brain_engine.epistemic.store import (
    InMemoryBeliefStore,
    InMemoryObservationStore,
)


def _obs(*, subject: str, value: object) -> Observation:
    moment = datetime(2026, 5, 10, tzinfo=timezone.utc)
    prov = Provenance(
        kind=ProvenanceKind.SENSOR,
        source_id="sensor",
    )
    digest = observation_integrity_hash(
        observation_id=f"obs-{subject}-{value}",
        subject=subject,
        value=value,
        recorded_at=moment,
        provenance=prov,
    )
    return Observation(
        observation_id=f"obs-{subject}-{value}",
        subject=subject,
        value=value,
        recorded_at=moment,
        provenance=prov,
        integrity_hex=digest,
    )


def test_observation_store_round_trip() -> None:
    store = InMemoryObservationStore()
    a = _obs(subject="s1", value=1)
    b = _obs(subject="s1", value=2)
    store.record(a)
    store.record(b)
    assert store.observations_for("s1") == (a, b)
    assert store.observations_for("missing") == ()


def test_observation_store_known_subjects() -> None:
    store = InMemoryObservationStore()
    store.record(_obs(subject="a", value=1))
    store.record(_obs(subject="b", value=1))
    assert set(store.known_subjects()) == {"a", "b"}


def test_belief_store_promote_overwrites() -> None:
    store = InMemoryBeliefStore()
    moment = datetime(2026, 5, 10, tzinfo=timezone.utc)
    earlier = Belief(
        belief_id="b1",
        subject="s",
        promoted_value="v1",
        wilson_lb=0.7,
        sample_size=40,
        supporting_observation_ids=("o",),
        promoted_at=moment,
        promoted_by="system",
    )
    later = Belief(
        belief_id="b2",
        subject="s",
        promoted_value="v2",
        wilson_lb=0.8,
        sample_size=80,
        supporting_observation_ids=("o", "o2"),
        promoted_at=moment,
        promoted_by="system",
    )
    store.promote(earlier)
    assert store.current("s") is earlier
    store.promote(later)
    assert store.current("s") is later


def test_belief_store_unknown_subject_returns_none() -> None:
    store = InMemoryBeliefStore()
    assert store.current("missing") is None


def test_belief_store_known_subjects() -> None:
    store = InMemoryBeliefStore()
    moment = datetime(2026, 5, 10, tzinfo=timezone.utc)
    store.promote(
        Belief(
            belief_id="b",
            subject="alpha",
            promoted_value=1,
            wilson_lb=0.7,
            sample_size=30,
            supporting_observation_ids=("o",),
            promoted_at=moment,
            promoted_by="system",
        )
    )
    assert store.known_subjects() == ("alpha",)
