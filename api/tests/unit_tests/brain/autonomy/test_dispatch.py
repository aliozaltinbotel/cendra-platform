"""Dispatch-time autonomy resolution and gateway posture behaviour."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.brain.autonomy import (
    AutonomyEngine,
    AutonomyState,
    DispatchSemantics,
    InMemoryAutonomyStore,
    InMemoryWorkflowKindRegistry,
    resolve_dispatch_autonomy,
)
from core.brain.gates import GateName, GateOutcome, PipelineDecision, PipelineVerdict
from core.brain.runtime_gateway import GATES_MODE_ENV, evaluate_dispatch_with_shadow, reset_gateway_state

TENANT = "11111111-1111-1111-1111-111111111111"
PROPERTY_ID = "app-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> Iterator[None]:
    monkeypatch.delenv(GATES_MODE_ENV, raising=False)
    reset_gateway_state()
    yield
    reset_gateway_state()


def _gateway_dispatch():
    return evaluate_dispatch_with_shadow(
        tenant_id=TENANT,
        app_id=PROPERTY_ID,
        tool_id="send_message",
        conversation_id="conv-1",
    )


def _proceed_decision() -> PipelineDecision:
    return PipelineDecision(
        verdict=PipelineVerdict.PROCEED,
        rationale="all gates passed",
        gate_trace=(
            GateOutcome(
                gate=GateName.ABSTENTION,
                verdict="proceed",
                rationale="calibration window healthy",
            ),
        ),
        evaluated_at=datetime.now(UTC),
    )


def _patch_proceeding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.brain.runtime_gateway._adapter_for",
        lambda tenant_id: SimpleNamespace(decide=lambda request: _proceed_decision()),
    )


@pytest.mark.parametrize(
    ("state", "expected_semantics"),
    [
        (AutonomyState.OBSERVE, DispatchSemantics.DRAFT_ONLY),
        (AutonomyState.SEMI_AUTO, DispatchSemantics.HOLD),
        (AutonomyState.AUTOPILOT, DispatchSemantics.PROCEED),
    ],
)
def test_gateway_records_per_rung_dispatch_semantics(monkeypatch, state, expected_semantics) -> None:
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    _patch_proceeding_gateway(monkeypatch)

    engine = AutonomyEngine(store=InMemoryAutonomyStore())
    if state is not AutonomyState.OBSERVE:
        engine.force_state(
            property_id=PROPERTY_ID,
            workflow="guest_reply",
            state=state,
            actor="pm:7",
            reason="test fixture",
        )
    registry = InMemoryWorkflowKindRegistry({"guest_reply": ("send_message",)})
    monkeypatch.setattr(
        "core.brain.runtime_gateway._autonomy_runtime_for",
        lambda tenant_id: SimpleNamespace(engine=engine, registry=registry),
    )

    dispatch = _gateway_dispatch()

    assert dispatch.enforcement is None
    assert dispatch.autonomy is None
    assert dispatch.shadow is not None
    assert dispatch.shadow["autonomy"] == {
        "workflow": "guest_reply",
        "state": state.value,
        "semantics": expected_semantics.value,
        "rationale": "dispatch identity 'send_message' resolved to workflow 'guest_reply'",
    }


def test_gateway_enforce_observe_rung_blocks_dispatch_as_draft_only(monkeypatch) -> None:
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    _patch_proceeding_gateway(monkeypatch)
    monkeypatch.setattr(
        "core.brain.runtime_gateway._autonomy_runtime_for",
        lambda tenant_id: SimpleNamespace(
            engine=AutonomyEngine(store=InMemoryAutonomyStore()),
            registry=InMemoryWorkflowKindRegistry({"guest_reply": ("send_message",)}),
        ),
    )

    dispatch = _gateway_dispatch()

    assert dispatch.enforcement is not None
    assert dispatch.enforcement.verdict is PipelineVerdict.BLOCKED
    assert dispatch.enforcement.gate_trace[-1].gate.value == "autonomy"
    assert "draft-only" in dispatch.enforcement.rationale
    assert dispatch.autonomy is not None
    assert dispatch.autonomy.semantics is DispatchSemantics.DRAFT_ONLY
    assert dispatch.shadow is not None
    assert dispatch.shadow["pipeline_verdict"] == "blocked"
    assert dispatch.shadow["refusing_gate"] == "autonomy"


def test_gateway_enforce_semi_auto_rung_defers_into_hold_path(monkeypatch) -> None:
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    _patch_proceeding_gateway(monkeypatch)
    engine = AutonomyEngine(store=InMemoryAutonomyStore())
    engine.force_state(
        property_id=PROPERTY_ID,
        workflow="guest_reply",
        state=AutonomyState.SEMI_AUTO,
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

    dispatch = _gateway_dispatch()

    assert dispatch.enforcement is not None
    assert dispatch.enforcement.verdict is PipelineVerdict.DEFER
    assert dispatch.enforcement.gate_trace[-1].gate.value == "autonomy"
    assert "hold/approval" in dispatch.enforcement.rationale
    assert dispatch.autonomy is not None
    assert dispatch.autonomy.semantics is DispatchSemantics.HOLD
    assert dispatch.shadow is not None
    assert dispatch.shadow["pipeline_verdict"] == "defer"
    assert dispatch.shadow["refusing_gate"] == "autonomy"


def test_gateway_enforce_autopilot_rung_proceeds(monkeypatch) -> None:
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    _patch_proceeding_gateway(monkeypatch)
    engine = AutonomyEngine(store=InMemoryAutonomyStore())
    engine.force_state(
        property_id=PROPERTY_ID,
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

    dispatch = _gateway_dispatch()

    assert dispatch.enforcement is not None
    assert dispatch.enforcement.verdict is PipelineVerdict.PROCEED
    assert dispatch.autonomy is not None
    assert dispatch.autonomy.semantics is DispatchSemantics.PROCEED
    assert dispatch.shadow is not None
    assert dispatch.shadow["pipeline_verdict"] == "proceed"
    assert dispatch.shadow["refusing_gate"] is None


def test_unresolved_dispatch_identity_fails_safe_without_state_lookup() -> None:
    class ExplodingEngine:
        def state_for(self, *, property_id: str, workflow: str) -> AutonomyState:
            raise AssertionError("state_for should not run for unresolved identities")

    registry = InMemoryWorkflowKindRegistry({"guest_reply": ("agent:reply",)})

    dispatch = resolve_dispatch_autonomy(
        engine=ExplodingEngine(),
        registry=registry,
        property_id=PROPERTY_ID,
        dispatch_identity="send_message",
    )

    assert dispatch.workflow is None
    assert dispatch.state is AutonomyState.OBSERVE
    assert dispatch.semantics is DispatchSemantics.DRAFT_ONLY
    assert dispatch.rationale == "dispatch identity 'send_message' unresolved in workflow registry"


def test_gateway_off_mode_skips_autonomy_resolution(monkeypatch) -> None:
    monkeypatch.setenv(GATES_MODE_ENV, "off")
    monkeypatch.setattr(
        "core.brain.runtime_gateway._dispatch_autonomy",
        lambda **_: (_ for _ in ()).throw(AssertionError("autonomy resolution should be skipped")),
    )

    dispatch = _gateway_dispatch()

    assert dispatch.enforcement is None
    assert dispatch.shadow is None
    assert dispatch.autonomy is None
