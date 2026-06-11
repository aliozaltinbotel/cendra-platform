"""T7 capture threads the observe-posture shadow verdict (CEN-33).

The DecisionCase written under ``BRAIN_GATES_MODE=observe`` must carry
the gate-chain shadow verdict in ``orchestrator_verdict[SHADOW_KEY]``,
while a dispatch with no shadow stays backward compatible (no key).
"""

from __future__ import annotations

import types
from collections.abc import Iterator

import pytest

from core.brain.patterns.shadow_verdict import SHADOW_KEY
from core.callback_handler import cendra_decision_capture as cap

TENANT = "11111111-1111-1111-1111-111111111111"


class _CapturingStore:
    """Stand-in for SQLAlchemyDecisionCaseStore that records the case."""

    last_case = None

    def __init__(self, *, session_maker, tenant_id) -> None:
        self._tenant_id = tenant_id

    def store(self, case) -> str:
        _CapturingStore.last_case = case
        return case.case_id


@pytest.fixture(autouse=True)
def _observe_capture(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("BRAIN_GATES_MODE", "observe")
    # isolate from the DB: fake engine (sessionmaker just stores the bind),
    # capturing store, and a no-op calibration recorder.
    monkeypatch.setattr("extensions.ext_database.db", types.SimpleNamespace(engine=object()), raising=False)
    monkeypatch.setattr(
        "core.brain.patterns.case_store.SQLAlchemyDecisionCaseStore",
        _CapturingStore,
    )
    monkeypatch.setattr("core.brain.runtime_gateway.record_tool_outcome", lambda **_: None)
    _CapturingStore.last_case = None
    yield


def _shadow() -> dict:
    return {
        "schema": 1,
        "verdict": "would_abstain",
        "pipeline_verdict": "defer",
        "refusing_gate": "abstention",
        "confidence": 1.0,
        "rationale": "wilson_lb=0.45 < threshold=0.60",
        "gate_trace": [{"gate": "abstention", "verdict": "abstain", "rationale": "thin"}],
        "evaluated_at": "2026-06-11T00:00:00+00:00",
    }


def test_capture_records_shadow_in_orchestrator_verdict():
    shadow = _shadow()
    cap.capture_tool_outcome(
        tenant_id=TENANT,
        app_id="app-1",
        tool_id="send_message",
        conversation_id="conv-1",
        dispatch_key="0:abc",
        success=True,
        shadow_verdict=shadow,
    )
    case = _CapturingStore.last_case
    assert case is not None, "capture swallowed an error before storing"
    assert case.orchestrator_verdict["source"] == "t7_capture"
    assert case.orchestrator_verdict["tool_id"] == "send_message"
    assert case.orchestrator_verdict[SHADOW_KEY] == shadow


def test_capture_without_shadow_stays_backward_compatible():
    cap.capture_tool_outcome(
        tenant_id=TENANT,
        app_id="app-1",
        tool_id="send_message",
        conversation_id="conv-1",
        dispatch_key="0:abc",
        success=True,
    )
    case = _CapturingStore.last_case
    assert case is not None
    assert case.orchestrator_verdict == {"source": "t7_capture", "tool_id": "send_message"}
    assert SHADOW_KEY not in case.orchestrator_verdict


def test_instrument_stream_threads_shadow_on_success():
    shadow = _shadow()
    stream = (m for m in ("a", "b"))
    out = list(
        cap.instrument_tool_messages(
            stream,
            tenant_id=TENANT,
            app_id="app-1",
            tool_id="send_message",
            conversation_id="conv-1",
            dispatch_key="0:abc",
            shadow_verdict=shadow,
        )
    )
    assert out == ["a", "b"]
    assert _CapturingStore.last_case.orchestrator_verdict[SHADOW_KEY] == shadow
