"""SQLAlchemy observation/belief store behaviour against in-memory SQLite.

Mirrors the in-memory Protocol contract (test_store.py) for the
persistent implementations, plus tenant isolation, append idempotence,
integrity-hash round-trip, and belief overwrite-on-promote.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.epistemic.models import (
    Belief,
    Observation,
    Provenance,
    ProvenanceKind,
    observation_integrity_hash,
)
from core.brain.epistemic.sa_store import (
    SQLAlchemyBeliefStore,
    SQLAlchemyObservationStore,
)
from models.brain_epistemic import BrainBelief, BrainObservation

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainObservation.__table__.create(engine)
    BrainBelief.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def obs_store(session_maker) -> SQLAlchemyObservationStore:
    return SQLAlchemyObservationStore(session_maker=session_maker, tenant_id=TENANT)


@pytest.fixture
def belief_store(session_maker) -> SQLAlchemyBeliefStore:
    return SQLAlchemyBeliefStore(session_maker=session_maker, tenant_id=TENANT)


def _observation(obs_id: str, *, subject: str = "property:p1:noise", value=True) -> Observation:
    recorded_at = datetime.now(UTC)
    provenance = Provenance(kind=ProvenanceKind.SENSOR, source_id="sensor-1", correlation_id="corr-1")
    return Observation(
        observation_id=obs_id,
        subject=subject,
        value=value,
        recorded_at=recorded_at,
        provenance=provenance,
        integrity_hex=observation_integrity_hash(
            observation_id=obs_id,
            subject=subject,
            value=value,
            recorded_at=recorded_at,
            provenance=provenance,
        ),
    )


def _belief(subject: str = "property:p1:noise", **overrides) -> Belief:
    base = {
        "belief_id": "b-1",
        "subject": subject,
        "promoted_value": "quiet",
        "wilson_lb": 0.7,
        "sample_size": 40,
        "supporting_observation_ids": ("o-1", "o-2"),
        "promoted_at": datetime.now(UTC),
        "promoted_by": "system",
    }
    base.update(overrides)
    return Belief(**base)


class TestObservationStore:
    def test_round_trip_preserves_integrity_hash(self, obs_store):
        obs = _observation("o-1")
        obs_store.record(obs)
        (loaded,) = obs_store.observations_for(obs.subject)
        assert loaded.observation_id == "o-1"
        assert loaded.value is True
        assert loaded.recorded_at.tzinfo is not None
        assert loaded.provenance == obs.provenance
        assert loaded.integrity_hex == obs.integrity_hex
        # hash still verifies against the rebuilt observation
        assert (
            observation_integrity_hash(
                observation_id=loaded.observation_id,
                subject=loaded.subject,
                value=loaded.value,
                recorded_at=loaded.recorded_at,
                provenance=loaded.provenance,
            )
            == loaded.integrity_hex
        )

    def test_insertion_order_oldest_first(self, obs_store):
        # recorded_at deliberately out of order: insertion order must win
        late = _observation("o-late")
        early = _observation("o-early")
        object.__setattr__(early, "recorded_at", late.recorded_at - timedelta(hours=2))
        obs_store.record(late)
        obs_store.record(early)
        ids = [o.observation_id for o in obs_store.observations_for("property:p1:noise")]
        assert ids == ["o-late", "o-early"]

    def test_record_is_idempotent_on_observation_id(self, obs_store, session_maker):
        obs = _observation("o-1")
        obs_store.record(obs)
        obs_store.record(obs)
        with session_maker() as session:
            assert session.query(BrainObservation).count() == 1

    def test_unknown_subject_returns_empty(self, obs_store):
        assert obs_store.observations_for("nothing") == ()

    def test_known_subjects(self, obs_store):
        obs_store.record(_observation("o-1", subject="s-a"))
        obs_store.record(_observation("o-2", subject="s-b"))
        obs_store.record(_observation("o-3", subject="s-a"))
        assert set(obs_store.known_subjects()) == {"s-a", "s-b"}

    def test_tenant_isolation(self, session_maker):
        a = SQLAlchemyObservationStore(session_maker=session_maker, tenant_id=TENANT)
        b = SQLAlchemyObservationStore(session_maker=session_maker, tenant_id=OTHER_TENANT)
        a.record(_observation("o-1"))
        assert b.observations_for("property:p1:noise") == ()
        assert b.known_subjects() == ()


class TestBeliefStore:
    def test_round_trip(self, belief_store):
        belief = _belief(extra={"note": "promoted nightly"})
        belief_store.promote(belief)
        loaded = belief_store.current(belief.subject)
        assert loaded is not None
        assert loaded.belief_id == belief.belief_id
        assert loaded.promoted_value == "quiet"
        assert loaded.wilson_lb == pytest.approx(0.7)
        assert loaded.supporting_observation_ids == ("o-1", "o-2")
        assert loaded.promoted_at.tzinfo is not None
        assert loaded.extra == {"note": "promoted nightly"}

    def test_promote_overwrites_current(self, belief_store, session_maker):
        belief_store.promote(_belief())
        belief_store.promote(_belief(belief_id="b-2", promoted_value="noisy", wilson_lb=0.8))
        with session_maker() as session:
            assert session.query(BrainBelief).count() == 1
        current = belief_store.current("property:p1:noise")
        assert current.belief_id == "b-2"
        assert current.promoted_value == "noisy"

    def test_current_missing_returns_none(self, belief_store):
        assert belief_store.current("nothing") is None

    def test_tenant_isolation(self, session_maker):
        a = SQLAlchemyBeliefStore(session_maker=session_maker, tenant_id=TENANT)
        b = SQLAlchemyBeliefStore(session_maker=session_maker, tenant_id=OTHER_TENANT)
        a.promote(_belief())
        assert b.current("property:p1:noise") is None
        assert b.known_subjects() == ()
        # same subject may hold independent beliefs per tenant
        b.promote(_belief(promoted_value="other"))
        assert a.current("property:p1:noise").promoted_value == "quiet"


def test_empty_tenant_rejected(session_maker):
    with pytest.raises(ValueError, match="tenant_id"):
        SQLAlchemyObservationStore(session_maker=session_maker, tenant_id="")
    with pytest.raises(ValueError, match="tenant_id"):
        SQLAlchemyBeliefStore(session_maker=session_maker, tenant_id="")
