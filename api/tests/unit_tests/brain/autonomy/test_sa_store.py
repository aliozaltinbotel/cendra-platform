"""SQLAlchemy autonomy store + workflow-kind registry against SQLite."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.autonomy import (
    AutonomyEngine,
    AutonomyState,
    SQLAlchemyAutonomyStore,
    SQLAlchemyWorkflowKindRegistry,
    WorkflowAutonomy,
    WorkflowMetrics,
)
from models.brain_autonomy import BrainWorkflowAutonomy, BrainWorkflowKind

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainWorkflowAutonomy.__table__.create(engine)
    BrainWorkflowKind.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def store(session_maker) -> SQLAlchemyAutonomyStore:
    return SQLAlchemyAutonomyStore(session_maker=session_maker, tenant_id=TENANT)


def test_round_trip_and_upsert(store, session_maker):
    record = WorkflowAutonomy(
        property_id="p1",
        workflow="code_release",
        state=AutonomyState.SEMI_AUTO,
        metrics=WorkflowMetrics(sample_size=25, success_rate=0.9, override_rate=0.05),
        hold_seconds=120,
        changed_by="nightly",
        reason="promoted",
    )
    store.put(record)
    loaded = store.get(property_id="p1", workflow="code_release")
    assert loaded is not None
    assert loaded.state is AutonomyState.SEMI_AUTO
    assert loaded.metrics.sample_size == 25
    assert loaded.metrics.success_rate == pytest.approx(0.9)
    assert loaded.hold_seconds == 120
    assert loaded.changed_at.tzinfo is not None
    assert loaded.reason == "promoted"
    # upsert on (tenant, property, workflow)
    store.put(replace(record, state=AutonomyState.AUTOPILOT, reason="promoted again"))
    with session_maker() as session:
        assert session.query(BrainWorkflowAutonomy).count() == 1
    assert store.get(property_id="p1", workflow="code_release").state is AutonomyState.AUTOPILOT


def test_list_for_property_sorted(store):
    store.put(WorkflowAutonomy(property_id="p1", workflow="late_checkout"))
    store.put(WorkflowAutonomy(property_id="p1", workflow="code_release"))
    store.put(WorkflowAutonomy(property_id="p2", workflow="code_release"))
    assert [r.workflow for r in store.list_for_property("p1")] == [
        "code_release",
        "late_checkout",
    ]


def test_engine_runs_on_sqlalchemy_store(store):
    engine = AutonomyEngine(store=store)
    updated = engine.update_metrics(
        property_id="p1",
        workflow="code_release",
        metrics=WorkflowMetrics(
            sample_size=25,
            success_rate=0.85,
            override_rate=0.1,
            incidents=1,
            mean_latency_seconds=30.0,
        ),
    )
    assert updated.state is AutonomyState.SEMI_AUTO
    assert engine.state_for(property_id="p1", workflow="code_release") is AutonomyState.SEMI_AUTO


def test_tenant_isolation(session_maker):
    a = SQLAlchemyAutonomyStore(session_maker=session_maker, tenant_id=TENANT)
    b = SQLAlchemyAutonomyStore(session_maker=session_maker, tenant_id=OTHER_TENANT)
    a.put(WorkflowAutonomy(property_id="p1", workflow="w1", state=AutonomyState.AUTOPILOT))
    assert b.get(property_id="p1", workflow="w1") is None
    assert b.list_for_property("p1") == []


class TestRegistry:
    @pytest.fixture
    def registry(self, session_maker) -> SQLAlchemyWorkflowKindRegistry:
        with session_maker() as session:
            session.add(
                BrainWorkflowKind(
                    tenant_id=TENANT,
                    kind="code_release",
                    event_aliases=["send_access_code", "access_code_request"],
                )
            )
            session.add(BrainWorkflowKind(tenant_id=TENANT, kind="late_checkout"))
            session.add(
                BrainWorkflowKind(
                    tenant_id=TENANT,
                    kind="retired_kind",
                    enabled=False,
                )
            )
            session.add(BrainWorkflowKind(tenant_id=OTHER_TENANT, kind="other_vertical_kind"))
            session.commit()
        return SQLAlchemyWorkflowKindRegistry(session_maker=session_maker, tenant_id=TENANT)

    def test_kinds_are_tenant_scoped_and_enabled_only(self, registry):
        assert registry.kinds() == ("code_release", "late_checkout")

    def test_resolve_event_via_alias_case_insensitive(self, registry):
        assert registry.resolve_event("SEND_ACCESS_CODE") == "code_release"
        assert registry.resolve_event("late_checkout") == "late_checkout"
        assert registry.resolve_event("other_vertical_kind") is None
        assert registry.resolve_event("") is None
