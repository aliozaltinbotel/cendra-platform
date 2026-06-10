"""Value objects for the decision-evidence read model.

An :class:`EvidenceBundle` is the canonical "why" payload returned by
the ``/api/decisions/{decision_id}/evidence`` endpoint.  It is a pure
read projection over four independent evidence stores:

- **Rules** — :class:`~brain_engine.patterns.models.PatternRule` matches
  (summarised as :class:`RulePick`).
- **Cases** — prior :class:`~brain_engine.patterns.models.DecisionCase`
  rows that support or contradict the current decision
  (:class:`CasePick`).
- **Prompts** — memory-derived hints (:class:`PromptPick`).
- **Blockers** — pending :class:`~brain_engine.blockers` items
  (:class:`BlockerPick`).

Every pick carries an :class:`EvidenceWeight` so the UI can visualise
agreement vs. disagreement without re-running the match logic.

Value objects are frozen + slots for caching and deterministic
equality semantics.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EvidenceWeight(StrEnum):
    """Direction of influence a pick has on the decision."""

    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    NEUTRAL = "neutral"


class DecisionReference(StrEnum):
    """Kind of identifier used to look up a decision.

    Evidence is almost always fetched by ``case_id`` (an existing
    :class:`DecisionCase`), but the same composer is reused for live
    *proposed* decisions that have no case yet — those are addressed
    by ``correlation_id``.
    """

    CASE_ID = "case_id"
    CORRELATION_ID = "correlation_id"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Query envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceQuery:
    """Arguments that identify which decision to explain."""

    decision_id: str
    reference: DecisionReference = DecisionReference.CASE_ID
    property_id: str | None = None
    scenario: str | None = None
    guest_id: str | None = None
    owner_id: str | None = None
    limit: int = 10


# ---------------------------------------------------------------------------
# Pick value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RulePick:
    """One :class:`PatternRule` summarised for the evidence bundle."""

    pattern_id: str
    scenario: str
    scope: str
    scope_id: str
    confidence: float
    support_count: int
    counterexample_ratio: float
    risk_level: str
    execution_mode: str
    weight: EvidenceWeight = EvidenceWeight.SUPPORTING
    action_type: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CasePick:
    """One prior :class:`DecisionCase` summarised for the bundle."""

    case_id: str
    scenario: str
    stage: str
    decision_type: str
    weight: EvidenceWeight
    resolution_type: str | None = None
    revenue_impact: float | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PromptPick:
    """Memory-derived hint as it appears on the evidence card."""

    prompt_id: str
    source: str
    kind: str
    text: str
    relevance: float = 0.5
    reference_id: str | None = None


@dataclass(frozen=True, slots=True)
class BlockerPick:
    """Live blocker attached to the decision context."""

    blocker_id: str
    blocker_type: str
    severity: str
    reason: str
    introduced_at: datetime | None = None
    resolves_on: str | None = None


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """Aggregate counters + convenience flags for the bundle."""

    rule_count: int
    case_count: int
    prompt_count: int
    blocker_count: int
    supporting_cases: int
    contradicting_cases: int
    has_hard_blocker: bool


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Full evidence payload for a single decision."""

    query: EvidenceQuery
    rules: tuple[RulePick, ...] = ()
    cases: tuple[CasePick, ...] = ()
    prompts: tuple[PromptPick, ...] = ()
    blockers: tuple[BlockerPick, ...] = ()
    errors: tuple[str, ...] = ()
    bundle_id: str = field(default_factory=_new_id)
    assembled_at: datetime = field(default_factory=_utc_now)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Aggregate helpers
    # ------------------------------------------------------------------

    @property
    def summary(self) -> EvidenceSummary:
        """Compute the pre-digested counters for the UI."""
        supporting = sum(
            1 for c in self.cases
            if c.weight is EvidenceWeight.SUPPORTING
        )
        contradicting = sum(
            1 for c in self.cases
            if c.weight is EvidenceWeight.CONTRADICTING
        )
        return EvidenceSummary(
            rule_count=len(self.rules),
            case_count=len(self.cases),
            prompt_count=len(self.prompts),
            blocker_count=len(self.blockers),
            supporting_cases=supporting,
            contradicting_cases=contradicting,
            has_hard_blocker=any(
                b.severity in {"hard", "critical"}
                for b in self.blockers
            ),
        )

    @property
    def strongest_rule(self) -> RulePick | None:
        """Highest-confidence supporting rule, if any."""
        supporting = [
            r for r in self.rules
            if r.weight is EvidenceWeight.SUPPORTING
        ]
        if not supporting:
            return None
        return max(supporting, key=lambda r: r.confidence)

    @property
    def is_empty(self) -> bool:
        """Whether the bundle carries no evidence at all."""
        return not (
            self.rules or self.cases or self.prompts or self.blockers
        )
