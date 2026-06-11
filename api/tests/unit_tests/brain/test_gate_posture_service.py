"""Per-tenant observe posture service + console API contract (CEN-31)."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import controllers.console.brain as brain_controller
import services.brain_gate_posture_service as posture_service_module
from models.brain_gate_posture import (
    BrainTenantGatePosture,
    BrainTenantGatePostureAudit,
)
from services.brain_gate_posture_service import (
    BrainGatePostureService,
    ObserveOnlyGatePostureWriteError,
)

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_GATES_MODE", raising=False)
    monkeypatch.delenv("BRAIN_GATES_TENANTS", raising=False)


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainTenantGatePosture.__table__.create(engine)
    BrainTenantGatePostureAudit.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def service(session_maker, monkeypatch) -> BrainGatePostureService:
    monkeypatch.setattr(posture_service_module, "_session_maker", lambda: session_maker)
    return BrainGatePostureService(tenant_id=TENANT)


class TestBrainGatePostureService:
    def test_observe_override_bypasses_legacy_allowlist_and_is_audited(self, service, monkeypatch) -> None:
        monkeypatch.setenv("BRAIN_GATES_MODE", "observe")
        monkeypatch.setenv("BRAIN_GATES_TENANTS", "some-other-tenant")

        payload = service.set_posture(
            posture="observe",
            reason="design partner activation",
            actor_kind="api_key",
            actor_id="token-1",
        )

        assert payload["override_posture"] == "observe"
        assert payload["changed_by"] == "api_key:token-1"
        assert payload["resolution"] == {
            "configured_mode": "observe",
            "effective_mode": "observe",
            "tenant_enabled": False,
            "override_mode": "observe",
            "source": "tenant_override",
            "active": True,
        }

        audit = service.list_audit()
        assert audit == [
            {
                "actor_type": "api_key",
                "actor_id": "token-1",
                "changed_by": "api_key:token-1",
                "prior_posture": "off",
                "new_posture": "observe",
                "prior_effective_posture": "off",
                "new_effective_posture": "observe",
                "changed_at": audit[0]["changed_at"],
                "reason": "design partner activation",
            }
        ]

    def test_off_override_disables_allowlisted_tenant_and_appends_audit(self, service, monkeypatch) -> None:
        monkeypatch.setenv("BRAIN_GATES_MODE", "observe")

        service.set_posture(
            posture="observe",
            reason="initial opt in",
            actor_kind="account",
            actor_id="account-1",
        )
        payload = service.set_posture(
            posture="off",
            reason="rollback",
            actor_kind="account",
            actor_id="account-2",
        )

        assert payload["override_posture"] == "off"
        assert payload["changed_by"] == "account:account-2"
        assert payload["resolution"]["effective_mode"] == "off"
        assert payload["resolution"]["source"] == "tenant_override"

        audit = service.list_audit(limit=10)
        assert [row["new_posture"] for row in audit] == ["off", "observe"]
        assert audit[0]["prior_posture"] == "observe"
        assert audit[0]["prior_effective_posture"] == "observe"
        assert audit[0]["new_effective_posture"] == "off"

    def test_writes_are_rejected_when_configured_enforce(self, service, monkeypatch) -> None:
        monkeypatch.setenv("BRAIN_GATES_MODE", "enforce")
        with pytest.raises(ObserveOnlyGatePostureWriteError):
            service.set_posture(
                posture="off",
                reason="out of scope in enforce",
                actor_kind="account",
                actor_id="account-1",
            )


class TestConsoleBrainGatePostureApi:
    def test_get_reads_current_posture(self, app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            brain_controller,
            "current_user",
            SimpleNamespace(current_tenant_id=TENANT, id="account-7"),
        )
        monkeypatch.setattr(
            brain_controller,
            "_gate_posture_service",
            lambda: SimpleNamespace(get_posture=lambda: {"tenant_id": TENANT, "override_posture": "observe"}),
        )

        with app.test_request_context("/console/api/brain/gate-posture"):
            payload = inspect.unwrap(brain_controller.ConsoleBrainGatePostureApi.get)(
                brain_controller.ConsoleBrainGatePostureApi()
            )

        assert payload == {"tenant_id": TENANT, "override_posture": "observe"}

    def test_put_forwards_account_actor(self, app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            brain_controller,
            "current_user",
            SimpleNamespace(current_tenant_id=TENANT, id="account-9"),
        )
        calls: list[dict[str, str]] = []

        class _Service:
            def set_posture(self, *, posture: str, reason: str, actor_kind: str, actor_id: str):
                calls.append(
                    {
                        "posture": posture,
                        "reason": reason,
                        "actor_kind": actor_kind,
                        "actor_id": actor_id,
                    }
                )
                return {"resolution": {"effective_mode": "observe"}}

        monkeypatch.setattr(brain_controller, "_gate_posture_service", lambda: _Service())

        with app.test_request_context(
            "/console/api/brain/gate-posture",
            json={"posture": "observe", "reason": "activate property"},
        ):
            payload = inspect.unwrap(brain_controller.ConsoleBrainGatePostureApi.put)(
                brain_controller.ConsoleBrainGatePostureApi()
            )

        assert payload == {"resolution": {"effective_mode": "observe"}}
        assert calls == [
            {
                "posture": "observe",
                "reason": "activate property",
                "actor_kind": "account",
                "actor_id": "account-9",
            }
        ]
