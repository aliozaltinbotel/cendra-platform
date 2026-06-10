"""Value objects for the Decision Pipeline Adapter (M19)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.certificates.cert import AutonomyCertificate
from brain_engine.compliance.art12_decision import Art12Decision


__all__ = [
    "GateName",
    "GateOutcome",
    "PipelineDecision",
    "PipelineRequest",
    "PipelineVerdict",
]


class PipelineVerdict(StrEnum):
    """Three-valued final outcome.

    - ``PROCEED`` — every gate passed; the action may run and the
      adapter emits an Art.12 audit record.
    - ``DEFER`` — a gate said *not enough confidence right now*
      (insufficient calibration data, conformal threshold, or
      EU AI Act Art. 14 review).  The action does *not* run; the
      caller may resubmit later.
    - ``BLOCKED`` — a gate hard-fails (Reg 2024/1028 violation,
      Wilson LB too low, CVaR over policy threshold, bad cert).
      The action does not run.
    """

    PROCEED = "proceed"
    DEFER = "defer"
    BLOCKED = "blocked"


class GateName(StrEnum):
    """Stable identifiers for the gates the adapter runs."""

    COMPLIANCE = "compliance"
    ABSTENTION = "abstention"
    RISK = "risk"
    CERTIFICATE = "certificate"


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """One row in the per-gate audit trail.

    Attributes:
        gate: Which gate produced this row.
        verdict: The verdict the gate emitted (typed as a string
            so each gate's enum can stringify into the same
            audit log).
        rationale: One-line plain-English summary.
    """

    gate: GateName
    verdict: str
    rationale: str

    def __post_init__(self) -> None:
        if not self.verdict:
            raise ValueError("verdict required")
        if not self.rationale:
            raise ValueError("rationale required")


@dataclass(frozen=True, slots=True)
class PipelineRequest:
    """Inputs the adapter consumes for one decision.

    Attributes:
        decision_id: Stable opaque identifier for audit-log
            stitching.
        property_id: Property the action targets.
        owner_id: Owner of the property.
        action_kind: Action class under consideration.
        jurisdiction: City code (passed to ComplianceMonitor).
        registration_id: Unit's registration_id (Reg 2024/1028).
        booking_dates: Tuple of dates the action affects.
        is_natural_person_decision: GDPR Art. 22 trigger.
        has_human_consent: Whether HITL consent has been
            recorded for this action.
        compliance_extra: Extra metadata for the ComplianceMonitor
            (e.g. ``"never_ai_category"``).
        tool_id: Stable identifier the AbstentionGate uses.
        model_confidence: Model-reported confidence the
            AbstentionGate consults.
        risk_samples: OutcomeSample tuple the RiskGate consults
            (can be empty when no risk distribution is available
            — gate will return insufficient_data → DEFER).
        certificate: Optional cert; when ``None``, the
            certificate gate is skipped (caller signals "this
            action class does not require a signed cert").
        provenance_digest: BLAKE2B-256 digest the Art.12 record
            embeds.
        autonomy_tier: Tier the certificate authorised; emitted
            in the Art.12 record.  Carried in by the caller.
        planner_style: Planner style (M4); emitted in the Art.12
            record.
        handler_solver: Per-component solver the action uses
            (GR00T P2).
        rationale: Caller-supplied human-readable rationale for
            the action; ends up in the Art.12 record's
            ``rationale`` field on PROCEED.
        extra: Free-form audit metadata.
    """

    decision_id: str
    property_id: str
    owner_id: str
    action_kind: CardActionKind
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

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "property_id",
            "owner_id",
            "rationale",
            "provenance_digest",
            "tool_id",
            "handler_solver",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} required")
        if not 0.0 <= self.model_confidence <= 1.0:
            raise ValueError(
                "model_confidence must be in [0.0, 1.0]"
            )


@dataclass(frozen=True, slots=True)
class PipelineDecision:
    """Aggregate output of the adapter.

    Attributes:
        verdict: Final :class:`PipelineVerdict`.
        rationale: One-line summary of the deciding gate.
        gate_trace: Ordered tuple of per-gate :class:`GateOutcome`
            rows.
        evaluated_at: tz-aware UTC instant the adapter decided.
        audit_record: Art.12 audit record (M5) — present *only*
            when ``verdict is PROCEED``; otherwise ``None`` (the
            failing gate's rationale is already in the trace).
    """

    verdict: PipelineVerdict
    rationale: str
    gate_trace: tuple[GateOutcome, ...]
    evaluated_at: datetime
    audit_record: Art12Decision | None = None

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        if not self.rationale:
            raise ValueError("rationale required")
        if (
            self.verdict is PipelineVerdict.PROCEED
            and self.audit_record is None
        ):
            raise ValueError(
                "PROCEED verdict must carry an audit_record"
            )
        if (
            self.verdict is not PipelineVerdict.PROCEED
            and self.audit_record is not None
        ):
            raise ValueError(
                "non-PROCEED verdicts must not carry an "
                "audit_record"
            )
