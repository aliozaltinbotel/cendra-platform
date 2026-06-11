"""Controller contract for the Cendra brain service_api endpoints."""

from __future__ import annotations

import inspect
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask

import controllers.service_api.brain as brain_api

TENANT = "11111111-1111-1111-1111-111111111111"


def unwrap(func):
    return inspect.unwrap(func)


def test_brain_case_metrics_api_delegates_with_tenant_scoped_query(app: Flask, monkeypatch):
    service = Mock()
    service.case_metrics.return_value = {
        "date_from": "2026-06-01",
        "date_to": "2026-06-07",
        "generated_at": "2026-06-11T10:00:00+00:00",
        "capture_integrity": {
            "captured_count": 3,
            "dispatched_count": 4,
            "capture_rate": 0.75,
        },
        "calibration_window": {
            "window_size": 200,
            "min_samples": 30,
            "active_workflow_count": 1,
            "covered_workflow_count": 1,
            "coverage_rate": 1.0,
        },
        "by_day": [
            {
                "date": "2026-06-01",
                "captured_count": 3,
                "dispatched_count": 4,
                "verdict_counts": {
                    "would_act": 1,
                    "would_abstain": 1,
                    "unknown": 1,
                },
            }
        ],
        "by_workflow": [
            {
                "workflow": "code_release",
                "label": "Access Code Release",
                "captured_count": 3,
                "dispatched_count": 4,
                "verdict_counts": {
                    "would_act": 1,
                    "would_abstain": 1,
                    "unknown": 1,
                },
                "calibration_window": {
                    "sample_size": 30,
                    "covered": True,
                },
                "latest_case_at": "2026-06-01T10:05:00+00:00",
                "latest_dispatch_at": "2026-06-01T10:15:00+00:00",
            }
        ],
        "by_verdict": {
            "would_act": 1,
            "would_abstain": 1,
            "unknown": 1,
        },
    }
    service_cls = Mock(return_value=service)
    monkeypatch.setattr(brain_api, "BrainGovernanceService", service_cls)

    with app.test_request_context(
        "/brain/cases/metrics?date_from=2026-06-01&date_to=2026-06-07&workflow=code_release",
        method="GET",
    ):
        api = brain_api.BrainCasesMetricsApi()
        response, status = unwrap(api.get)(api, app_model=SimpleNamespace(tenant_id=TENANT))

    assert status == 200
    assert response["capture_integrity"]["capture_rate"] == 0.75
    assert response["by_workflow"][0]["label"] == "Access Code Release"
    assert response["by_workflow"][0]["latest_case_at"] == "2026-06-01T10:05:00Z"
    service_cls.assert_called_once_with(TENANT)
    service.case_metrics.assert_called_once_with(
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 7),
        workflow="code_release",
    )


def test_brain_gate_posture_api_forwards_api_token_actor(app: Flask, monkeypatch):
    service = Mock()
    service.set_posture.return_value = {
        "tenant_id": TENANT,
        "override_posture": "observe",
        "changed_at": "2026-06-11T09:00:00+00:00",
        "changed_by": "api_key:token-1",
        "reason": "activate tenant",
        "resolution": {
            "configured_mode": "observe",
            "effective_mode": "observe",
            "tenant_enabled": False,
            "override_mode": "observe",
            "source": "tenant_override",
            "active": True,
        },
    }
    service_cls = Mock(return_value=service)
    monkeypatch.setattr(brain_api, "BrainGatePostureService", service_cls)
    monkeypatch.setattr(brain_api, "validate_and_get_api_token", lambda scope: SimpleNamespace(id="token-1"))

    with app.test_request_context(
        "/brain/gate-posture",
        method="PUT",
        json={"posture": "observe", "reason": "activate tenant"},
    ):
        api = brain_api.BrainGatePostureApi()
        response, status = unwrap(api.put)(api, app_model=SimpleNamespace(tenant_id=TENANT))

    assert status == 200
    assert response["changed_by"] == "api_key:token-1"
    service_cls.assert_called_once_with(TENANT)
    service.set_posture.assert_called_once_with(
        posture="observe",
        reason="activate tenant",
        actor_kind="api_key",
        actor_id="token-1",
    )


def test_brain_gate_posture_audit_api_scopes_limit_to_tenant(app: Flask, monkeypatch):
    service = Mock()
    service.list_audit.return_value = [
        {
            "actor_type": "api_key",
            "actor_id": "token-1",
            "changed_by": "api_key:token-1",
            "prior_posture": "off",
            "new_posture": "observe",
            "prior_effective_posture": "off",
            "new_effective_posture": "observe",
            "changed_at": "2026-06-11T09:00:00+00:00",
            "reason": "activate tenant",
        }
    ]
    service_cls = Mock(return_value=service)
    monkeypatch.setattr(brain_api, "BrainGatePostureService", service_cls)

    with app.test_request_context("/brain/gate-posture/audit?limit=5", method="GET"):
        api = brain_api.BrainGatePostureAuditApi()
        response, status = unwrap(api.get)(api, app_model=SimpleNamespace(tenant_id=TENANT))

    assert status == 200
    assert response["records"][0]["new_posture"] == "observe"
    service_cls.assert_called_once_with(TENANT)
    service.list_audit.assert_called_once_with(limit=5)
