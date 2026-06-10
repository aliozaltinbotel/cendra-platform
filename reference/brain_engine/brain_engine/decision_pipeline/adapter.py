"""Decision Pipeline Adapter (M19).

Orchestration glue: runs the per-tool-call gates Brain Engine
already ships — ComplianceMonitor (M10), AbstentionGate (M1),
RiskGate (M9), CertificateVerifier (M3) — sequentially with
short-circuit semantics, and emits an Art.12 audit record (M5)
on the PROCEED path.

**Not a new moat.**  Every gate is patent-defensible on its own;
this adapter is the operational seam that turns the gates into
one pre-tool-call entry point so the runtime never accidentally
forgets one of them.

Short-circuit order:

  1. ComplianceMonitor → ``BLOCKED`` short-circuits.
  2. CertificateVerifier (if cert supplied) → ``BLOCKED`` on
     bad-signature / expired / wrong-scope / policy-ceiling
     exceeded.
  3. AbstentionGate → ``ABSTAIN`` short-circuits to ``DEFER``;
     ``INSUFFICIENT_DATA`` also DEFER (caller may retry once
     calibration data accumulates).
  4. RiskGate → ``ABSTAIN`` short-circuits to ``DEFER``;
     ``INSUFFICIENT_DATA`` also DEFER.
  5. All gates passed → emit Art.12 audit record → ``PROCEED``.

The COMPLIANCE ``NEEDS_REVIEW`` outcome (EU AI Act Art. 14 HITL)
maps to ``DEFER`` so the caller wires explicit human approval
before resubmitting.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from brain_engine.abstention.gate import AbstentionGate
from brain_engine.abstention.models import AbstentionVerdict
from brain_engine.certificates.verifier import (
    CertificateVerifier,
    VerifyOutcome,
)
from brain_engine.compliance.art12_decision import (
    Art12Decision,
    HandlerSolver,
)
from brain_engine.compliance.monitor import (
    ComplianceContext,
    ComplianceMonitor,
    VerdictKind,
)
from brain_engine.decision_pipeline.models import (
    GateName,
    GateOutcome,
    PipelineDecision,
    PipelineRequest,
    PipelineVerdict,
)
from brain_engine.risk.gate import RiskGate
from brain_engine.risk.models import RiskVerdict


__all__ = ["DecisionPipelineAdapter"]


logger = structlog.get_logger(__name__)


class DecisionPipelineAdapter:
    """One-shot façade for the pre-tool-call gate chain."""

    def __init__(
        self,
        *,
        compliance_monitor: ComplianceMonitor,
        abstention_gate: AbstentionGate,
        risk_gate: RiskGate,
        certificate_verifier: CertificateVerifier,
    ) -> None:
        self._compliance = compliance_monitor
        self._abstention = abstention_gate
        self._risk = risk_gate
        self._certificate = certificate_verifier
        self._log = logger.bind(component="decision_pipeline")

    def decide(
        self,
        request: PipelineRequest,
        *,
        at: datetime | None = None,
    ) -> PipelineDecision:
        """Run every gate; return the aggregate :class:`PipelineDecision`."""
        moment = self._now(at)
        trace: list[GateOutcome] = []
        compliance_row = self._compliance_step(
            request=request, at=moment,
        )
        trace.append(compliance_row)
        if compliance_row.verdict == VerdictKind.BLOCKED.value:
            return self._terminal(
                trace=trace,
                verdict=PipelineVerdict.BLOCKED,
                rationale=compliance_row.rationale,
                moment=moment,
            )
        cert_row = self._certificate_step(request=request, at=moment)
        if cert_row is not None:
            trace.append(cert_row)
            if cert_row.verdict != VerifyOutcome.OK.value:
                return self._terminal(
                    trace=trace,
                    verdict=PipelineVerdict.BLOCKED,
                    rationale=cert_row.rationale,
                    moment=moment,
                )
        abstention_row = self._abstention_step(request=request)
        trace.append(abstention_row)
        if abstention_row.verdict != AbstentionVerdict.PROCEED.value:
            return self._terminal(
                trace=trace,
                verdict=PipelineVerdict.DEFER,
                rationale=abstention_row.rationale,
                moment=moment,
            )
        risk_row = self._risk_step(request=request)
        trace.append(risk_row)
        if risk_row.verdict != RiskVerdict.PROCEED.value:
            return self._terminal(
                trace=trace,
                verdict=PipelineVerdict.DEFER,
                rationale=risk_row.rationale,
                moment=moment,
            )
        if compliance_row.verdict == VerdictKind.NEEDS_REVIEW.value:
            return self._terminal(
                trace=trace,
                verdict=PipelineVerdict.DEFER,
                rationale=compliance_row.rationale,
                moment=moment,
            )
        audit = self._emit_audit(request=request, moment=moment)
        return PipelineDecision(
            verdict=PipelineVerdict.PROCEED,
            rationale="all gates passed",
            gate_trace=tuple(trace),
            evaluated_at=moment,
            audit_record=audit,
        )

    # ── gate steps ────────────────────────────────────────── #

    def _compliance_step(
        self,
        *,
        request: PipelineRequest,
        at: datetime,
    ) -> GateOutcome:
        ctx = ComplianceContext(
            property_id=request.property_id,
            owner_id=request.owner_id,
            action_kind=request.action_kind,
            jurisdiction=request.jurisdiction,
            registration_id=request.registration_id,
            booking_dates=request.booking_dates,
            is_natural_person_decision=(
                request.is_natural_person_decision
            ),
            has_human_consent=request.has_human_consent,
            extra=request.compliance_extra,
        )
        verdict = self._compliance.evaluate(ctx, at=at)
        return GateOutcome(
            gate=GateName.COMPLIANCE,
            verdict=verdict.kind.value,
            rationale=verdict.rationale,
        )

    def _certificate_step(
        self,
        *,
        request: PipelineRequest,
        at: datetime,
    ) -> GateOutcome | None:
        if request.certificate is None:
            return None
        result = self._certificate.verify(
            cert=request.certificate,
            action_kind=request.action_kind,
            property_id=request.property_id,
            owner_id=request.owner_id,
            at=at,
        )
        return GateOutcome(
            gate=GateName.CERTIFICATE,
            verdict=result.outcome.value,
            rationale=result.rationale,
        )

    def _abstention_step(
        self,
        *,
        request: PipelineRequest,
    ) -> GateOutcome:
        decision = self._abstention.decide(
            tool_id=request.tool_id,
            model_confidence=request.model_confidence,
        )
        return GateOutcome(
            gate=GateName.ABSTENTION,
            verdict=decision.verdict.value,
            rationale=decision.rationale,
        )

    def _risk_step(
        self,
        *,
        request: PipelineRequest,
    ) -> GateOutcome:
        decision = self._risk.decide(request.risk_samples)
        return GateOutcome(
            gate=GateName.RISK,
            verdict=decision.verdict.value,
            rationale=decision.rationale,
        )

    # ── helpers ───────────────────────────────────────────── #

    def _emit_audit(
        self,
        *,
        request: PipelineRequest,
        moment: datetime,
    ) -> Art12Decision:
        try:
            solver = HandlerSolver(request.handler_solver)
        except ValueError as exc:
            raise ValueError(
                f"unknown handler_solver "
                f"{request.handler_solver!r}"
            ) from exc
        return Art12Decision(
            decision_id=request.decision_id,
            occurred_at=moment,
            property_id=request.property_id,
            owner_id=request.owner_id,
            action_kind=request.action_kind,
            handler_solver=solver,
            rationale=request.rationale,
            provenance_digest=request.provenance_digest,
            autonomy_tier=request.autonomy_tier,
            planner_style=request.planner_style,
            extra=request.extra,
        )

    @staticmethod
    def _terminal(
        *,
        trace: list[GateOutcome],
        verdict: PipelineVerdict,
        rationale: str,
        moment: datetime,
    ) -> PipelineDecision:
        return PipelineDecision(
            verdict=verdict,
            rationale=rationale,
            gate_trace=tuple(trace),
            evaluated_at=moment,
        )

    @staticmethod
    def _now(at: datetime | None) -> datetime:
        if at is None:
            return datetime.now(timezone.utc)
        if at.tzinfo is None:
            raise ValueError("`at` must be tz-aware when provided")
        return at
