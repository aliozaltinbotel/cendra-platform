"""Gap-emission hook in the gate chain (CEN-15 Part B, CEN-28).

The hook rides ``DecisionPipelineAdapter._abstention_step``: no new
gate slot, chain order unchanged, and a sink failure never alters the
verdict.  Fixtures mirror ``test_gates.py``.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from core.brain.abstention.calibrator import ConformalCalibrator
from core.brain.abstention.gap_registry import GapStatus, InMemoryGapStore
from core.brain.abstention.gate import AbstentionGate
from core.brain.abstention.models import CalibrationSample
from core.brain.abstention.protocols import InMemoryCalibrationStore
from core.brain.certificates.issuer import MIN_KEY_BYTES
from core.brain.certificates.policy import TierPolicy
from core.brain.certificates.tier import AutonomyTier
from core.brain.certificates.verifier import CertificateVerifier
from core.brain.gates import DecisionPipelineAdapter, PipelineRequest, PipelineVerdict
from core.brain.risk.gate import RiskGate
from core.brain.risk.models import OutcomeSample

KEY = secrets.token_bytes(MIN_KEY_BYTES)
POLICY = TierPolicy({"send_message": AutonomyTier.COLLABORATOR})

EVENT_AT = datetime(2026, 6, 10, 22, 3, 11, tzinfo=UTC)
DISPATCH_AT = datetime(2026, 6, 10, 22, 3, 12, tzinfo=UTC)


def _gate(*, successes: int, failures: int) -> AbstentionGate:
    store = InMemoryCalibrationStore()
    for _ in range(successes):
        store.record(CalibrationSample.now(tool_id="send_message", predicted_confidence=0.9, actual_success=True))
    for _ in range(failures):
        store.record(CalibrationSample.now(tool_id="send_message", predicted_confidence=0.2, actual_success=False))
    return AbstentionGate(calibrator=ConformalCalibrator(store=store))


def _adapter(abstention_gate: AbstentionGate, sink) -> DecisionPipelineAdapter:
    return DecisionPipelineAdapter(
        abstention_gate=abstention_gate,
        risk_gate=RiskGate(cvar_threshold=100.0, min_samples=4),
        certificate_verifier=CertificateVerifier(signing_key=KEY, policy=POLICY),
        gap_sink=sink,
    )


def _request(**overrides) -> PipelineRequest:
    base = {
        "decision_id": "d-1",
        "property_id": "p-1",
        "owner_id": "o-1",
        "action_kind": "send_message",
        "rationale": "reply to guest",
        "provenance_digest": "ab" * 32,
        "tool_id": "send_message",
        "model_confidence": 0.9,
        "handler_solver": "llm",
        "risk_samples": tuple(OutcomeSample(loss=1.0) for _ in range(8)),
        "inbound_event_at": EVENT_AT,
    }
    base.update(overrides)
    return PipelineRequest(**base)


def test_abstain_emits_gap_with_decision_time_provenance():
    store = InMemoryGapStore()
    adapter = _adapter(_gate(successes=10, failures=30), store.record)
    decision = adapter.decide(
        _request(extra={"query": "quiet hours?", "run_id": "run-9", "missing_predicate": "quiet_hours"}),
        at=DISPATCH_AT,
    )
    assert decision.verdict is PipelineVerdict.DEFER
    (gap,) = store.list_for("p-1")
    assert gap.subject_ref == "p-1"
    assert gap.run_id == "run-9"
    assert gap.query == "quiet hours?"
    assert gap.missing_predicate == "quiet_hours"
    assert gap.as_of == EVENT_AT  # §E1: inbound-event timestamp, not wall-clock
    assert gap.dispatched_at == DISPATCH_AT
    assert gap.kg_snapshot_ref == f"brain:kg:p-1@{EVENT_AT.isoformat()}"
    assert gap.status is GapStatus.OPEN


def test_abstain_without_context_falls_back_to_request_fields():
    store = InMemoryGapStore()
    adapter = _adapter(_gate(successes=10, failures=30), store.record)
    adapter.decide(_request(inbound_event_at=None), at=DISPATCH_AT)
    (gap,) = store.list_for("p-1")
    assert gap.run_id == "d-1"  # decision_id fallback
    assert gap.query == "reply to guest"  # rationale fallback
    assert gap.as_of == DISPATCH_AT  # evaluation wall-clock fallback
    assert gap.missing_predicate  # gate rationale fallback, non-empty


def test_proceed_emits_nothing():
    store = InMemoryGapStore()
    adapter = _adapter(_gate(successes=40, failures=0), store.record)
    decision = adapter.decide(_request(), at=DISPATCH_AT)
    assert decision.verdict is PipelineVerdict.PROCEED
    assert store.list_for("p-1") == ()


def test_insufficient_data_emits_nothing():
    store = InMemoryGapStore()
    adapter = _adapter(_gate(successes=3, failures=0), store.record)
    decision = adapter.decide(_request(), at=DISPATCH_AT)
    assert decision.verdict is PipelineVerdict.DEFER
    assert store.list_for("p-1") == ()


def test_sink_failure_never_changes_the_verdict():
    def exploding_sink(gap):
        raise RuntimeError("sink down")

    adapter = _adapter(_gate(successes=10, failures=30), exploding_sink)
    decision = adapter.decide(_request(), at=DISPATCH_AT)
    assert decision.verdict is PipelineVerdict.DEFER  # abstention outcome intact


def test_no_sink_means_no_emission_path():
    adapter = _adapter(_gate(successes=10, failures=30), None)
    decision = adapter.decide(_request(), at=DISPATCH_AT)
    assert decision.verdict is PipelineVerdict.DEFER
