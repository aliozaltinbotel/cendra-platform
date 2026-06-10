"""Persistent calibration store behaviour against SQLite."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.abstention.models import CalibrationSample
from core.brain.abstention.sa_store import SQLAlchemyCalibrationStore
from models.brain_calibration import BrainCalibrationSample

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainCalibrationSample.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _sample(i: int, *, success: bool = True) -> CalibrationSample:
    return CalibrationSample(
        tool_id="send_message",
        predicted_confidence=0.9,
        actual_success=success,
        recorded_at=datetime.now(UTC) + timedelta(seconds=i),
    )


def test_window_round_trip_oldest_first(session_maker):
    store = SQLAlchemyCalibrationStore(session_maker=session_maker, tenant_id=TENANT, window_size=3)
    for i in range(5):
        store.record(_sample(i, success=i % 2 == 0))
    window = store.samples_for("send_message")
    assert len(window) == 3  # pruned to window
    assert window[0].recorded_at < window[-1].recorded_at
    assert all(s.recorded_at.tzinfo is not None for s in window)


def test_tenant_isolation_and_clear(session_maker):
    a = SQLAlchemyCalibrationStore(session_maker=session_maker, tenant_id=TENANT)
    b = SQLAlchemyCalibrationStore(session_maker=session_maker, tenant_id="2" * 8 + "-2222-2222-2222-222222222222")
    a.record(_sample(0))
    assert b.samples_for("send_message") == ()
    a.clear("send_message")
    assert a.samples_for("send_message") == ()
