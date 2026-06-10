"""Core data models for the decision-pattern learning subsystem.

Defines the two fundamental abstractions that transform Brain Engine from
a chatbot into an operational brain:

- **DecisionCase** — one operational situation with full context, decision,
  and outcome.  Every guest interaction that triggers an action or a
  deliberate non-action is captured as a DecisionCase.
- **PatternRule** — a learned behavioural rule extracted from repeated,
  validated DecisionCases.  Rules are scoped (property / owner / portfolio)
  and carry confidence, risk, and execution-mode metadata.

Supporting enumerations model the booking lifecycle (9 stages), scenario
taxonomy, decision types, risk levels, and execution modes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


# Vertical vocabulary note (genericised at port time, golden rule 4):
# the reference declared ``BookingStage`` (10-stage booking lifecycle) and
# ``Scenario`` (~100 hospitality scenario buckets) as kernel StrEnums.  In
# cendra-platform both are opaque, vertical-neutral ``str`` kinds — the
# vocabulary itself is pack / tenant data (packs/hospitality/, Batch 6
# loader; per-tenant registry per PORTING_MAP Batch 2 autonomy note).
# The single value the kernel itself relies on is the unclassified bucket:

SCENARIO_GENERAL: Final[str] = "general"
"""Scenario value for generic / unclassified cases — never learnable."""


class DecisionType(StrEnum):
    """Taxonomy of actions the engine can take or propose.

    Mirrors the Cendra operational vocabulary so that pattern rules
    are directly actionable.

    The ``DEFER`` member encodes a non-action: the PM saw the
    inquiry and chose to wait (or to ignore until a later trigger,
    e.g. "5 days before check-in is too early — answer later").
    Without it the case_builder would have to fabricate an ``ASK``
    or ``INFORM`` decision and the pattern miner would mistake the
    silence for engagement.

    ``MODIFY_BOOKING``, ``REFUND`` and ``CLAIM`` close the
    ali.md §7 taxonomy: a modification of dates/guests, a money
    return, and a damage / insurance claim are operationally
    distinct from ``APPROVE`` / ``CHARGE`` — they have their own
    legal envelopes and audit trails, so the pattern miner must
    keep them separate to avoid contaminating the dominant
    decision per scenario.
    """

    ASK = "ask"
    APPROVE = "approve"
    DENY = "deny"
    CHARGE = "charge"
    QUOTE = "quote"
    BLOCK = "block"
    ESCALATE = "escalate"
    DISPATCH = "dispatch"
    FETCH_LIVE_DATA = "fetch_live_data"
    OFFER = "offer"
    INFORM = "inform"
    RELEASE = "release"
    DEFER = "defer"
    MODIFY_BOOKING = "modify_booking"
    REFUND = "refund"
    CLAIM = "claim"


class ExecutionMode(StrEnum):
    """How a matched PatternRule should be executed at runtime.

    The mode is determined by the combination of confidence and risk
    level when the rule is extracted.
    """

    AUTO = "auto"
    ASK = "ask"
    APPROVAL = "approval"
    BLOCK = "block"


class RiskLevel(StrEnum):
    """Risk classification for pattern rules.

    Higher risk lowers the confidence threshold required for autonomous
    execution and may force approval or blocking.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PatternScope(StrEnum):
    """Scope at which a PatternRule applies.

    A rule learned for one property may not generalise to others owned
    by the same person, and vice versa.
    """

    PROPERTY = "property"
    OWNER = "owner"
    PORTFOLIO = "portfolio"
    GUEST = "guest"


class ResolutionType(StrEnum):
    """How a DecisionCase was ultimately resolved."""

    AUTO_RESOLVED = "auto_resolved"
    PM_APPROVED = "pm_approved"
    PM_DENIED = "pm_denied"
    PM_MODIFIED = "pm_modified"
    GUEST_ACCEPTED = "guest_accepted"
    GUEST_REJECTED = "guest_rejected"
    TIMEOUT = "timeout"
    ESCALATED = "escalated"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    """Return current UTC datetime — extracted for testability."""
    return datetime.now(UTC)


def _new_id() -> str:
    """Generate a unique identifier for cases and rules."""
    return uuid.uuid4().hex


