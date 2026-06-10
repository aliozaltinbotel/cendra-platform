"""Continuous compliance monitor (Moat #10).

The monitor is the runtime middleware the action pipeline calls
*on every side-effecting tool-call*.  It evaluates every
registered :class:`ComplianceCheck` against the proposed action
context and returns a structured :class:`ComplianceVerdict`
carrying every violation the checks reported.

A single :class:`ComplianceViolation` carries the rule id, the
human-readable reason, and the severity tier so the runtime can
distinguish *block* (PASS / FAIL) from *needs human review*
outcomes — the latter mirrors EU AI Act Art. 14 (a competent human
must decide).

Defensibility: the monitor closes ``domain axis G`` (continuous
compliance monitor under Reg 2024/1028 + EU AI Act Art. 12 / 14 /
72) from latest_research §2.4.  No proptech competitor surveyed
runs a per-action runtime compliance gate.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final, Protocol

__all__ = [
    "ComplianceCheck",
    "ComplianceContext",
    "ComplianceMonitor",
    "ComplianceSeverity",
    "ComplianceVerdict",
    "ComplianceViolation",
    "VerdictKind",
]


logger = logging.getLogger(__name__)


class ComplianceSeverity(StrEnum):
    """Severity tiers for one violation row.

    - ``BLOCK``: action must not proceed.  Hard fail.
    - ``REVIEW``: action requires explicit human approval (EU AI
      Act Art. 14 oversight; GDPR Art. 22 adverse-decision HITL).
    - ``WARN``: borderline / informational; the audit log records
      it but the action proceeds.
    """

    BLOCK = "block"
    REVIEW = "review"
    WARN = "warn"


class VerdictKind(StrEnum):
    """Three-valued runtime verdict."""

    PASS = "pass"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ComplianceContext:
    """Inputs every check can consult.

    Attributes:
        property_id: Property the action targets.
        owner_id: Owner of the property.
        action_kind: Action class under consideration.
        jurisdiction: City / region code.
        registration_id: Authority-issued unit registration.
        booking_dates: Tuple of dates the action affects.
        is_natural_person_decision: ``True`` when the action
            produces an adverse decision against a natural person
            (GDPR Art. 22 trigger).
        has_human_consent: ``True`` when an approving human has
            explicitly consented for this action.
        extra: Free-form per-check metadata.
    """

    property_id: str
    owner_id: str
    action_kind: str
    jurisdiction: str | None = None
    registration_id: str | None = None
    booking_dates: tuple[date, ...] = ()
    is_natural_person_decision: bool = False
    has_human_consent: bool = False
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComplianceViolation:
    """One row reported by a check.

    Attributes:
        rule_id: Stable opaque identifier (e.g. ``"reg_2024_1028.
            registration_id_required"``).
        severity: Severity tier.
        reason: One-line plain-English explanation; consumed by
            the audit log.
        evidence: Optional free-form diagnostic data the regulator
            can replay later.
    """

    rule_id: str
    severity: ComplianceSeverity
    reason: str
    evidence: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComplianceVerdict:
    """Aggregate runtime decision.

    Attributes:
        kind: One of :class:`VerdictKind`.
        violations: Tuple of every reported violation, in the
            order checks ran.  Empty when the verdict is
            :attr:`VerdictKind.PASS`.
        evaluated_at: tz-aware UTC instant the monitor evaluated
            the context.
        rationale: One-line summary of the highest-severity row.
    """

    kind: VerdictKind
    violations: tuple[ComplianceViolation, ...]
    evaluated_at: datetime
    rationale: str

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        if not self.rationale:
            raise ValueError("rationale required")


class ComplianceCheck(Protocol):
    """Single compliance rule.

    Implementations return ``None`` when the context passes the
    rule; otherwise they return a populated
    :class:`ComplianceViolation`.
    """

    def __call__(
        self,
        context: ComplianceContext,
    ) -> ComplianceViolation | None: ...


_BLOCK_FIRST: Final[Mapping[ComplianceSeverity, int]] = {
    ComplianceSeverity.BLOCK: 0,
    ComplianceSeverity.REVIEW: 1,
    ComplianceSeverity.WARN: 2,
}


class ComplianceMonitor:
    """Runtime middleware combining many :class:`ComplianceCheck` rules.

    The monitor evaluates every registered check (order
    preserved); aggregates the violations; and computes a
    :class:`VerdictKind`:

    - any ``BLOCK`` row → :attr:`VerdictKind.BLOCKED`;
    - any ``REVIEW`` row (and no ``BLOCK``) →
      :attr:`VerdictKind.NEEDS_REVIEW`;
    - everything else → :attr:`VerdictKind.PASS`.
    """

    def __init__(
        self,
        *,
        checks: Sequence[ComplianceCheck],
        clock: type | None = None,
    ) -> None:
        if not checks:
            raise ValueError("at least one check required")
        self._checks = tuple(checks)

    def evaluate(
        self,
        context: ComplianceContext,
        *,
        at: datetime | None = None,
    ) -> ComplianceVerdict:
        """Run every check and return the aggregate verdict."""
        moment = at or datetime.now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        violations: list[ComplianceViolation] = []
        for check in self._checks:
            row = check(context)
            if row is not None:
                violations.append(row)
        kind = self._aggregate_kind(violations)
        rationale = self._rationale(kind, violations)
        if violations:
            logger.info(
                "compliance.evaluated kind=%s violations=%s property_id=%s",
                kind.value,
                len(violations),
                context.property_id,
            )
        return ComplianceVerdict(
            kind=kind,
            violations=tuple(violations),
            evaluated_at=moment,
            rationale=rationale,
        )

    @staticmethod
    def _aggregate_kind(
        violations: Sequence[ComplianceViolation],
    ) -> VerdictKind:
        if any(v.severity is ComplianceSeverity.BLOCK for v in violations):
            return VerdictKind.BLOCKED
        if any(v.severity is ComplianceSeverity.REVIEW for v in violations):
            return VerdictKind.NEEDS_REVIEW
        return VerdictKind.PASS

    @staticmethod
    def _rationale(
        kind: VerdictKind,
        violations: Sequence[ComplianceViolation],
    ) -> str:
        if kind is VerdictKind.PASS and not violations:
            return "no compliance violations"
        if kind is VerdictKind.PASS:
            return f"{len(violations)} warning(s) only"
        ordered = sorted(
            violations,
            key=lambda v: _BLOCK_FIRST[v.severity],
        )
        top = ordered[0]
        return f"{kind.value}: {top.rule_id} ({top.severity.value}) — {top.reason}"
