"""Ledger accrual metrics over DecisionCase + calibration rows (CEN-32)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.brain_governance_service as gov
from core.brain.abstention.models import CalibrationSample
from core.brain.abstention.sa_store import SQLAlchemyCalibrationStore
from core.brain.patterns.case_store import SQLAlchemyDecisionCaseStore
from core.brain.patterns.models import DecisionAction, DecisionCase, DecisionType
from core.brain.patterns.shadow_verdict import SHADOW_KEY, WOULD_ABSTAIN, WOULD_ACT
from models.brain_autonomy import BrainWorkflowKind
from models.brain_calibration import BrainCalibrationSample
from models.brain_decision import BrainDecisionCase

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainDecisionCase.__table__.create(engine)
    BrainCalibrationSample.__table__.create(engine)
    BrainWorkflowKind.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def service(session_maker, monkeypatch):
    monkeypatch.setattr(gov, "_session_maker", lambda: session_maker)
    return gov.BrainGovernanceService(tenant_id=TENANT)


def _seed_workflow_registry(session_maker) -> None:
    with session_maker() as session:
        session.add_all(
            [
                BrainWorkflowKind(
                    tenant_id=TENANT,
                    kind="code_release",
                    event_aliases=["send_access_code"],
                    label="Access Code Release",
                ),
                BrainWorkflowKind(
                    tenant_id=TENANT,
                    kind="inquiry_reply",
                    event_aliases=["send_guest_message"],
                    label="Inquiry Reply",
                ),
            ]
        )
        session.commit()


def _store_case(
    session_maker,
    *,
    tenant_id: str,
    created_at: datetime,
    tool_id: str,
    verdict: str | None,
) -> None:
    store = SQLAlchemyDecisionCaseStore(session_maker=session_maker, tenant_id=tenant_id)
    orchestrator_verdict: dict[str, object] = {"source": "t7_capture", "tool_id": tool_id}
    if verdict is not None:
        orchestrator_verdict[SHADOW_KEY] = {"verdict": verdict}
    store.store(
        DecisionCase(
            created_at=created_at,
            stage="ops",
            scenario="general",
            property_id="app-1",
            owner_id=tenant_id,
            decision=DecisionAction(
                action_type=DecisionType.DISPATCH,
                params={"tool_id": tool_id},
            ),
            orchestrator_verdict=orchestrator_verdict,
        )
    )


def _store_sample(
    session_maker,
    *,
    tenant_id: str,
    recorded_at: datetime,
    tool_id: str,
    success: bool = True,
) -> None:
    store = SQLAlchemyCalibrationStore(session_maker=session_maker, tenant_id=tenant_id)
    store.record(
        CalibrationSample(
            tool_id=tool_id,
            predicted_confidence=0.9,
            actual_success=success,
            recorded_at=recorded_at,
        )
    )


def test_case_metrics_aggregate_by_day_workflow_verdict_and_coverage(service, session_maker):
    _seed_workflow_registry(session_maker)
    day_1 = datetime(2026, 6, 1, 10, tzinfo=UTC)
    day_2 = datetime(2026, 6, 2, 9, tzinfo=UTC)

    _store_case(session_maker, tenant_id=TENANT, created_at=day_1, tool_id="send_access_code", verdict=WOULD_ACT)
    _store_case(
        session_maker,
        tenant_id=TENANT,
        created_at=day_1 + timedelta(minutes=5),
        tool_id="send_access_code",
        verdict=WOULD_ABSTAIN,
    )
    _store_case(
        session_maker,
        tenant_id=TENANT,
        created_at=day_2,
        tool_id="send_guest_message",
        verdict=None,
    )
    _store_case(
        session_maker,
        tenant_id=OTHER_TENANT,
        created_at=day_1,
        tool_id="send_access_code",
        verdict=WOULD_ACT,
    )

    for offset in range(28):
        _store_sample(
            session_maker,
            tenant_id=TENANT,
            recorded_at=datetime(2026, 5, 20, 8, tzinfo=UTC) + timedelta(minutes=offset),
            tool_id="send_access_code",
        )
    _store_sample(session_maker, tenant_id=TENANT, recorded_at=day_1, tool_id="send_access_code")
    _store_sample(
        session_maker,
        tenant_id=TENANT,
        recorded_at=day_1 + timedelta(minutes=15),
        tool_id="send_access_code",
    )
    for offset in range(3):
        _store_sample(
            session_maker,
            tenant_id=TENANT,
            recorded_at=datetime(2026, 5, 25, 9, tzinfo=UTC) + timedelta(minutes=offset),
            tool_id="send_guest_message",
        )
    _store_sample(session_maker, tenant_id=TENANT, recorded_at=day_2, tool_id="send_guest_message")
    _store_sample(
        session_maker,
        tenant_id=TENANT,
        recorded_at=day_2 + timedelta(minutes=30),
        tool_id="send_guest_message",
    )
    _store_sample(session_maker, tenant_id=OTHER_TENANT, recorded_at=day_1, tool_id="send_access_code")

    metrics = service.case_metrics(date_from=date(2026, 6, 1), date_to=date(2026, 6, 2))

    assert metrics["capture_integrity"] == {
        "captured_count": 3,
        "dispatched_count": 4,
        "capture_rate": 0.75,
    }
    assert metrics["calibration_window"] == {
        "window_size": 200,
        "min_samples": 30,
        "active_workflow_count": 2,
        "covered_workflow_count": 1,
        "coverage_rate": 0.5,
    }
    assert metrics["by_verdict"] == {
        "would_act": 1,
        "would_abstain": 1,
        "unknown": 1,
    }
    assert metrics["by_day"] == [
        {
            "date": "2026-06-01",
            "captured_count": 2,
            "dispatched_count": 2,
            "verdict_counts": {
                "would_act": 1,
                "would_abstain": 1,
                "unknown": 0,
            },
        },
        {
            "date": "2026-06-02",
            "captured_count": 1,
            "dispatched_count": 2,
            "verdict_counts": {
                "would_act": 0,
                "would_abstain": 0,
                "unknown": 1,
            },
        },
    ]

    workflows = {row["workflow"]: row for row in metrics["by_workflow"]}
    assert workflows["code_release"] == {
        "workflow": "code_release",
        "label": "Access Code Release",
        "captured_count": 2,
        "dispatched_count": 2,
        "verdict_counts": {
            "would_act": 1,
            "would_abstain": 1,
            "unknown": 0,
        },
        "calibration_window": {
            "sample_size": 30,
            "covered": True,
        },
        "latest_case_at": (day_1 + timedelta(minutes=5)).isoformat(),
        "latest_dispatch_at": (day_1 + timedelta(minutes=15)).isoformat(),
    }
    assert workflows["inquiry_reply"] == {
        "workflow": "inquiry_reply",
        "label": "Inquiry Reply",
        "captured_count": 1,
        "dispatched_count": 2,
        "verdict_counts": {
            "would_act": 0,
            "would_abstain": 0,
            "unknown": 1,
        },
        "calibration_window": {
            "sample_size": 5,
            "covered": False,
        },
        "latest_case_at": day_2.isoformat(),
        "latest_dispatch_at": (day_2 + timedelta(minutes=30)).isoformat(),
    }


def test_case_metrics_returns_zeroed_window_when_only_other_tenant_has_activity(service, session_maker):
    _seed_workflow_registry(session_maker)
    _store_case(
        session_maker,
        tenant_id=OTHER_TENANT,
        created_at=datetime(2026, 6, 1, 10, tzinfo=UTC),
        tool_id="send_access_code",
        verdict=WOULD_ACT,
    )
    _store_sample(
        session_maker,
        tenant_id=OTHER_TENANT,
        recorded_at=datetime(2026, 6, 1, 11, tzinfo=UTC),
        tool_id="send_access_code",
    )

    metrics = service.case_metrics(date_from=date(2026, 6, 1), date_to=date(2026, 6, 2))

    assert metrics["capture_integrity"] == {
        "captured_count": 0,
        "dispatched_count": 0,
        "capture_rate": None,
    }
    assert metrics["calibration_window"] == {
        "window_size": 200,
        "min_samples": 30,
        "active_workflow_count": 0,
        "covered_workflow_count": 0,
        "coverage_rate": None,
    }
    assert metrics["by_workflow"] == []
    assert metrics["by_verdict"] == {
        "would_act": 0,
        "would_abstain": 0,
        "unknown": 0,
    }
    assert metrics["by_day"] == [
        {
            "date": "2026-06-01",
            "captured_count": 0,
            "dispatched_count": 0,
            "verdict_counts": {
                "would_act": 0,
                "would_abstain": 0,
                "unknown": 0,
            },
        },
        {
            "date": "2026-06-02",
            "captured_count": 0,
            "dispatched_count": 0,
            "verdict_counts": {
                "would_act": 0,
                "would_abstain": 0,
                "unknown": 0,
            },
        },
    ]


def test_case_metrics_can_scope_to_one_workflow_alias_and_report_recency(service, session_maker):
    _seed_workflow_registry(session_maker)
    day_1 = datetime(2026, 6, 1, 10, tzinfo=UTC)
    day_2 = datetime(2026, 6, 2, 9, tzinfo=UTC)

    _store_case(session_maker, tenant_id=TENANT, created_at=day_1, tool_id="send_access_code", verdict=WOULD_ACT)
    _store_case(
        session_maker,
        tenant_id=TENANT,
        created_at=day_1 + timedelta(minutes=5),
        tool_id="send_access_code",
        verdict=WOULD_ABSTAIN,
    )
    _store_case(
        session_maker,
        tenant_id=TENANT,
        created_at=day_2,
        tool_id="send_guest_message",
        verdict=WOULD_ACT,
    )
    _store_sample(session_maker, tenant_id=TENANT, recorded_at=day_1, tool_id="send_access_code")
    _store_sample(
        session_maker,
        tenant_id=TENANT,
        recorded_at=day_1 + timedelta(minutes=15),
        tool_id="send_access_code",
    )
    _store_sample(session_maker, tenant_id=TENANT, recorded_at=day_2, tool_id="send_guest_message")

    metrics = service.case_metrics(
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 2),
        workflow="send_access_code",
    )

    assert metrics["capture_integrity"] == {
        "captured_count": 2,
        "dispatched_count": 2,
        "capture_rate": 1.0,
    }
    assert metrics["calibration_window"] == {
        "window_size": 200,
        "min_samples": 30,
        "active_workflow_count": 1,
        "covered_workflow_count": 0,
        "coverage_rate": 0.0,
    }
    assert metrics["by_verdict"] == {
        "would_act": 1,
        "would_abstain": 1,
        "unknown": 0,
    }
    assert metrics["by_day"] == [
        {
            "date": "2026-06-01",
            "captured_count": 2,
            "dispatched_count": 2,
            "verdict_counts": {
                "would_act": 1,
                "would_abstain": 1,
                "unknown": 0,
            },
        },
        {
            "date": "2026-06-02",
            "captured_count": 0,
            "dispatched_count": 0,
            "verdict_counts": {
                "would_act": 0,
                "would_abstain": 0,
                "unknown": 0,
            },
        },
    ]
    assert metrics["by_workflow"] == [
        {
            "workflow": "code_release",
            "label": "Access Code Release",
            "captured_count": 2,
            "dispatched_count": 2,
            "verdict_counts": {
                "would_act": 1,
                "would_abstain": 1,
                "unknown": 0,
            },
            "calibration_window": {
                "sample_size": 2,
                "covered": False,
            },
            "latest_case_at": (day_1 + timedelta(minutes=5)).isoformat(),
            "latest_dispatch_at": (day_1 + timedelta(minutes=15)).isoformat(),
        }
    ]