class CaseSource(StrEnum):
    """Origin of a :class:`DecisionCase`.

    ``LIVE`` cases are emitted by the runtime cognitive loop and carry
    direct PM/guest/engine evidence.  ``HISTORICAL`` cases are replayed
    by :class:`HistoricalCaseExtractor` from archived conversations
    during onboarding bootstrap; they are lossy by nature (side-channel
    decisions, missing outcomes) and must be down-weighted during
    pattern extraction so they never outweigh fresh observations.
    """

    LIVE = "live"
    HISTORICAL = "historical"


@dataclass(frozen=True, slots=True)
class PatternOrigin:
    """Provenance trail attached to a :class:`DecisionCase` or
    :class:`PatternRule` (FL-12).

    Closes Ali's Turkish requirement #1: *"Oluşturduğumuz rule hangi
    foundationa göre oluşturuldu bunu loglayan bir yapı kurmak
    mantıklı."*  Every learnt rule must trace back to the foundation
    scenarios, raw events, and signals that birthed it.  Storing this
    trail on the case / rule itself keeps the provenance edge local —
    no separate join table to keep in sync, no orphan rows when a
    rule is rebuilt.

    Three orthogonal lists, all optional:

    * ``foundation_scenario_ids`` — slugs from
      :data:`brain_engine.patterns.foundation_registry.FoundationScenario.
      scenario_id`.  Plural because a cross-scenario rule may rest on
      cases that classified to different foundation rows; the
      foundation matcher records every contributor.  Subsumes the
      singular :pyattr:`DecisionCase.foundation_scenario_id` field
      shipped in FL-03 — the singular field still records the
      *dominant* foundation row for query convenience.
    * ``source_event_ids`` — opaque identifiers for the upstream
      events (incoming guest message ids, reservation change ids,
      PMS sync events, vendor task updates …) that triggered the
      decision.  Empty until FL-16 (Foundation Analysis Orchestrator)
      starts populating it from the event firehose.
    * ``contributing_signal_ids`` — identifiers for the proactive
      signals or surprise-detector emissions that informed the
      decision.  Empty in Sprint 2; FL-09 (deferred Proactive layer)
      will start emitting them.

    Backward-compatible: every field defaults to the empty tuple so
    callers that pre-date FL-12 keep constructing cases / rules
    without touching the origin attribute.  Postgres rows pre-dating
    migration 028 deserialise to the empty
    :class:`PatternOrigin` via :func:`_decode_origin`.

    Attributes:
        foundation_scenario_ids: Foundation catalog slugs that
            contributed to the decision.  Order is *insertion order
            from the matcher* — strongest match first when known.
        source_event_ids: Upstream event identifiers (message ids,
            reservation change ids, PMS event ids).
        contributing_signal_ids: Proactive signal identifiers that
            informed the decision.
    """

    foundation_scenario_ids: tuple[str, ...] = ()
    source_event_ids: tuple[str, ...] = ()
    contributing_signal_ids: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        """Whether no provenance has been recorded yet.

        Used by serialisation layers to short-circuit storing an
        empty trail — the Postgres column defaults to ``'{}'::jsonb``
        so an empty origin needs no write.
        """
        return not self.foundation_scenario_ids and not self.source_event_ids and not self.contributing_signal_ids

    def to_jsonable(self) -> dict[str, list[str]]:
        """Render as a JSON-safe dict for JSONB storage.

        Only non-empty lists are emitted so the payload stays
        compact and an empty origin survives a round-trip as
        ``{}`` — matching the Postgres column default.
        """
        payload: dict[str, list[str]] = {}
        if self.foundation_scenario_ids:
            payload["foundation_scenario_ids"] = list(
                self.foundation_scenario_ids,
            )
        if self.source_event_ids:
            payload["source_event_ids"] = list(self.source_event_ids)
        if self.contributing_signal_ids:
            payload["contributing_signal_ids"] = list(
                self.contributing_signal_ids,
            )
        return payload

    @classmethod
    def from_jsonable(
        cls,
        payload: dict[str, Any] | None,
    ) -> PatternOrigin:
        """Build a :class:`PatternOrigin` from a JSONB payload.

        Trusts the writer (the store itself) but coerces every
        value through :class:`tuple` of ``str`` so a hand-edited
        Postgres row with the wrong shape collapses to an empty
        list instead of poisoning the call site.  ``None`` /
        non-dict payloads degrade to the empty origin so a row
        that pre-dates migration 028 still rebuilds cleanly.
        """
        if not isinstance(payload, dict):
            return cls()
        return cls(
            foundation_scenario_ids=_coerce_str_tuple(
                payload.get("foundation_scenario_ids"),
            ),
            source_event_ids=_coerce_str_tuple(
                payload.get("source_event_ids"),
            ),
            contributing_signal_ids=_coerce_str_tuple(
                payload.get("contributing_signal_ids"),
            ),
        )


