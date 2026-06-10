"""Behaviour of the T1 runtime gateway (mode gating + calibration loop)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.brain.gates import PipelineVerdict
from core.brain.runtime_gateway import (
    GATES_MODE_ENV,
    GATES_TENANTS_ENV,
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
