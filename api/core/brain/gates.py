"""Gate-chain composition for the Cendra brain kernel.

Port of the reference's ``decision_pipeline/{models,adapter}.py``
(M19) @a761e29 — the one pre-tool-call entry point that runs every
gate sequentially with short-circuit semantics so the runtime never
accidentally forgets one of them.  **Not a new moat**: every gate is
patent-defensible on its own; this is the operational seam the
touchpoint adapters (T1–T3, see FORK_LEDGER.md) wrap around tool /
agent dispatch.  Nothing here may import from ``core.workflow``,
``core.app`` or ``core.agent``.

Short-circuit order (reference parity):

  1. Compliance → ``BLOCKED`` short-circuits.
  2. Certificate (if a cert is supplied) → ``BLOCKED`` on bad
     signature / expiry / scope / policy-ceiling.
  3. Abstention → ``ABSTAIN`` / ``INSUFFICIENT_DATA`` → ``DEFER``.
  4. Risk (CVaR) → ``ABSTAIN`` / ``INSUFFICIENT_DATA`` → ``DEFER``.
  5. Compliance ``NEEDS_REVIEW`` (Art. 14 HITL) → ``DEFER``.
  6. All gates passed → ``PROCEED`` (+ Art.12 audit record when the
     audit seam is wired).

Batch 4 port notes:

- The compliance monitor and the Art.12 audit record are **Batch 5**
  (PORTING_MAP) — both are optional seams here: a ``None`` compliance
  gate is skipped (chain position preserved), and ``audit_factory``
  defaults to ``None`` so ``PipelineDecision.audit_record`` stays
  ``None`` until M5 lands.  The reference's PROCEED-must-carry-audit
  invariant returns with Batch 5.
- ``action_kind`` is an opaque vertical-neutral string (certificates
  precedent, golden rule 4) — the reference typed it as
  ``CardActionKind``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from core.brain.abstention.gap_registry import GapRecord, build_gap_record
from core.brain.abstention.gate import AbstentionGate
from core.brain.abstention.models import AbstentionDecision, AbstentionVerdict
from core.brain.certificates.cert import AutonomyCertificate
from core.brain.certificates.verifier import CertificateVerifier, VerifyOutcome
from core.brain.risk.gate import RiskGate
from core.brain.risk.models import RiskVerdict

__all__ = [
    "ComplianceGate",
    "ComplianceVerdict",
    "DecisionPipelineAdapter",
    "GateName",
    "GateOutcome",
    "PipelineDecision",
    "PipelineRequest",
    "PipelineVerdict",
]


logger = logging.getLogger(__name__)


class PipelineVerdict(StrEnum):
    """Three-valued final outcome.

    - ``PROCEED`` — every gate passed; the action may run.
    - ``DEFER`` — a gate said *not enough confidence right now*; the
      caller may resubmit later (or route to Human Input).
    - ``BLOCKED`` — a gate hard-failed; the action does not run.
    """

    PROCEED = "proceed"
    DEFER = "defer"
    BLOCKED = "blocked"


class GateName(StrEnum):
    """Stable identifiers for the gates the adapter or runtime emits."""

    COMPLIANCE = "compliance"
    ABSTENTION = "abstention"
    RISK = "risk"
    CERTIFICATE = "certificate"
    AUTONOMY = "autonomy"


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """One row in the per-gate audit trail."""

    gate: GateName
    verdict: str
    rationale: str

    def __post_init__(self) -> None:
        if not self.verdict:
            raise ValueError("verdict required")
        if not self.rationale:
            raise ValueError("rationale required")


@dataclass(frozen=True, slots=True)
class ComplianceVerdict:
    """Verdict shape the optional compliance gate returns (Batch 5 seam).

    ``kind`` is one of ``"ok"`` / ``"needs_review"`` / ``"blocked"`` —
    string-typed so the Batch 5 ComplianceMonitor's enum stringifies in.
    """

    kind: str
    rationale: str


@runtime_checkable
class ComplianceGate(Protocol):
    """Optional compliance slot in the chain (ComplianceMonitor, Batch 5)."""

    def evaluate(self, request: PipelineRequest, *, at: datetime) -> ComplianceVerdict:
        """Return the compliance verdict for one pipeline request."""
        ...


_COMPLIANCE_BLOCKED = "blocked"
_COMPLIANCE_NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class PipelineRequest:
    """Inputs the adapter consumes for one decision.

    Attributes:
        decision_id: Stable opaque identifier for audit-log stitching.
        property_id: Property the action targets.
        owner_id: Owner of the property.
        action_kind: Action class under consideration (opaque
            vertical-defined string).
        rationale: Caller-supplied human-readable rationale.
        provenance_digest: BLAKE2B-256 digest for the audit record.
        tool_id: Stable identifier the AbstentionGate uses.
        model_confidence: Model-reported confidence the AbstentionGate
            consults.
        handler_solver: Per-component solver the action uses.
        jurisdiction / registration_id / booking_dates /
        is_natural_person_decision / has_human_consent /
        compliance_extra: Compliance-gate inputs (consumed by the
            Batch 5 monitor through the request object).
        risk_samples: OutcomeSample tuple the RiskGate consults (empty
            → insufficient_data → DEFER).
        certificate: Optional cert; ``None`` skips the certificate gate
            (caller signals this action class needs no signed cert).
        autonomy_tier / planner_style / extra: Audit metadata.
    """

    decision_id: str
    property_id: str
    owner_id: str
    action_kind: str
    rationale: str
    provenance_digest: str
    tool_id: str
    model_confidence: float
    handler_solver: str
    jurisdiction: str | None = None
    registration_id: str | None = None
    booking_dates: tuple = ()
    is_natural_person_decision: bool = False
    has_human_consent: bool = False
    compliance_extra: Mapping[str, str] = field(default_factory=dict)
    risk_samples: tuple = ()
    certificate: AutonomyCertificate | None = None
    autonomy_tier: str | None = None
    planner_style: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)
    # Decision-time of the run (the inbound-event timestamp, CEN-15
    # ruling §E1) — consumed by the gap-emission hook and the as-of
    # retrieval path.  ``None`` falls back to the evaluation wall-clock.
    inbound_event_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "property_id",
            "owner_id",
            "action_kind",
            "rationale",
            "provenance_digest",
            "tool_id",
            "handler_solver",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} required")
        if not 0.0 <= self.model_confidence <= 1.0:
            raise ValueError("model_confidence must be in [0.0, 1.0]")


@dataclass(frozen=True, slots=True)
class PipelineDecision:
    """Aggregate output of the adapter.

    ``audit_record`` carries the Art.12 record once the Batch 5 audit
    seam is wired; until then it is always ``None`` (the reference's
    PROCEED-must-carry-audit invariant is reinstated with Batch 5).
    """

    verdict: PipelineVerdict
    rationale: str
    gate_trace: tuple[GateOutcome, ...]
    evaluated_at: datetime
    audit_record: Any | None = None

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        if not self.rationale:
            raise ValueError("rationale required")


class DecisionPipelineAdapter:
    """One-shot façade for the pre-tool-call gate chain."""

    def __init__(
        self,
        *,
        abstention_gate: AbstentionGate,
        risk_gate: RiskGate,
        certificate_verifier: CertificateVerifier,
        compliance_gate: ComplianceGate | None = None,
        audit_factory: Callable[[PipelineRequest, datetime], Any] | None = None,
        gap_sink: Callable[[GapRecord], None] | None = None,
    ) -> None:
        self._compliance = compliance_gate
        self._abstention = abstention_gate
        self._risk = risk_gate
        self._certificate = certificate_verifier
        self._audit_factory = audit_factory
        self._gap_sink = gap_sink

    def decide(
        self,
        request: PipelineRequest,
        *,
        at: datetime | None = None,
    ) -> PipelineDecision:
        """Run every gate; return the aggregate :class:`PipelineDecision`."""
        moment = self._now(at)
        trace: list[GateOutcome] = []

        compliance_row = self._compliance_step(request=request, at=moment)
        if compliance_row is not None:
            trace.append(compliance_row)
            if compliance_row.verdict == _COMPLIANCE_BLOCKED:
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

        abstention_row = self._abstention_step(request=request, moment=moment)
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

        if compliance_row is not None and compliance_row.verdict == _COMPLIANCE_NEEDS_REVIEW:
            return self._terminal(
                trace=trace,
                verdict=PipelineVerdict.DEFER,
                rationale=compliance_row.rationale,
                moment=moment,
            )

        audit = self._audit_factory(request, moment) if self._audit_factory is not None else None
        return PipelineDecision(
            verdict=PipelineVerdict.PROCEED,
            rationale="all gates passed",
            gate_trace=tuple(trace),
            evaluated_at=moment,
            audit_record=audit,
        )

    # ── gate steps ────────────────────────────────────────────── #

    def _compliance_step(
        self,
        *,
        request: PipelineRequest,
        at: datetime,
    ) -> GateOutcome | None:
        if self._compliance is None:
            return None
        verdict = self._compliance.evaluate(request, at=at)
        return GateOutcome(
            gate=GateName.COMPLIANCE,
            verdict=verdict.kind,
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

    def _abstention_step(self, *, request: PipelineRequest, moment: datetime) -> GateOutcome:
        decision = self._abstention.decide(
            tool_id=request.tool_id,
            model_confidence=request.model_confidence,
        )
        self._emit_gap(request=request, decision=decision, moment=moment)
        return GateOutcome(
            gate=GateName.ABSTENTION,
            verdict=decision.verdict.value,
            rationale=decision.rationale,
        )

    def _emit_gap(self, *, request: PipelineRequest, decision: AbstentionDecision, moment: datetime) -> None:
        """Knowledge-gap emission hook (CEN-15 Part B, Moat #4 → #5).

        Fires only when the abstention gate ABSTAINS (an
        INSUFFICIENT_DATA verdict is a calibration shortfall, not a
        knowledge gap) and only when a sink is wired.  Emission is
        observe-posture side-channel evidence: a sink failure is logged
        and swallowed — it must never change the chain's verdict.
        """
        if self._gap_sink is None:
            return
        gap = build_gap_record(
            decision,
            subject_ref=request.property_id,
            run_id=request.extra.get("run_id", request.decision_id),
            query=request.extra.get("query", request.rationale),
            as_of=request.inbound_event_at or moment,
            dispatched_at=moment,
            missing_predicate=request.extra.get("missing_predicate"),
        )
        if gap is None:
            return
        try:
            self._gap_sink(gap)
        except Exception:
            logger.exception("gap emission sink failed; verdict unaffected")

    def _risk_step(self, *, request: PipelineRequest) -> GateOutcome:
        decision = self._risk.decide(request.risk_samples)
        return GateOutcome(
            gate=GateName.RISK,
            verdict=decision.verdict.value,
            rationale=decision.rationale,
        )

    # ── helpers ───────────────────────────────────────────────── #

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
            return datetime.now(UTC)
        if at.tzinfo is None:
            raise ValueError("`at` must be tz-aware when provided")
        return at