def _coerce_str_tuple(raw: Any) -> tuple[str, ...]:
    """Cast a JSON value into a tuple of strings.

    Tolerates ``None``, scalar strings, and lists.  Anything else
    collapses to ``()`` so a malformed JSONB row cannot raise during
    deserialisation.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if isinstance(raw, list | tuple):
        return tuple(str(item) for item in raw if item is not None)
    return ()


@dataclass(frozen=True, slots=True)
class DecisionAction:
    """The action taken (or proposed) for a DecisionCase.

    Attributes:
        action_type: What kind of action (approve, deny, charge, …).
        params: Action-specific parameters (amount, message, target, …).
    """

    action_type: DecisionType
    params: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"DecisionAction({self.action_type.value}, params={self.params})"


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Observable outcome of a DecisionCase.

    Records what actually happened *after* the engine acted, which is
    the signal used for learning (reinforcement from real outcomes).

    Attributes:
        guest_replied: Whether the guest responded to the action.
        human_overrode: Whether a PM manually overrode the engine decision.
        approval_required: Whether the action went through approval flow.
        approved: Whether the PM approved (None if no approval needed).
        successful: Whether the action achieved its goal.
        resolution_type: How the case was ultimately resolved.
        revenue_impact: Estimated revenue impact in property currency.
    """

    guest_replied: bool = False
    human_overrode: bool = False
    approval_required: bool = False
    approved: bool | None = None
    successful: bool | None = None
    resolution_type: ResolutionType | None = None
    revenue_impact: float | None = None

    @property
    def is_positive_signal(self) -> bool:
        """Whether this outcome counts as positive evidence for learning.

        An outcome is positive when: (a) successful AND not overridden, or
        (b) approved by PM without modification.
        """
        if self.human_overrode:
            return False
        if self.successful is True:
            return True
        if self.approved is True and self.resolution_type == ResolutionType.PM_APPROVED:
            return True
        return False

    @property
    def is_negative_signal(self) -> bool:
        """Whether this outcome counts as negative evidence (counter-example).

        Negative when: PM denied or modified, or action marked unsuccessful.
        """
        if self.human_overrode:
            return True
        if self.successful is False:
            return True
        if self.resolution_type in {
            ResolutionType.PM_DENIED,
            ResolutionType.PM_MODIFIED,
        }:
            return True
        return False

    @classmethod
    def from_decision_type(
        cls,
        decision_type: DecisionType,
    ) -> CaseOutcome:
        """Materialise a learnable outcome from a deliberate PM decision.

        Replayed and live conversations both arrive with the PM's
        recorded ``decision_type`` but no observable post-hoc outcome
        (we never saw the guest's downstream behaviour).  This
        factory synthesises a :class:`CaseOutcome` carrying the
        :attr:`resolution_type` the PatternExtractor needs to mark
        the case ``has_outcome=True`` so :attr:`DecisionCase.is_learnable`
        returns True.

        Deliberate PM decisions (APPROVE / CHARGE / OFFER / RELEASE /
        DENY / BLOCK) all collapse to a ``PM_APPROVED + successful``
        outcome — the :class:`DecisionAction.action_type` itself
        carries the grant/refuse distinction, while the outcome only
        records that the PM took a deliberate action successfully.
        The DENY/BLOCK collapse is intentional: it routes the case
        into :meth:`PatternExtractor._split_by_signal`'s *positive*
        pool so a DENY rule can form from N consistent refusals
        (closes Mümin round-4 #4 on the live path; the historical
        path already wired this via
        :func:`brain_engine.onboarding.historical_case_extractor.
        _outcome_for_historical`).

        :attr:`DecisionType.ESCALATE` lands as
        :attr:`ResolutionType.ESCALATED` with ``successful=False``;
        every remaining conversational decision (INFORM, ASK, QUOTE,
        DEFER, DISPATCH, FETCH_LIVE_DATA) collapses to
        :attr:`ResolutionType.AUTO_RESOLVED`.  ``human_overrode``
        stays False — the PM authored the response themselves.
        """
        if decision_type in _DELIBERATE_DECISION_TYPES:
            return cls(
                approved=True,
                successful=True,
                resolution_type=ResolutionType.PM_APPROVED,
            )
        if decision_type is DecisionType.ESCALATE:
            return cls(
                successful=False,
                resolution_type=ResolutionType.ESCALATED,
            )
        return cls(
            successful=True,
            resolution_type=ResolutionType.AUTO_RESOLVED,
        )


