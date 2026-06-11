"""SQLAlchemy gap store against SQLite (tenant-scoped, append-only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.abstention.gap_registry import GapRecord, GapStatus
from core.brain.abstention.sa_gap_store import SQLAlchemyGapStore
from models.brain_gap import BrainGapRecord

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"

T = datetime(2026, 6, 10, 22, 3, 11, tzinfo=UTC)


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainGapRecord.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def store(session_maker) -> SQLAlchemyGapStore:
    return SQLAlchemyGapStore(session_maker=session_maker, tenant_id=TENANT)


def _record(*, run_id: str = "run-1", as_of: datetime = T, missing_predicate: str = "quiet_hours") -> GapRecord:
    return GapRecord(
        gap_id=f"gap-{run_id}",
        subject_ref="prop-1",
        run_id=run_id,
        query="what are the quiet hours?",
        missing_predicate=missing_predicate,
        confidence=0.41,
        threshold=0.75,
        wilson_lb=0.41,
        as_of=as_of,
        dispatched_at=as_of + timedelta(seconds=1),
        kg_snapshot_ref=f"brain:kg:prop-1@{as_of.isoformat()}",
    )


def test_round_trip_preserves_decision_time_provenance(store):
    store.record(_record())
    (loaded,) = store.list_for("prop-1")
    assert loaded.gap_id == "gap-run-1"
    assert loaded.as_of == T
    assert loaded.as_of.tzinfo is not None
    assert loaded.dispatched_at == T + timedelta(seconds=1)
    assert loaded.kg_snapshot_ref == f"brain:kg:prop-1@{T.isoformat()}"
    assert loaded.threshold == pytest.approx(0.75)
    assert loaded.wilson_lb == pytest.approx(0.41)
    assert loaded.status is GapStatus.OPEN


def test_per_event_rows_accumulate_newest_first(store):
    store.record(_record(run_id="run-0", as_of=T - timedelta(days=1)))
    store.record(_record(run_id="run-1"))
    rows = store.list_for("prop-1")
    assert [r.run_id for r in rows] == ["run-1", "run-0"]
    assert store.list_for("prop-1", limit=1)[0].run_id == "run-1"


def test_status_filter_and_predicate_grained_lifecycle(store):
    store.record(_record(run_id="run-0", as_of=T - timedelta(days=1)))
    store.record(_record(run_id="run-1"))
    store.record(_record(run_id="run-2", missing_predicate="parking_rules"))
    assert store.mark_status(subject_ref="prop-1", missing_predicate="quiet_hours", status=GapStatus.ANSWERED) == 2
    assert {r.run_id for r in store.list_for("prop-1", status=GapStatus.ANSWERED)} == {"run-0", "run-1"}
    assert {r.run_id for r in store.list_for("prop-1", status=GapStatus.OPEN)} == {"run-2"}
    # second transition is a no-op
    assert store.mark_status(subject_ref="prop-1", missing_predicate="quiet_hours", status=GapStatus.ANSWERED) == 0


def test_tenant_isolation(store, session_maker):
    store.record(_record())
    other = SQLAlchemyGapStore(session_maker=session_maker, tenant_id=OTHER_TENANT)
    assert other.list_for("prop-1") == ()
    assert other.mark_status(subject_ref="prop-1", missing_predicate="quiet_hours", status=GapStatus.DISMISSED) == 0


def test_tenant_required(session_maker):
    with pytest.raises(ValueError, match="tenant_id"):
        SQLAlchemyGapStore(session_maker=session_maker, tenant_id="")
