"""Data models for the Foundation Analysis Orchestrator (FL-16).

The orchestrator (see :mod:`brain_engine.analysis.orchestrator`)
turns an upstream operational event into an
:class:`AnalysisResult` that downstream code can attach to a
:class:`brain_engine.patterns.models.DecisionCase` or pattern rule.
This module owns the value objects on the orchestrator's contract:

* :class:`AnalysisEvent` — typed input.  Carries every event-type
  the upcoming orchestrator stubs may receive.
* :class:`AnalysisEventType` — closed enumeration of event sources.
  Kept as a :class:`~enum.StrEnum` so the JSONB payload that
  travels through the system stays human-readable.
* :class:`FoundationMatchCandidate` — one ranked foundation row
  the matcher returned (slug + similarity + optional enrichment
  from the :class:`FoundationCatalogStore`).
* :class:`FoundationMatch` — top-K candidates + the dominant slug
  + the catalog entry for the dominant slug (when available).
  Empty when the matcher is not wired or when the event carries
  no classifiable text.
* :class:`AnalysisResult` — the orchestrator's final output.
  Re-uses :class:`PatternOrigin` from
  :mod:`brain_engine.patterns.models` for the provenance trail so
  the API endpoint shipped in FL-12 can render the trail without
  a translation layer.

All value objects are frozen + ``slots=True`` to match the
project's existing dataclass conventions.  Empty defaults are
preferred over optional ``None`` for collection-typed fields so
the caller can iterate the result without a null check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from brain_engine.patterns.foundation_registry import FoundationScenario
from brain_engine.patterns.models import PatternOrigin

__all__ = [
    "AnalysisEvent",
    "AnalysisEventType",
    "AnalysisResult",
    "FoundationMatch",
    "FoundationMatchCandidate",
    "MemoryTier",
    "memory_type_label_to_tier",
]


class MemoryTier(StrEnum):
    """Memory tier slugs the orchestrator can route a case into (FL-04).

    Mirrors the thirteen ``### Memory Type`` labels observed in the
    reactive foundation catalog (verified against
    ``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_
    Foundation.md`` via the FL-01 grep — Reservation context +
    Guest profile each appear 252 times, Property knowledge 134
    times, ... Guest risk 11 times).  Kept as snake_case slugs
    so the value can
    travel through JSONB origin payloads, log lines, and the
    ``AnalysisResult.memory_routes`` tuple without escaping.

    Sprint 3 ships the *routing decision*: the orchestrator emits
    a tuple of these slugs based on the foundation entry the
    matcher selected.  Wiring each slug to a concrete memory store
    (e.g. ``PROPERTY_KNOWLEDGE`` → :class:`SemanticMemory` /
    Property Brain, ``PM_PREFERENCE_MEMORY`` →
    :class:`ProceduralMemory`) is a follow-up Sprint 4-5 PR that
    extends :class:`brain_engine.memory.fanout.MemoryFanOut` with a
    tier-aware ``record_case`` variant.  Until then the
    :class:`AnalysisResult.memory_routes` field is observation-only
    — it surfaces *what should be written* without performing the
    write.
    """

    PROPERTY_KNOWLEDGE = "property_knowledge"
    PM_PREFERENCE_MEMORY = "pm_preference_memory"
    PM_BEHAVIOR_MEMORY = "pm_behavior_memory"
    RESERVATION_CONTEXT_MEMORY = "reservation_context_memory"
    GUEST_PROFILE_MEMORY = "guest_profile_memory"
    GUEST_RISK_MEMORY = "guest_risk_memory"
    OWNER_PREFERENCE_MEMORY = "owner_preference_memory"
    VENDOR_MEMORY = "vendor_memory"
    TASK_WORKFLOW_MEMORY = "task_workflow_memory"
    OPERATIONAL_WORKFLOW_MEMORY = "operational_workflow_memory"
    CHANNEL_SPECIFIC_BEHAVIOR_MEMORY = "channel_specific_behavior_memory"
    MISSING_INFO_REGISTRY = "missing_info_registry"
    SOP_CANDIDATE_MEMORY = "sop_candidate_memory"


# Verbatim catalog label → MemoryTier mapping.  Labels are matched
# case-insensitively after stripping leading / trailing whitespace
# so a catalog edit that changes capitalisation does not break the
# router.  Unknown labels collapse to ``None`` and the orchestrator
# logs a warning + skips them — better than smuggling a typo into
# the routes tuple.
_LABEL_TO_TIER: Final[dict[str, MemoryTier]] = {
    "property knowledge": MemoryTier.PROPERTY_KNOWLEDGE,
    "pm preference memory": MemoryTier.PM_PREFERENCE_MEMORY,
    "pm behavior memory": MemoryTier.PM_BEHAVIOR_MEMORY,
    "reservation context memory": MemoryTier.RESERVATION_CONTEXT_MEMORY,
    "guest profile memory": MemoryTier.GUEST_PROFILE_MEMORY,
    "guest risk memory": MemoryTier.GUEST_RISK_MEMORY,
    "owner preference memory": MemoryTier.OWNER_PREFERENCE_MEMORY,
    "vendor memory": MemoryTier.VENDOR_MEMORY,
    "task workflow memory": MemoryTier.TASK_WORKFLOW_MEMORY,
    "operational workflow memory": (MemoryTier.OPERATIONAL_WORKFLOW_MEMORY),
    "channel-specific behavior memory": (
        MemoryTier.CHANNEL_SPECIFIC_BEHAVIOR_MEMORY
    ),
    "missing-info registry": MemoryTier.MISSING_INFO_REGISTRY,
    "sop candidate memory": MemoryTier.SOP_CANDIDATE_MEMORY,
}


def memory_type_label_to_tier(label: str) -> MemoryTier | None:
    """Map a foundation ``Memory Type`` label to a :class:`MemoryTier`.

    Returns ``None`` for unknown / blank labels so the orchestrator
    can drop them from the routes tuple without raising.  Lookups
    are case-insensitive and whitespace-tolerant so a stray space
    in the markdown does not collapse a valid label into ``None``.
    """
    if not label:
        return None
    return _LABEL_TO_TIER.get(label.strip().lower())


class AnalysisEventType(StrEnum):
    """Closed set of upstream event sources the orchestrator handles.

    The seven entries below cover every upstream signal listed in
    §2 of the reactive foundation document (Brain Engine
    Interpretation Model) plus the three event sources the proactive
    foundation §1 expects.  Adding a new source requires extending
    this enum and the corresponding handler in the orchestrator —
    callers cannot smuggle in an arbitrary string.
    """

    MESSAGE = "message"
    RESERVATION_CHANGE = "reservation_change"
    PMS_EVENT = "pms_event"
    VENDOR_UPDATE = "vendor_update"
    REVIEW_RECEIVED = "review_received"
    TASK_UPDATE = "task_update"
    PAYMENT_EVENT = "payment_event"


@dataclass(frozen=True, slots=True)
class AnalysisEvent:
    """One operational event ready to enter the Foundation pipeline.

    The orchestrator only inspects ``text`` for the foundation
    matcher; everything else is passed through to downstream pipeline
    steps (the FL-04 router reads ``payload`` for property metadata,
    the FL-05 guardrail reads ``event_type`` for risk class lookup).
    Keeping ``payload`` open-typed avoids a separate variant per
    event source — the orchestrator's contract stays one method.

    Attributes:
        event_id: Globally unique identifier — flows through to
            :pyattr:`PatternOrigin.source_event_ids` so the trail
            survives across the pipeline.
        event_type: Closed enum (:class:`AnalysisEventType`) telling
            the orchestrator which upstream produced the event.
        property_id: Property identifier from PMS / OTA.  Empty
            string when the event has no property context yet (rare;
            mostly internal ops events).
        occurred_at: UTC timestamp the event was produced.
        text: Free-form text the foundation matcher should embed.
            For ``MESSAGE`` events this is the guest message body;
            for system events it is the synthesised description
            (e.g. ``"PMS sync: reservation 123 dates moved"``).
        payload: Structured event-specific data.  The orchestrator
            does not inspect it — downstream steps do.
        reservation_id: Optional reservation linked to the event.
        guest_id: Optional guest linked to the event.
        owner_id: Optional owner linked to the event.
        pms_snapshot: PMS reservation / listing facts at the time
            the event fired.  Mirrors the same-named dict carried
            on :class:`brain_engine.patterns.models.DecisionCase`
            so the FL-16 orchestrator's Q5-B
            ``_validate_required_data`` step can verify that the
            data the foundation scenario said was required
            actually reached the pipeline.  Empty dict (the
            default) skips the check — backwards-compatible with
            pre-Q5-B call sites that did not populate snapshots.
        calendar_snapshot: Availability / arrival-time / lead-time
            facts at event time.  Same provenance + role as
            :pyattr:`pms_snapshot`.
        ops_snapshot: Cleaning / vendor / access-code operational
            state at event time.  Same provenance + role.
        guest_snapshot: Guest profile / history / verification
            facts at event time.  Same provenance + role.

    Raises:
        ValueError: When ``event_id`` is empty.
    """

    event_id: str
    event_type: AnalysisEventType
    property_id: str
    occurred_at: datetime
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    reservation_id: str | None = None
    guest_id: str | None = None
    owner_id: str | None = None
    pms_snapshot: dict[str, Any] = field(default_factory=dict)
    calendar_snapshot: dict[str, Any] = field(default_factory=dict)
    ops_snapshot: dict[str, Any] = field(default_factory=dict)
    guest_snapshot: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id required")


@dataclass(frozen=True, slots=True)
class FoundationMatchCandidate:
    """One ranked foundation row produced by the matcher step.

    Mirrors :class:`brain_engine.patterns.scenario_matcher.
    ScenarioCandidate` but adds the optional catalog enrichment so
    downstream steps do not need to call the catalog store again.

    Attributes:
        scenario_id: Foundation slug from
            :data:`FoundationScenario.scenario_id`.
        similarity: Cosine similarity in ``[-1.0, 1.0]`` from the
            embedding matcher.
        catalog_entry: Full :class:`FoundationScenario` when the
            :class:`FoundationCatalogStore` was wired and the slug
            was present; ``None`` when the catalog store is absent
            or the slug has drifted out of the catalog.
    """

    scenario_id: str
    similarity: float
    catalog_entry: FoundationScenario | None = None


@dataclass(frozen=True, slots=True)
class FoundationMatch:
    """Output of the orchestrator's ``match_foundation`` pipeline step.

    Empty when the matcher is not wired, when the event carries no
    text, or when the event text yields no candidates above the
    matcher's noise floor.  The :pyattr:`is_empty` helper lets
    downstream steps short-circuit instead of branching on
    ``len(candidates) == 0``.

    Attributes:
        candidates: Top-K candidates in descending similarity
            order.  Empty tuple when nothing matched.
        dominant_scenario_id: Convenience alias for
            ``candidates[0].scenario_id`` when present; ``None`` for
            empty matches.  Lets the caller pick the dominant slug
            without indexing the tuple.
        dominant_catalog_entry: The full catalog entry for the
            dominant slug when available — the FL-04 / FL-05 steps
            read ``memory_type`` / ``should_auto_reply`` directly
            from this.
    """

    candidates: tuple[FoundationMatchCandidate, ...] = ()
    dominant_scenario_id: str | None = None
    dominant_catalog_entry: FoundationScenario | None = None

    @property
    def is_empty(self) -> bool:
        """Whether the matcher produced no usable candidates."""
        return not self.candidates


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """The orchestrator's final pipeline output.

    The caller attaches :pyattr:`origin` to a freshly-built
    :class:`brain_engine.patterns.models.DecisionCase` or
    :class:`PatternRule` so the FL-12 ``/rules/{id}/origin``
    endpoint can surface the provenance trail.  The other fields
    are stubs that future sprints populate:

    * :pyattr:`guardrail_block` — FL-05 sets to ``True`` when the
      foundation says ``Should AI Auto-Reply: No``.
    * :pyattr:`pattern_candidate_emitted` — FL-05 sets when the
      pattern miner should skip the case
      (``Should AI Learn Pattern: No``).
    * :pyattr:`memory_routes` — FL-04 lists the memory tiers the
      :class:`MemoryFanOut` should write to, derived from the
      foundation's ``memory_type`` field.

    Attributes:
        event_id: Echoed from the input event for trace correlation.
        foundation_match: The match step's output — always present,
            potentially empty.
        origin: :class:`PatternOrigin` ready to be persisted on a
            :class:`DecisionCase` / :class:`PatternRule`.
        guardrail_block: ``True`` when a guardrail step blocks the
            event from auto-handling.  Always ``False`` until FL-05.
        pattern_candidate_emitted: ``True`` when the pattern miner
            should treat this event as a learning candidate.
            Always ``False`` until FL-05.
        memory_routes: Tuple of memory-tier names the case should
            be fanned out to.  Empty until FL-04.
        missing_required_data: Verbatim catalog labels from
            ``dominant_catalog_entry.required_data_checks`` whose
            target snapshot was empty on the event when the
            FL-16 Q5-B step ran.  Empty tuple when the catalog
            entry had no checks, when every mappable check was
            satisfied, or when the dominant entry was cleared
            (Q5-A similarity gate trip / unwired catalog).  The
            orchestrator's ``_mine_if_learnable`` step treats a
            non-empty value as a reason to skip mining — a
            scenario whose required data is physically missing
            from the event should not seed pattern rules.
        stage_mismatch: ``True`` when the FL-16 Q5-C step
            detected a contradiction between the booking stage
            implied by the event's calendar
            (``check_in``/``check_out``/``current_time`` on the
            calendar_snapshot) and the booking stage the matched
            scenario expects.  ``False`` when no contradiction
            was found, when calendar data was missing, when the
            scenario is stage-agnostic, or when Q5-A cleared the
            dominant entry.  **Observation only** in Variant A —
            the orchestrator does NOT gate any downstream step
            on this flag; a Q5-C Variant B PR may consume it.
        stage_mismatch_detail: Human-readable explanation when
            ``stage_mismatch`` is ``True``, in the stable format
            ``"calendar=<stage> scenario=<stage>"``.  Empty
            string otherwise.  Suited for log lines and
            adversarial-test response inspection.
    """

    event_id: str
    foundation_match: FoundationMatch
    origin: PatternOrigin
    guardrail_block: bool = False
    pattern_candidate_emitted: bool = False
    memory_routes: tuple[str, ...] = ()
    missing_required_data: tuple[str, ...] = ()
    stage_mismatch: bool = False
    stage_mismatch_detail: str = ""