_DELIBERATE_DECISION_TYPES: Final[frozenset[DecisionType]] = frozenset(
    {
        DecisionType.APPROVE,
        DecisionType.CHARGE,
        DecisionType.OFFER,
        DecisionType.RELEASE,
        DecisionType.DENY,
        DecisionType.BLOCK,
    }
)


# ---------------------------------------------------------------------------
# DecisionCase — the core learning unit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionCase:
    """One operational decision with full context, action, and outcome.

    A DecisionCase captures everything needed to learn *why* a decision
    was made and *whether it worked*:

    - **Who**: guest, owner, property
    - **When**: booking stage, timestamps
    - **What**: scenario, extracted entities, message
    - **Context**: PMS snapshot, calendar snapshot, ops state, guest history
    - **Decision**: action type + parameters
    - **Outcome**: success, override, revenue impact

    DecisionCases are immutable value objects.  Once created they are
    never modified — new cases are appended, not updated.

    Attributes:
        case_id: Unique identifier (auto-generated).
        stage: Booking lifecycle stage when the decision occurred.
        scenario: Operational scenario classification.
        property_id: Property identifier from PMS.
        owner_id: Property-owner identifier.
        reservation_id: Reservation this case relates to (if any).
        guest_id: Guest identifier (if known).
        message_text: Original guest/PM message that triggered the case.
        message_language: Detected language of the message.
        extracted_entities: Entities parsed from the message (amounts,
            dates, counts, item names, …).
        pms_snapshot: Relevant PMS state at decision time.
        calendar_snapshot: Calendar availability around the reservation.
        ops_snapshot: Operational state (cleaning, maintenance, …).
        guest_snapshot: Guest history summary at decision time.
        decision: The action taken or proposed.
        response_text: The text response sent to the guest.
        executed_actions: Tuple of action identifiers that were executed.
        outcome: Observable outcome (filled asynchronously).
        evidence_source_ids: IDs of memory / knowledge entries used.
        created_at: UTC timestamp of case creation (the extraction
            wall-clock — *not* when the decision happened).
        decision_at: When the underlying decision actually occurred —
            the source guest message's ``sent_at`` for historical
            replay cases.  The temporal knowledge graph anchors the
            case on this *event time* (``fanout._record_kg`` reads
            ``decision_at or created_at``), so bi-temporal time-travel
            by archive works: ``event_time`` = when the guest asked,
            ``record_time`` = ``now()`` when we onboarded.  ``None``
            for live cases that never carried a source timestamp, where
            ``created_at`` ≈ the event time anyway.
        source: Origin of the case — ``LIVE`` for runtime-captured
            decisions and ``HISTORICAL`` for replayed archive cases.
            Used by :class:`PatternMiner` to down-weight lossy
            bootstrap evidence.
        orchestrator_verdict: §10 priority-chain verdict captured at
            the moment the case was logged.  Empty when the request
            ran with the orchestrator disabled (legacy / unit-test
            paths).  Schema:
            ``{"tier": str, "action": str, "mode": str,
            "rationale": str, "params": dict}``.  Persisted as JSONB
            on the ``decision_cases`` table — see migration
            ``015_orchestrator_verdict``.  Pattern miners read this to
            distinguish "PM agreed with the engine" from "PM overrode
            the engine"; without it the human-overrode signal in
            :class:`CaseOutcome` cannot be attributed to a specific
            tier.
    """

    stage: str
    scenario: str
    property_id: str
    owner_id: str
    decision: DecisionAction
    case_id: str = field(default_factory=_new_id)
    reservation_id: str | None = None
    guest_id: str | None = None
    message_text: str = ""
    message_language: str = "en"
    extracted_entities: dict[str, Any] = field(default_factory=dict)
    pms_snapshot: dict[str, Any] = field(default_factory=dict)
    calendar_snapshot: dict[str, Any] = field(default_factory=dict)
    ops_snapshot: dict[str, Any] = field(default_factory=dict)
    guest_snapshot: dict[str, Any] = field(default_factory=dict)
    response_text: str = ""
    executed_actions: tuple[str, ...] = ()
    outcome: CaseOutcome = field(default_factory=CaseOutcome)
    evidence_source_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=_utc_now)
    decision_at: datetime | None = None
    source: CaseSource = CaseSource.LIVE
    orchestrator_verdict: dict[str, Any] = field(default_factory=dict)
    # Sprint-4 soft archive — populated by the nightly archiver
    # when a case is moved out of the hot mining window
    # (default 90 days unused + not referenced by any active
    # PatternRule.source_case_ids).  ``None`` while the case is
    # part of the working set; the value records *when* the
    # registry stopped consulting the case so audit can replay
    # historical state.  All learning-side queries
    # (PatternMiner, PatternExtractor) filter
    # ``archived_at IS NULL`` so archived cases stop influencing
    # rules without losing the provenance edge to existing
    # rule.source_case_ids.
    archived_at: datetime | None = None
    # FL-03 — Foundation Layer bridge.  Optional reference to the
    # ``foundation_scenarios_reactive`` catalog row that classified
    # this case at logging time.  ``None`` while the orchestrator
    # is not yet wired (Sprints 1-2) and on cases that pre-date
    # FL-16's foundation matcher integration.  The string is a
    # deterministic slug produced by
    # :func:`~brain_engine.patterns.foundation_registry._build_id`
    # — kept here as a plain TEXT FK so we never need to widen the
    # in-code ``Scenario`` enum to the 469 catalog entries.  Once
    # populated, the field unlocks the rule-origin trail (FL-12),
    # memory-tier routing (FL-04), and safety gating (FL-05).
    foundation_scenario_id: str | None = None
    # FL-12 — Provenance trail.  Records every foundation scenario,
    # raw event, and proactive signal that contributed to this
    # case.  Subsumes the singular ``foundation_scenario_id`` for
    # the multi-contributor case (FL-16 populates both: the
    # singular field gets the dominant match, the origin gets all
    # candidates).  Defaults to the empty trail so legacy call
    # sites do not break.
    origin: PatternOrigin = field(default_factory=PatternOrigin)

    def __repr__(self) -> str:
        return (
            f"DecisionCase(id={self.case_id[:8]}…, "
            f"stage={self.stage}, "
            f"scenario={self.scenario}, "
            f"property={self.property_id}, "
            f"decision={self.decision.action_type.value})"
        )

    @property
    def has_outcome(self) -> bool:
        """Whether the outcome has been filled with real data."""
        return self.outcome.resolution_type is not None

    @property
    def is_learnable(self) -> bool:
        """Whether this case can contribute to pattern extraction.

        A case is learnable when it has a known outcome and is not a
        generic/unclassified scenario.
        """
        return self.has_outcome and self.scenario != SCENARIO_GENERAL


