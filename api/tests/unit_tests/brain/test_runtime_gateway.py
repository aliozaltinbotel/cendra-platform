"""Behaviour of the T1 runtime gateway (mode gating + calibration loop)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from core.brain.autonomy import (
    AutonomyEngine,
    AutonomyState,
    InMemoryAutonomyStore,
    InMemoryWorkflowKindRegistry,
)
from core.brain.gates import PipelineVerdict
from core.brain.patterns.shadow_verdict import WOULD_ABSTAIN
from core.brain.runtime_gateway import (
    GATES_MODE_ENV,
    GATES_TENANTS_ENV,
    evaluate_dispatch_with_shadow,
    evaluate_tool_dispatch,
    record_tool_outcome,
    reset_gateway_state,
)

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> Iterator[None]:
    monkeypatch.delenv(GATES_MODE_ENV, raising=False)
    monkeypatch.delenv(GATES_TENANTS_ENV, raising=False)
    reset_gateway_state()
    yield
    reset_gateway_state()


def _dispatch(**overrides):
    base = {
        "tenant_id": TENANT,
        "app_id": "app-1",
        "tool_id": "send_message",
        "conversation_id": "conv-1",
    }
    base.update(overrides)
    return evaluate_tool_dispatch(**base)


def test_default_mode_is_off_and_returns_none():
    assert _dispatch() is None


def test_unknown_mode_treated_as_off(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "yolo")
    assert _dispatch() is None


def test_observe_mode_logs_but_never_blocks(monkeypatch, caplog):
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    with caplog.at_level("INFO"):
        result = _dispatch()
    assert result is None
    assert any("brain.gates" in r.message for r in caplog.records)
    assert any("verdict=defer" in r.message for r in caplog.records)  # thin calibration → defer logged


def test_tenant_allowlist_scopes_gating(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    monkeypatch.setenv(GATES_TENANTS_ENV, "some-other-tenant")
    assert _dispatch() is None  # outside allowlist → untouched


def test_enforce_defers_on_thin_calibration(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    decision = _dispatch()
    assert decision is not None
    assert decision.verdict is PipelineVerdict.DEFER
    assert decision.gate_trace[-1].gate.value == "abstention"


def test_outcome_recording_builds_calibration_until_proceed(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    monkeypatch.setenv(GATES_TENANTS_ENV, TENANT)
    engine = AutonomyEngine(store=InMemoryAutonomyStore())
    engine.force_state(
        property_id="app-1",
        workflow="guest_reply",
        state=AutonomyState.AUTOPILOT,
        actor="pm:7",
        reason="test fixture",
    )
    monkeypatch.setattr(
        "core.brain.runtime_gateway._autonomy_runtime_for",
        lambda tenant_id: SimpleNamespace(
            engine=engine,
            registry=InMemoryWorkflowKindRegistry({"guest_reply": ("send_message",)}),
        ),
    )
    for _ in range(40):
        record_tool_outcome(
            tenant_id=TENANT,
            tool_id="send_message",
            predicted_confidence=0.9,
            success=True,
        )
    decision = _dispatch(model_confidence=0.9)
    # abstention passes; risk lacks samples at dispatch → Batch 4 pass-through
    assert decision is None


def test_enforce_isolates_tenants(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    for _ in range(40):
        record_tool_outcome(
            tenant_id=TENANT,
            tool_id="send_message",
            predicted_confidence=0.9,
            success=True,
        )
    other = evaluate_tool_dispatch(
        tenant_id="22222222-2222-2222-2222-222222222222",
        app_id="app-1",
        tool_id="send_message",
    )
    assert other is not None
    assert other.verdict is PipelineVerdict.DEFER


def test_missing_tenant_is_passthrough(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    assert _dispatch(tenant_id="") is None


# ── shadow verdict (CEN-33): observe records what enforce would do ── #


def test_off_mode_yields_no_shadow(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "off")
    dispatch = evaluate_dispatch_with_shadow(tenant_id=TENANT, app_id="app-1", tool_id="send_message")
    assert dispatch.enforcement is None
    assert dispatch.shadow is None  # readers treat as unknown


def test_observe_records_shadow_without_binding(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    dispatch = evaluate_dispatch_with_shadow(tenant_id=TENANT, app_id="app-1", tool_id="send_message")
    # observe never refuses the dispatch...
    assert dispatch.enforcement is None
    # ...but the gate chain's would-be verdict is captured for the ledger
    assert dispatch.shadow is not None
    assert dispatch.shadow["verdict"] == WOULD_ABSTAIN  # thin calibration → defer
    assert dispatch.shadow["pipeline_verdict"] == "defer"
    assert dispatch.shadow["refusing_gate"] == "abstention"
    assert dispatch.shadow["confidence"] == 1.0


def test_enforce_shadow_matches_bound_decision(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    monkeypatch.setenv(GATES_TENANTS_ENV, TENANT)
    dispatch = evaluate_dispatch_with_shadow(tenant_id=TENANT, app_id="app-1", tool_id="send_message")
    # single evaluation: the bound enforcement decision and the shadow agree
    assert dispatch.enforcement is not None
    assert dispatch.enforcement.verdict is PipelineVerdict.DEFER
    assert dispatch.shadow["verdict"] == WOULD_ABSTAIN
    assert dispatch.shadow["pipeline_verdict"] == "defer"
