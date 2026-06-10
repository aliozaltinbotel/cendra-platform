"""End-to-end behaviour of :class:`DecisionPipelineAdapter`."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from brain_engine.abstention.calibrator import (
    ConformalCalibrator,
)
from brain_engine.abstention.gate import AbstentionGate
from brain_engine.abstention.models import CalibrationSample
from brain_engine.abstention.protocols import (
    InMemoryCalibrationStore,
)
from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.certificates.issuer import CertificateIssuer
from brain_engine.certificates.tier import AutonomyTier
from brain_engine.certificates.verifier import (
    CertificateVerifier,
)
from brain_engine.compliance.checks import (
    DEFAULT_BUILTIN_CHECKS,
)
from brain_engine.compliance.monitor import ComplianceMonitor
from brain_engine.decision_pipeline.adapter import (
    DecisionPipelineAdapter,
)
from brain_engine.decision_pipeline.models import (
    GateName,
    PipelineRequest,
    PipelineVerdict,
)
from brain_engine.risk.gate import RiskGate
from brain_engine.risk.models import OutcomeSample


@pytest.fixture
def signing_key() -> bytes:
    return secrets.token_bytes(32)


@pytest.fixture
def adapter(signing_key: bytes) -> DecisionPipelineAdapter:
    monitor = ComplianceMonitor(checks=DEFAULT_BUILTIN_CHECKS)
    calib_store = InMemoryCalibrationStore()
    calibrator = ConformalCalibrator(store=calib_store)
    for _ in range(45):
        calibrator.record(
            CalibrationSample.now(
                tool_id="trusted_tool",
                predicted_confidence=0.92,
                actual_success=True,
            )
        )
    return DecisionPipelineAdapter(
        compliance_monitor=monitor,
        abstention_gate=AbstentionGate(calibrator=calibrator),
        risk_gate=RiskGate(
            cvar_threshold=20.0,
            alpha=0.05,
            min_samples=32,
        ),
        certificate_verifier=CertificateVerifier(
            signing_key=signing_key,
        ),
    )


@pytest.fixture
def cert(signing_key: bytes):
    issuer = CertificateIssuer(signing_key=signing_key)
    return issuer.issue(
        action_kind=CardActionKind.SEND_MESSAGE,
        property_id="p1",
        owner_id="o1",
        granted_tier=AutonomyTier.COLLABORATOR,
    )


def _samples() -> tuple[OutcomeSample, ...]:
    return tuple(OutcomeSample(loss=1.0) for _ in range(40))


def _now() -> datetime:
    return datetime(2026, 5, 11, tzinfo=timezone.utc)


def test_clean_path_proceeds_and_emits_audit(
    adapter: DecisionPipelineAdapter,
    cert,
) -> None:
    """Every gate passes → PROCEED + Art.12 record emitted."""
    request = PipelineRequest(
        decision_id="d1",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        rationale="quiet hours warning",
        provenance_digest="a" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="llm",
        risk_samples=_samples(),
        certificate=cert,
        autonomy_tier="collaborator",
        planner_style="cooperative",
    )
    decision = adapter.decide(request, at=_now())
    assert decision.verdict is PipelineVerdict.PROCEED
    assert decision.audit_record is not None
    assert decision.audit_record.decision_id == "d1"
    gates = [r.gate for r in decision.gate_trace]
    assert gates == [
        GateName.COMPLIANCE,
        GateName.CERTIFICATE,
        GateName.ABSTENTION,
        GateName.RISK,
    ]


def test_compliance_block_short_circuits(
    adapter: DecisionPipelineAdapter,
    cert,
) -> None:
    """Reg 2024/1028 missing reg_id → BLOCKED at compliance."""
    request = PipelineRequest(
        decision_id="d2",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.CONFIRM_BOOKING,
        rationale="approve",
        provenance_digest="b" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="llm",
        risk_samples=_samples(),
        certificate=cert,
    )
    decision = adapter.decide(request, at=_now())
    assert decision.verdict is PipelineVerdict.BLOCKED
    # Only the compliance row should be in the trace
    assert len(decision.gate_trace) == 1
    assert decision.gate_trace[0].gate is GateName.COMPLIANCE
    assert decision.audit_record is None


def test_certificate_failure_short_circuits(
    adapter: DecisionPipelineAdapter,
) -> None:
    """Bad-key cert → BLOCKED at the certificate gate."""
    forged_issuer = CertificateIssuer(
        signing_key=secrets.token_bytes(32),
    )
    bad_cert = forged_issuer.issue(
        action_kind=CardActionKind.SEND_MESSAGE,
        property_id="p1",
        owner_id="o1",
        granted_tier=AutonomyTier.COLLABORATOR,
    )
    request = PipelineRequest(
        decision_id="d3",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        rationale="ok",
        provenance_digest="c" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="llm",
        risk_samples=_samples(),
        certificate=bad_cert,
    )
    decision = adapter.decide(request, at=_now())
    assert decision.verdict is PipelineVerdict.BLOCKED
    cert_row = [
        r for r in decision.gate_trace
        if r.gate is GateName.CERTIFICATE
    ][0]
    assert cert_row.verdict == "bad_signature"


def test_abstention_insufficient_defers(
    adapter: DecisionPipelineAdapter,
    cert,
) -> None:
    """Untrained tool → DEFER on insufficient_data."""
    request = PipelineRequest(
        decision_id="d4",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        rationale="ok",
        provenance_digest="d" * 64,
        tool_id="untested",  # no calibration history
        model_confidence=0.95,
        handler_solver="llm",
        risk_samples=_samples(),
        certificate=cert,
    )
    decision = adapter.decide(request, at=_now())
    assert decision.verdict is PipelineVerdict.DEFER
    assert decision.audit_record is None


def test_risk_high_cvar_defers(
    adapter: DecisionPipelineAdapter,
    cert,
) -> None:
    """Distribution with heavy tail → DEFER on risk."""
    high_tail = tuple(
        [OutcomeSample(loss=1.0) for _ in range(35)]
        + [OutcomeSample(loss=500.0) for _ in range(5)]
    )
    request = PipelineRequest(
        decision_id="d5",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        rationale="ok",
        provenance_digest="e" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="llm",
        risk_samples=high_tail,
        certificate=cert,
    )
    decision = adapter.decide(request, at=_now())
    assert decision.verdict is PipelineVerdict.DEFER
    risk_row = [
        r for r in decision.gate_trace
        if r.gate is GateName.RISK
    ][0]
    assert risk_row.verdict == "abstain"


def test_compliance_review_defers(
    adapter: DecisionPipelineAdapter,
) -> None:
    """ISSUE_REFUND without consent → COMPLIANCE NEEDS_REVIEW → DEFER."""
    request = PipelineRequest(
        decision_id="d6",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.ISSUE_REFUND,
        rationale="refund",
        provenance_digest="f" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="hitl",
        risk_samples=_samples(),
    )
    decision = adapter.decide(request, at=_now())
    assert decision.verdict is PipelineVerdict.DEFER
    compliance_row = decision.gate_trace[0]
    assert compliance_row.verdict == "needs_review"


def test_no_certificate_skips_certificate_gate(
    adapter: DecisionPipelineAdapter,
) -> None:
    """When certificate is None, the cert gate is skipped."""
    request = PipelineRequest(
        decision_id="d7",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        rationale="ok",
        provenance_digest="g" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="llm",
        risk_samples=_samples(),
    )
    decision = adapter.decide(request, at=_now())
    gates = [r.gate for r in decision.gate_trace]
    assert GateName.CERTIFICATE not in gates


def test_naive_at_rejected(
    adapter: DecisionPipelineAdapter,
) -> None:
    """Caller-supplied naive timestamp is rejected."""
    request = PipelineRequest(
        decision_id="d8",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        rationale="ok",
        provenance_digest="h" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="llm",
        risk_samples=_samples(),
    )
    with pytest.raises(ValueError, match="tz-aware"):
        adapter.decide(request, at=datetime(2026, 5, 11))


def test_unknown_handler_solver_raises_on_proceed(
    adapter: DecisionPipelineAdapter,
    cert,
) -> None:
    """A garbage handler_solver value raises during audit emit."""
    request = PipelineRequest(
        decision_id="d9",
        property_id="p1",
        owner_id="o1",
        action_kind=CardActionKind.SEND_MESSAGE,
        rationale="ok",
        provenance_digest="i" * 64,
        tool_id="trusted_tool",
        model_confidence=0.95,
        handler_solver="not_a_real_solver",
        risk_samples=_samples(),
        certificate=cert,
    )
    with pytest.raises(ValueError, match="handler_solver"):
        adapter.decide(request, at=_now())