# ---------------------------------------------------------------------------
# PatternRule — learned behavioural rule
# ---------------------------------------------------------------------------

# Minimum support count before a rule can be promoted to AUTO execution.
MIN_SUPPORT_AUTO: Final[int] = 5

# Maximum counterexample ratio for a rule to remain valid.
MAX_COUNTEREXAMPLE_RATIO: Final[float] = 0.15

# Confidence thresholds for execution mode assignment.
CONFIDENCE_AUTO_THRESHOLD: Final[float] = 0.85
CONFIDENCE_ASK_THRESHOLD: Final[float] = 0.6


@dataclass(frozen=True, slots=True)
class PatternRule:
    """A learned behavioural rule extracted from repeated DecisionCases.

    PatternRules encode *what the PM does in situation X* after observing
    enough consistent evidence.  They are the bridge between episodic
    experience (DecisionCases) and procedural memory.

    Rules are scoped (property / owner / portfolio / guest) so that
    "PM allows crib for 5+ night stays at Villa Azul" does not bleed
    into a different property where cribs are not available.

    Attributes:
        pattern_id: Unique identifier (auto-generated).
        scenario: Which scenario this rule addresses.
        scope: At what level the rule generalises.
        scope_id: Identifier matching the scope (property_id, owner_id, …).
        conditions: Deterministic conditions that must be true for the
            rule to fire.  Keys are field names from BookingFeatures;
            values are ``{"operator": "gte", "value": 5}``-style dicts.
        action: The action to take when conditions match.
        blocker_types: Blocker types that must be clear before execution.
        support_count: Number of positive DecisionCases backing this rule.
        counterexample_count: Number of negative cases contradicting it.
        confidence: Statistical confidence (support / total).
        risk_level: Risk classification of the action.
        stage: Booking lifecycle stage where this rule applies, derived
            from the dominant ``stage`` of the supporting cases.
            ``None`` when the evidence spans multiple stages without a
            strict majority — the rule applies cross-stage and a top-
            level stage tag would be misleading.  ``stage`` is observed
            metadata, not part of the rule identity tuple: it stays out
            of :meth:`deterministic_id` so re-mining cannot orphan
            existing rows when the dominant stage shifts.
        execution_mode: How the rule should be executed (auto / ask / …).
        valid_from: Earliest date this rule is considered active —
            *application-time* lower bound (set by the miner from
            the earliest supporting case).
        valid_to: Scheduled expiration date — application-time
            upper bound for rules with intrinsic shelf-life
            (e.g. seasonal policies).  ``None`` means indefinite.
        invalid_at: *Application-time* moment when the world made
            this rule wrong — typically populated by
            ``_resolve_pattern_rule_contradictions`` to the
            ``valid_from`` of a newer, contradicting rule.  This
            answers "when did the PM's behaviour shift".  Distinct
            from ``valid_to`` (which is a scheduled end, not a
            supplanting event).
        deactivated_at: *Transaction-time* moment when Brain Engine
            *learned* the rule was wrong (i.e. when the contradicting
            rule was mined and the invalidation persisted).  Diverges
            from ``invalid_at`` whenever evidence arrives late
            (out-of-order ingestion, historical re-bootstrap).  This
            answers "when did the system know".  ``None`` while the
            rule is still active in the registry.  This pair
            (``invalid_at`` / ``deactivated_at``) implements the
            bi-temporal soft-invalidate pattern from Zep / Graphiti
            (arXiv 2501.13956 §3.2) — adapted to a structured
            identity tuple so no LLM is required for conflict
            detection.
        last_seen_at: When the most recent supporting case occurred.
        source_case_ids: IDs of DecisionCases that contributed to this rule.
        created_at: When the rule was first extracted.
        active: Whether the rule is currently active.  Kept as a
            denormalised flag for fast index scans; equivalent to
            ``deactivated_at IS NULL``.
    """

    scenario: str
    scope: PatternScope
    scope_id: str
    conditions: dict[str, Any]
    action: DecisionAction
    pattern_id: str = field(default_factory=_new_id)
    blocker_types: tuple[str, ...] = ()
    support_count: int = 0
    counterexample_count: int = 0
    confidence: float = 0.0
    risk_level: RiskLevel = RiskLevel.MEDIUM
    stage: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.ASK
    valid_from: datetime = field(default_factory=_utc_now)
    valid_to: datetime | None = None
    invalid_at: datetime | None = None
    deactivated_at: datetime | None = None
    last_seen_at: datetime = field(default_factory=_utc_now)
    source_case_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=_utc_now)
    active: bool = True
    rationale: str = ""
    # FL-03 — Foundation Layer bridge.  Optional reference to the
    # ``foundation_scenarios_reactive`` catalog row that the rule's
    # supporting decision cases were classified against.  ``None``
    # for rules mined before the foundation matcher landed and for
    # rules whose source cases disagreed on the foundation
    # scenario (cross-scenario rules — these will remain rare; the
    # miner picks the dominant id when one exists).  Plain TEXT
    # FK, not a typed enum, so the 469 catalog entries do not have
    # to be materialised inside the ``Scenario`` enum.  Used by
    # FL-12 to render the rule-origin trail and by FL-13 to fan
    # out PM overrides into ``FoundationUpdateCandidate`` records.
    foundation_scenario_id: str | None = None
    # FL-12 — Full provenance trail.  Lists every foundation
    # scenario, raw event, and proactive signal that contributed
    # to the mining of this rule.  Plural where ``foundation_
    # scenario_id`` is singular — cross-scenario rules surface
    # multiple ids here, the singular field records the dominant
    # one.  Powers the ``/patterns/rules/{rule_id}/origin`` API
    # endpoint that closes Ali's Turkish requirement #1.
    origin: PatternOrigin = field(default_factory=PatternOrigin)

    def __repr__(self) -> str:
        return (
            f"PatternRule(id={self.pattern_id[:8]}…, "
            f"scenario={self.scenario}, "
            f"scope={self.scope.value}:{self.scope_id}, "
            f"confidence={self.confidence:.2f}, "
            f"mode={self.execution_mode.value})"
        )

    @staticmethod
    def deterministic_id(
        *,
        scenario: str,
        scope: PatternScope,
        scope_id: str,
        action_type: DecisionType,
        conditions: dict[str, Any],
    ) -> str:
        """Stable identifier derived from rule identity tuple.

        Repeated bootstraps over the same data must converge on the
        same ``pattern_id`` so the postgres UPSERT can update one
        row instead of producing N orphans.  The hash inputs cover
        the full rule identity: scenario, scope, scope_id,
        action_type, and the sorted-key JSON of conditions.
        Confidence / support / counterexample / source_case_ids do
        *not* enter the hash — they are observed values and would
        re-key the rule on every fresh observation.
        """
        import hashlib
        import json

        payload = json.dumps(
            {
                "s": scenario,
                "sc": scope.value,
                "sid": scope_id,
                "a": action_type.value,
                "c": conditions,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return digest[:32]

    @property
    def total_cases(self) -> int:
        """Total number of cases (support + counterexamples)."""
        return self.support_count + self.counterexample_count

    @property
    def counterexample_ratio(self) -> float:
        """Fraction of cases that contradict this rule."""
        if self.total_cases == 0:
            return 0.0
        return self.counterexample_count / self.total_cases

    @property
    def is_promotable(self) -> bool:
        """Whether this rule meets the criteria for AUTO execution.

        Requires: (a) enough support, (b) high confidence, (c) low
        counterexample ratio, (d) non-critical risk.
        """
        return (
            self.support_count >= MIN_SUPPORT_AUTO
            and self.confidence >= CONFIDENCE_AUTO_THRESHOLD
            and self.counterexample_ratio <= MAX_COUNTEREXAMPLE_RATIO
            and self.risk_level != RiskLevel.CRITICAL
        )

    @property
    def is_expired(self) -> bool:
        """Whether the rule has passed its valid_to date."""
        if self.valid_to is None:
            return False
        return _utc_now() > self.valid_to

    def matches_conditions(self, features: dict[str, Any]) -> bool:
        """Evaluate whether booking features satisfy all rule conditions.

        Each condition is a dict with ``operator`` and ``value`` keys.
        Supported operators: gt, gte, lt, lte, eq, neq, in, not_in,
        contains.

        Args:
            features: Flat dict of booking feature values to test.

        Returns:
            True if every condition is satisfied, False otherwise.
        """
        for field_name, condition in self.conditions.items():
            actual = features.get(field_name)
            if actual is None:
                return False
            if not _evaluate_condition(actual, condition):
                return False
        return True


# ---------------------------------------------------------------------------
# Condition evaluation helpers
# ---------------------------------------------------------------------------


def _evaluate_condition(actual: Any, condition: dict[str, Any]) -> bool:
    """Evaluate a single condition against an actual value.

    Args:
        actual: The real value from booking features.
        condition: Dict with ``operator`` and ``value`` keys.

    Returns:
        True if the condition is satisfied.
    """
    operator = condition.get("operator", "eq")
    expected = condition.get("value")

    if operator == "gt":
        return actual > expected  # type: ignore[operator]
    if operator == "gte":
        return actual >= expected  # type: ignore[operator]
    if operator == "lt":
        return actual < expected  # type: ignore[operator]
    if operator == "lte":
        return actual <= expected  # type: ignore[operator]
    if operator == "eq":
        return actual == expected
    if operator == "neq":
        return actual != expected
    if operator == "in":
        return actual in expected  # type: ignore[operator]
    if operator == "not_in":
        return actual not in expected  # type: ignore[operator]
    if operator == "contains":
        return expected in actual  # type: ignore[operator]
    return False
