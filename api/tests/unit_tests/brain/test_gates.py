"""Gate-chain composition behaviour (gates.py).

Adapted from the reference's decision_pipeline tests: the compliance
monitor and Art.12 audit record are Batch 5, so compliance runs through
the optional ComplianceGate seam (stubbed here) and the audit factory
seam is exercised with a sentinel.  Chain order and short-circuit
semantics are reference parity.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pytest

from core.brain.abstention.calibrator import ConformalCalibrator
from core.brain.abstention.gate import AbstentionGate
from core.brain.abstention.models import CalibrationSample
from core.brain.abstention.protocols import InMemoryCalibrationStore
from core.brain.certificates.issuer import MIN_KEY_BYTES, CertificateIssuer
from core.brain.certificates.policy import TierPolicy
from core.brain.certificates.tier import AutonomyTier
from core.brain.certificates.verifier import CertificateVerifier
from core.brain.gates import (
    ComplianceVerdict,
    DecisionPipelineAdapter,
    GateName,
    PipelineRequest,
    PipelineVerdict,
)
from core.brain.risk.gate import RiskGate
from core.brain.risk.models import OutcomeSample

KEY = secrets.token_bytes(MIN_KEY_BYTES)
POLICY = TierPolicy({"send_message": AutonomyTier.COLLABORATOR})


class _StubCompliance:
    def __init__(self, kind: str = "ok", rationale: str = "compliant"):
        self.kind = kind
        self.rationale = rationale
        self.calls = 0

    def evaluate(self, request, *, at):
        self.calls += 1
        return ComplianceVerdict(kind=self.kind, rationale=self.rationale)


def _calibrated_gate(*, successes: int = 40, failures: int = 0) -> AbstentionGate:
    store = InMemoryCalibrationStore()
    for i in range(successes):
        store.record(CalibrationSample.now(tool_id="send_message", predicted_confidence=0.9, actual_success=True))
    for i in range(failures):
        store.record(CalibrationSample.now(tool_id="send_message", predicted_confidence=0.2, actual_success=False))
    return AbstentionGate(calibrator=ConformalCalibrator(store=store))


def _adapter(**overrides) -> DecisionPipelineAdapter:
    base = {
        "abstention_gate": _calibrated_gate(),
        "risk_gate": RiskGate(cvar_threshold=100.0, min_samples=4),
        "certificate_verifier": CertificateVerifier(signing_key=KEY, policy=POLICY),
    }
    base.update(overrides)
    return DecisionPipelineAdapter(**base)


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
    }
    base.update(overrides)
    return PipelineRequest(**base)


def test_proceed_path_runs_all_gates_in_order():
    compliance = _StubCompliance()
    decision = _adapter(compliance_gate=compliance).decide(_request())
    assert decision.verdict is PipelineVerdict.PROCEED
    assert [row.gate for row in decision.gate_trace] == [
        GateName.COMPLIANCE,
        GateName.ABSTENTION,
        GateName.RISK,
    ]
    assert compliance.calls == 1
    assert decision.audit_record is None  # no audit_factory configured on this adapter


def test_compliance_blocked_short_circuits_everything():
    decision = _adapter(compliance_gate=_StubCompliance("blocked", "Reg 2024/1028 violation")).decide(_request())
    assert decision.verdict is PipelineVerdict.BLOCKED
    assert decision.rationale == "Reg 2024/1028 violation"
    assert [row.gate for row in decision.gate_trace] == [GateName.COMPLIANCE]


def test_compliance_needs_review_defers_after_all_gates():
    decision = _adapter(compliance_gate=_StubCompliance("needs_review", "Art. 14 HITL")).decide(_request())
    assert decision.verdict is PipelineVerdict.DEFER
    assert decision.rationale == "Art. 14 HITL"
    # every gate still ran (reference parity: review defers last)
    assert [row.gate for row in decision.gate_trace] == [
        GateName.COMPLIANCE,
        GateName.ABSTENTION,
        GateName.RISK,
    ]


def test_no_compliance_gate_is_skipped_not_failed():
    decision = _adapter().decide(_request())
    assert decision.verdict is PipelineVerdict.PROCEED
    assert GateName.COMPLIANCE not in [row.gate for row in decision.gate_trace]


def test_bad_certificate_blocks():
    issuer = CertificateIssuer(signing_key=secrets.token_bytes(MIN_KEY_BYTES))  # different key
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p-1",
        owner_id="o-1",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    decision = _adapter().decide(_request(certificate=cert))
    assert decision.verdict is PipelineVerdict.BLOCKED
    assert decision.gate_trace[-1].gate is GateName.CERTIFICATE


def test_valid_certificate_passes_through():
    issuer = CertificateIssuer(signing_key=KEY)
    cert = issuer.issue(
        action_kind="send_message",
        property_id="p-1",
        owner_id="o-1",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    decision = _adapter().decide(_request(certificate=cert))
    assert decision.verdict is PipelineVerdict.PROCEED
    assert [row.gate for row in decision.gate_trace] == [
        GateName.CERTIFICATE,
        GateName.ABSTENTION,
        GateName.RISK,
    ]


def test_abstention_defers_on_thin_calibration():
    decision = _adapter(abstention_gate=_calibrated_gate(successes=3)).decide(_request())
    assert decision.verdict is PipelineVerdict.DEFER
    assert decision.gate_trace[-1].gate is GateName.ABSTENTION
    assert decision.gate_trace[-1].verdict == "insufficient_data"


def test_risk_defers_on_cvar_breach():
    decision = _adapter(risk_gate=RiskGate(cvar_threshold=0.5, min_samples=4)).decide(_request())
    assert decision.verdict is PipelineVerdict.DEFER
    assert decision.gate_trace[-1].gate is GateName.RISK
    assert "cvar" in decision.gate_trace[-1].rationale


def test_risk_defers_on_empty_samples():
    decision = _adapter().decide(_request(risk_samples=()))
    assert decision.verdict is PipelineVerdict.DEFER
    assert decision.gate_trace[-1].verdict == "insufficient_data"


def test_audit_factory_seam_fires_on_proceed_only():
    sentinel = object()
    calls: list[tuple] = []

    def factory(request, moment, gate_trace):
        calls.append((request.decision_id, moment, gate_trace))
        return sentinel

    adapter = _adapter(audit_factory=factory)
    proceed = adapter.decide(_request())
    assert proceed.audit_record is sentinel
    assert len(calls) == 1
    defer = adapter.decide(_request(risk_samples=()))
    assert defer.audit_record is None
    assert len(calls) == 1


def test_audit_factory_receives_full_gate_trace():
    traces: list[tuple] = []

    def factory(request, moment, gate_trace):
        traces.append(gate_trace)

    decision = _adapter(audit_factory=factory).decide(_request())
    assert decision.verdict is PipelineVerdict.PROCEED
    # the factory sees exactly the trace the decision carries, so the
    # emitted receipt's signed bytes record what was decided and why
    assert traces == [decision.gate_trace]
    assert {row.gate for row in traces[0]} >= {GateName.ABSTENTION, GateName.RISK}


def test_request_validation():
    with pytest.raises(ValueError, match="tool_id required"):
        _request(tool_id="")
    with pytest.raises(ValueError, match="model_confidence"):
        _request(model_confidence=1.5)


def test_naive_at_rejected():
    with pytest.raises(ValueError, match="tz-aware"):
        _adapter().decide(_request(), at=datetime(2026, 6, 10))


def test_aware_at_is_used():
    moment = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    decision = _adapter().decide(_request(), at=moment)
    assert decision.evaluated_at == moment
