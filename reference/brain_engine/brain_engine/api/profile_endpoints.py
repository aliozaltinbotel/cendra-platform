"""Property-profile (knowledge) and sandbox HTTP surface.

Exposes two endpoints consumed by the V2 onboarding UI:

1. ``GET /api/v1/properties/{property_id}/knowledge`` — returns the
   aggregate :class:`PropertyProfile` Cendra builds during onboarding
   step 5 ("property-picked").  The payload answers the UI question
   *"what does Brain know about this place?"* without the caller
   having to fan out over every unified-data query.
2. ``GET /api/v1/interview/sandbox/{property_id}`` — returns up to
   three example questions the engine would ask next.  Onboarding
   step 13 uses this to let the PM play with the interview surface
   before the real loop begins.

The router uses the same lifespan-injected dependency dict pattern
as the interview / card / memory routers so ``server.py`` can wire
everything at startup without circular imports.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.cards.context_tags import ContextTag
from brain_engine.cards.models import (
    DecisionCard,
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
    ReversibilityTier,
)
from brain_engine.cards.store import CardStore
from brain_engine.integrations.unified_data.readers import (
    CalendarDay,
    OccupancyOption,
    PropertySummary,
    RatePlanWithCalendar,
    UnifiedDataReaderError,
    UnifiedPropertyReader,
    UnifiedRatePlanReader,
)
from brain_engine.interview import (
    BookingStage,
    InterviewEngine,
    InterviewQuestion,
)
from brain_engine.patterns.classifier import classify_stage_by_window
from brain_engine.patterns.refusal_extractor import (
    RefusalExtractor,
    RefusalSignal,
)
from brain_engine.profiles.models import (
    KnowledgeSection,
    PropertyProfile,
    ReviewAggregate,
)
from brain_engine.profiles.store import PropertyProfileStore
from brain_engine.sandbox.generator import ExampleReplyGenerator
from brain_engine.sandbox.models import UnansweredThread
from brain_engine.sandbox.readiness import (
    SandboxReadiness,
    SandboxReadinessService,
)
from brain_engine.sandbox.store import UnansweredThreadStore

__all__ = [
    "configure_profile_deps",
    "router",
]


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["Profiles"])


# Shared deps — injected from server.py at lifespan start.
_deps: dict[str, Any] = {}


def configure_profile_deps(deps: dict[str, Any]) -> None:
    """Inject shared dependencies.

    Args:
        deps: Dependency dict from server startup.  Must contain
            ``"property_profile_store"`` and ``"interview_engine"``.
    """
    _deps.update(deps)


def _profile_store() -> PropertyProfileStore:
    """Return the configured :class:`PropertyProfileStore` or 503."""
    store = _deps.get("property_profile_store")
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="PropertyProfileStore not configured",
        )
    return store


def _interview_engine() -> InterviewEngine:
    """Return the configured :class:`InterviewEngine` or 503."""
    engine = _deps.get("interview_engine")
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="InterviewEngine not configured",
        )
    return engine


def _sandbox_store() -> UnansweredThreadStore:
    """Return the configured :class:`UnansweredThreadStore` or 503."""
    store = _deps.get("unanswered_thread_store")
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="UnansweredThreadStore not configured",
        )
    return store


def _property_reader() -> UnifiedPropertyReader:
    """Return the configured :class:`UnifiedPropertyReader` or 503."""
    reader = _deps.get("property_reader")
    if reader is None:
        raise HTTPException(
            status_code=503,
            detail="UnifiedPropertyReader not configured",
        )
    return reader


def _rate_plan_reader() -> UnifiedRatePlanReader:
    """Return the configured :class:`UnifiedRatePlanReader` or 503."""
    reader = _deps.get("rate_plan_reader")
    if reader is None:
        raise HTTPException(
            status_code=503,
            detail="UnifiedRatePlanReader not configured",
        )
    return reader


def _decision_case_store_optional() -> Any:
    """Return the configured ``DecisionCaseStore`` or ``None``.

    Mümin 2026-05-13 (PR #E): ``/memory/timeline`` projects the
    bootstrap-loaded ``DecisionCase`` archive as timeline episodes
    alongside the PM-correction stream.  Returning ``None`` is the
    contract for "case store unavailable" and degrades the timeline
    to PM-only without raising.
    """
    return _deps.get("decision_case_store")


def _pm_fact_store_optional() -> Any:
    """Return the configured PM fact store or ``None``.

    The fact store is the canonical memory backing for the
    ``/properties/{id}/memory``, ``/memory/timeline`` and
    ``/memory/audit/sample`` endpoints.  Keeping the lookup
    tolerant means a misconfigured deployment still returns
    "no facts" cleanly rather than 500-ing when the V2 panel
    polls it.
    """
    return _deps.get("pm_fact_store")


def _card_store() -> CardStore:
    """Return the configured :class:`CardStore` or 503."""
    store = _deps.get("card_store")
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="CardStore not configured",
        )
    return store


def _readiness_service() -> SandboxReadinessService:
    """Return the configured :class:`SandboxReadinessService` or 503."""
    service = _deps.get("sandbox_readiness_service")
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="SandboxReadinessService not configured",
        )
    return service


def _sandbox_generator() -> ExampleReplyGenerator:
    """Return the configured :class:`ExampleReplyGenerator` or 503.

    The sandbox preview-reply endpoint asks the generator for a
    candidate response inline; if the lifespan wiring never
    published one (e.g. a stripped-down test rig), we surface a
    503 instead of silently returning an empty reply.
    """
    generator = _deps.get("sandbox_generator")
    if generator is None:
        raise HTTPException(
            status_code=503,
            detail="Sandbox example reply generator not configured",
        )
    return generator


# ── Response models ──────────────────────────────────────────── #


class PropertySummaryResponse(BaseModel):
    """Wire form of one :class:`PropertySummary` row.

    Slim subset designed for the property-picker UI rendered in
    onboarding step 2.  The picker only needs enough information to
    identify the property; the deep knowledge payload is served by the
    per-property knowledge endpoint once the PM has made a choice.
    """

    channel_entity_id: str
    pms_id: str
    title: str
    is_active: bool
    city: str
    country: str
    property_type: str
    max_occupancy: int
    bedrooms: int
    bathrooms: float
    base_price: float
    base_currency: str
    listing_id: str


class PropertiesResponse(BaseModel):
    """Top-level payload for the property-picker listing."""

    total: int
    limit: int
    skip: int
    properties: tuple[PropertySummaryResponse, ...]


class CalendarDayResponse(BaseModel):
    """Wire form of one :class:`CalendarDay` row."""

    date: str
    note: str
    stop_sell: bool
    count_available_units: int
    price: float


class OccupancyOptionResponse(BaseModel):
    """Wire form of one :class:`OccupancyOption` row."""

    occupancy: int
    is_primary: bool
    rate: float


class RatePlanWithCalendarResponse(BaseModel):
    """Wire form of one enriched :class:`RatePlanWithCalendar`."""

    rate_plan_id: str
    channel_entity_id: str
    property_channel_id: str
    property_pms_id: str
    name: str
    title: str
    currency: str
    rate_mode: str
    is_active: bool
    calendar: tuple[CalendarDayResponse, ...]
    occupancy_options: tuple[OccupancyOptionResponse, ...]


class RatePlansResponse(BaseModel):
    """Top-level payload for the rate-plans + calendar endpoint."""

    property_id: str
    date_from: str
    date_to: str
    total: int
    rate_plans: tuple[RatePlanWithCalendarResponse, ...]


class KnowledgeSectionResponse(BaseModel):
    """Wire form of :class:`KnowledgeSection`."""

    name: str
    item_count: int
    last_ingested_at: str | None
    notes: str = ""


class ReviewAggregateResponse(BaseModel):
    """Wire form of :class:`ReviewAggregate`."""

    total: int
    with_rating: int
    average_rating: float | None
    latest_review_at: str | None


class PropertyKnowledgeResponse(BaseModel):
    """Wire form of :class:`PropertyProfile`.

    Returns the static profile snapshot the harvester built during
    onboarding step 5.  The UI uses ``coverage_ratio`` /
    ``knowledge_percentage`` / ``sections`` to render the
    "what does Brain know?" answer.
    """

    property_channel_id: str
    pms_id: str
    customer_id: str
    org_id: str
    provider_type: str
    title: str
    is_active: bool
    city: str
    country: str
    property_type: str
    max_occupancy: int
    bedrooms: int
    bathrooms: float
    base_currency: str
    base_price: float
    knowledge_percentage: float
    amenity_codes: tuple[str, ...]
    image_count: int
    room_count: int
    description_languages: tuple[str, ...]
    coverage_ratio: float
    sections: tuple[KnowledgeSectionResponse, ...]
    review_aggregate: ReviewAggregateResponse
    static_payload: dict[str, Any] = Field(default_factory=dict)
    built_at: str


class UnansweredThreadResponse(BaseModel):
    """Wire form of one :class:`UnansweredThread`.

    Attributes:
        needs_review_reason: Comma-separated rule names from the
            sandbox heuristic check (e.g.
            ``"contains_time,contains_secret"``).  Empty when the
            reply contained no suspicious-fact pattern; the UI
            should colour rows with a non-empty value to mark
            them for the PM's attention first.
    """

    conversation_id: str
    property_id: str
    last_guest_message: str
    last_guest_sent_at: str
    example_reply: str
    generated_by: str
    language: str = ""
    generated_at: str
    needs_review_reason: str = ""


class UnansweredThreadsResponse(BaseModel):
    """Top-level payload for the unanswered-thread sandbox list."""

    property_id: str
    total: int
    threads: tuple[UnansweredThreadResponse, ...]


class SandboxQuestionResponse(BaseModel):
    """Wire form of one sandbox preview question."""

    qid: str
    stage: BookingStage
    topic: str
    prompt_text: str
    priority: str
    depends_on_qids: tuple[str, ...]
    triggered_by_events: tuple[str, ...]


class SandboxResponse(BaseModel):
    """Top-level sandbox payload."""

    property_id: str
    max_count: int
    questions: tuple[SandboxQuestionResponse, ...]


class SandboxAnswerRequest(BaseModel):
    """Wire form of one PM-supplied answer to an interview question.

    Shape mirrors Mümin's onboarding step 13 ("3-question sandbox").
    The PM replies to one prompt at a time; the engine stores the
    answer in the card store and the interview engine table.

    ``customer_id`` and ``conversation_id`` are optional cascade
    pointers retained for forward compatibility with PM-fact-store
    scoping; they are accepted but not currently used by the
    sandbox handler.
    """

    qid: str
    answer: str
    language: str = ""
    customer_id: str = ""
    conversation_id: str = ""


class SandboxAnswerResponse(BaseModel):
    """Outcome of a successful sandbox-answer ingestion."""

    property_id: str
    qid: str
    card_id: str


class PreviewReplyRequest(BaseModel):
    """Inputs for the time-aware sandbox preview-reply endpoint.

    The PM picks a hypothetical guest message + a stay window +
    "message sent at" timestamp; the engine runs the same
    date-aware classifier the live past-conversation pipeline
    uses, so the predicted stage agrees with what would later be
    persisted as the pattern bucket for that question.

    Attributes:
        guest_message: Text the guest would send (any language).
        language: Optional ISO language hint forwarded to the
            generator and the refusal extractor.  Empty defers
            to the generator's own detection.
        message_sent_at: ISO-8601 timestamp at which the guest
            message is "sent" — drives the proximity-to-arrival
            window math (4 h CHECKIN, 24 h PRE_ARRIVAL, 4 h
            CHECKOUT).
        arrival_date: Reservation check-in (ISO-8601, date or
            full timestamp).
        departure_date: Reservation check-out (ISO-8601).
    """

    model_config = ConfigDict(extra="forbid")

    guest_message: str
    language: str = ""
    message_sent_at: str
    arrival_date: str
    departure_date: str


class RefusalSignalResponse(BaseModel):
    """Wire form of one :class:`RefusalSignal`.

    Attributes:
        refusal_type: Semantic class (e.g. ``requires_document``).
        language: Detected language of the trigger phrase.
        trigger_phrase: Substring that fired the rule.
        conditional_clause: Bounding sub-phrase (``until / unless
            / sin / hasta que / olmadan / без``-style); empty for
            unconditional refusals.
        confidence: Deterministic ``[0, 1]`` score.
    """

    refusal_type: str
    language: str
    trigger_phrase: str
    conditional_clause: str
    confidence: float


class PreviewReplyResponse(BaseModel):
    """Top-level payload for the preview-reply endpoint.

    Attributes:
        property_id: Echo of the path parameter.
        predicted_stage: :class:`BookingStage` value chosen by
            :func:`classify_stage_by_window`, or empty when any of
            the timestamps fail to parse.
        example_reply: Candidate reply produced by the configured
            sandbox generator.
        generated_by: Generator backend identifier (template /
            llm:azure_openai / …) for traceability.
        language: Echo of the requested language hint.
        refusal_signals: Refusal patterns detected inside the
            example reply (used by the UI to flag guardrails).
        guardrail_summary: Comma-separated sorted refusal types,
            empty when the reply contained no refusal pattern.
    """

    property_id: str
    predicted_stage: str
    example_reply: str
    generated_by: str
    language: str
    refusal_signals: tuple[RefusalSignalResponse, ...]
    guardrail_summary: str


class MemoryFactResponse(BaseModel):
    """One PM-confirmed fact surfaced for memory recall.

    ``valid_at`` is the ISO-8601 timestamp when the fact landed in
    the PM fact store — surfaced so the V2 panel can show *when*
    Brain learned each line and so callers can sort newest-first
    without a follow-up query (Risk 8).
    """

    text: str
    valid_at: str = ""


class MemoryRecallResponse(BaseModel):
    """Top-level payload for the memory-recall endpoint."""

    property_id: str
    query: str
    facts: tuple[MemoryFactResponse, ...]
    scopes: tuple[str, ...] = ()


class TimelineEpisodeResponse(BaseModel):
    """One node on the episode timeline."""

    name: str
    source: str
    content: str
    valid_at: str


class TimelineResponse(BaseModel):
    """Episode timeline for a cascade scope."""

    scope: str
    time_from: str
    time_to: str
    episodes: tuple[TimelineEpisodeResponse, ...]


class MemoryAuditEpisodeResponse(BaseModel):
    """One Episodic node surfaced for spot-audit.

    Same shape as :class:`TimelineEpisodeResponse` but kept distinct
    so the audit endpoint can evolve its payload (rationale, source
    confidence, …) without touching the timeline contract.
    """

    name: str
    source: str
    content: str
    valid_at: str


class MemoryAuditSampleResponse(BaseModel):
    """Random sample of Episodic facts for the V2 audit panel.

    The audit endpoint addresses Risk 4 (LLM extraction errors): it
    surfaces *what* the engine has actually persisted so an operator
    can spot hallucinated facts before they shape decisions.  The
    ``scope`` field echoes the cascade scope that produced the
    sample, and ``sample_size`` is the requested cap (the response
    may be smaller when the graph holds fewer episodes).
    """

    scope: str
    sample_size: int
    episodes: tuple[MemoryAuditEpisodeResponse, ...]


# ── Endpoints ─────────────────────────────────────────────────── #


_PROPERTIES_DEFAULT_LIMIT: int = 50
_PROPERTIES_MAX_LIMIT: int = 200

# Timeline endpoint bounds.  These live at module scope because the
# memory_timeline() signature uses them as Query() defaults — default
# values are evaluated at def-time, so the constants must be defined
# before the handler is declared.
_TIMELINE_DEFAULT_TOP_K: Final[int] = 50
_TIMELINE_MAX_TOP_K: Final[int] = 500

# Audit-sample bounds.  Same def-time-default consideration as the
# timeline constants above — these must be hoisted before the handler.
# 20 is enough variety to spot extraction errors at a glance; 200 is
# the hard ceiling the V2 panel paginates against.
_AUDIT_SAMPLE_DEFAULT_SIZE: Final[int] = 20
_AUDIT_SAMPLE_MAX_SIZE: Final[int] = 200

# Rate-plans calendar window bounds.  The defaults cover the UI's
# 30-day price/availability card; the hard cap protects the
# onboarding-api GraphQL endpoint from pathological multi-year
# requests (confirmed with Mümin 2026-04-24).
_RATE_PLANS_DEFAULT_WINDOW_DAYS: Final[int] = 30
_RATE_PLANS_MAX_WINDOW_DAYS: Final[int] = 180
_ISO_DATE_FORMAT: Final[str] = "%Y-%m-%d"

@router.get(
    "/properties",
    response_model=PropertiesResponse,
)
async def list_properties(
    limit: int = _PROPERTIES_DEFAULT_LIMIT,
    skip: int = 0,
) -> PropertiesResponse:
    """Return one page of property summaries for the picker UI.

    Backs onboarding step 2 of Mümin's flow: once the PM has supplied
    PMS credentials, the UI paints the property list and lets the user
    pick the single home to bootstrap.  The endpoint proxies
    :meth:`UnifiedPropertyReader.list_summaries`, so scope
    (customer / org / provider) is fixed by the reader configured at
    lifespan start — we never let the caller broaden that scope.

    Args:
        limit: Page size clamped to ``[1, 200]``.
        skip: Row offset clamped to ``>= 0``.

    Raises:
        HTTPException: 502 when the upstream GraphQL call fails; the
            reader translates transport errors into
            :class:`UnifiedDataReaderError`.
    """
    reader = _property_reader()
    clamped_limit = _clamp_limit(limit)
    clamped_skip = max(0, int(skip))
    try:
        summaries = await reader.list_summaries(
            limit=clamped_limit,
            skip=clamped_skip,
        )
    except UnifiedDataReaderError as exc:
        logger.warning("profile.list_properties_failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Upstream property listing unavailable",
        ) from exc
    return PropertiesResponse(
        total=len(summaries),
        limit=clamped_limit,
        skip=clamped_skip,
        properties=tuple(_summary_to_response(row) for row in summaries),
    )


def _clamp_limit(value: int) -> int:
    """Clamp ``value`` into ``[1, _PROPERTIES_MAX_LIMIT]``."""
    if value < 1:
        return 1
    if value > _PROPERTIES_MAX_LIMIT:
        return _PROPERTIES_MAX_LIMIT
    return int(value)


@router.get(
    "/properties/{property_id}/rate-plans",
    response_model=RatePlansResponse,
)
async def list_rate_plans_with_calendar(
    property_id: str,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
) -> RatePlansResponse:
    """Return rate plans with calendar + occupancy options.

    Backs onboarding step 10 of Mümin's flow (``fiyat / müsaitlik``)
    and the post-onboarding property-detail UI that renders a 30-day
    price grid.  The endpoint forwards the caller's window to the
    GraphQL ``calendar(from: to:)`` resolver, or falls back to the
    default ``today..today+30d`` window when no bounds are supplied.

    Args:
        property_id: ``channelEntityId`` of the property.
        date_from: Optional ISO ``YYYY-MM-DD`` lower bound.  When
            omitted the server uses today's UTC date.
        date_to: Optional ISO ``YYYY-MM-DD`` upper bound.  When
            omitted the server uses ``date_from + 30 days``.

    Raises:
        HTTPException: 400 when dates are malformed or inverted;
            502 when the upstream GraphQL call fails.
    """
    reader = _rate_plan_reader()
    try:
        resolved_from, resolved_to = _resolve_rate_plan_window(
            date_from, date_to
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        enriched = await reader.list_with_calendar(
            property_channel_id=property_id,
            date_from=resolved_from,
            date_to=resolved_to,
        )
    except UnifiedDataReaderError as exc:
        logger.warning("profile.list_rate_plans_failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Upstream rate-plans listing unavailable",
        ) from exc
    return RatePlansResponse(
        property_id=property_id,
        date_from=resolved_from,
        date_to=resolved_to,
        total=len(enriched),
        rate_plans=tuple(_rate_plan_to_response(row) for row in enriched),
    )


def _resolve_rate_plan_window(
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, str]:
    """Resolve and validate the rate-plans window.

    Returns a pair of ISO-8601 ``YYYY-MM-DD`` strings.  The window is
    validated so the upper bound is strictly after the lower bound and
    the span never exceeds ``_RATE_PLANS_MAX_WINDOW_DAYS``.

    Raises:
        ValueError: On malformed dates, inverted bounds, or a span
            that exceeds the project-wide cap.
    """
    start = _parse_iso_date(date_from) or date.today()
    end = (
        _parse_iso_date(date_to)
        or start + timedelta(days=_RATE_PLANS_DEFAULT_WINDOW_DAYS)
    )
    if end <= start:
        raise ValueError("'to' must be strictly after 'from'")
    span_days = (end - start).days
    if span_days > _RATE_PLANS_MAX_WINDOW_DAYS:
        raise ValueError(
            f"Window span {span_days} days exceeds cap of "
            f"{_RATE_PLANS_MAX_WINDOW_DAYS} days"
        )
    return start.strftime(_ISO_DATE_FORMAT), end.strftime(_ISO_DATE_FORMAT)


def _parse_iso_date(value: str | None) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` string or return ``None`` for empty input.

    Raises:
        ValueError: When ``value`` is non-empty and not a valid ISO date.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid ISO date {value!r} (expected YYYY-MM-DD)"
        ) from exc


@router.get(
    "/properties/{property_id}/knowledge",
    response_model=PropertyKnowledgeResponse,
)
async def get_property_knowledge(
    property_id: str,
) -> PropertyKnowledgeResponse:
    """Return the stored :class:`PropertyProfile` for a property.

    The static profile answers "what does Brain know about this
    place?" with aggregate counts per onboarding domain.

    Args:
        property_id: Property channel id used for the profile store
            lookup.

    Raises:
        HTTPException: 404 when the profile store has no entry for
            ``property_id``.
    """
    store = _profile_store()
    profile = await store.get(property_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"No knowledge profile for property {property_id!r}",
        )
    return _profile_to_response(profile)


@router.get(
    "/properties/{property_id}/unanswered-threads",
    response_model=UnansweredThreadsResponse,
)
async def get_unanswered_threads(
    property_id: str,
) -> UnansweredThreadsResponse:
    """Return the sandbox rows for a property.

    Each row represents a guest thread whose most recent message is
    still awaiting a PM reply, together with the AI-generated
    candidate reply the PM can approve, edit, or discard.
    """
    store = _sandbox_store()
    threads = await store.list_for_property(property_id)
    return UnansweredThreadsResponse(
        property_id=property_id,
        total=len(threads),
        threads=tuple(_thread_to_response(thread) for thread in threads),
    )


_SANDBOX_MAX_COUNT: int = 3


@router.get(
    "/interview/sandbox/{property_id}",
    response_model=SandboxResponse,
)
async def interview_sandbox(
    property_id: str,
    max: int = _SANDBOX_MAX_COUNT,
) -> SandboxResponse:
    """Return up to ``max`` sample questions for the onboarding sandbox.

    ``max`` is clamped into ``[1, 3]`` — Mümin's spec asks for three
    preview questions, and we hard-cap the ceiling so the sandbox
    never turns into a full catalog dump.
    """
    engine = _interview_engine()
    clamped = max if 1 <= max <= _SANDBOX_MAX_COUNT else _SANDBOX_MAX_COUNT
    questions = await engine.next_questions(
        property_id=property_id,
        max_count=clamped,
    )
    return SandboxResponse(
        property_id=property_id,
        max_count=clamped,
        questions=tuple(_question_to_response(q) for q in questions),
    )


@router.post(
    "/interview/sandbox/{property_id}/answers",
    response_model=SandboxAnswerResponse,
)
async def submit_sandbox_answer(
    property_id: str,
    payload: SandboxAnswerRequest,
) -> SandboxAnswerResponse:
    """Record one PM-supplied sandbox answer.

    Every answer is persisted in two places so future recall has a
    backing store and the readiness gate has a single source of
    truth:

    1. The interview engine upserts an :class:`InterviewAnswer` row.
       This is what :class:`SandboxReadinessService` reads to decide
       whether the property has cleared the V1 onboarding gate, so
       this write is treated as authoritative — failures fail the
       request.
    2. A :class:`DecisionCard` is written through the configured
       :class:`CardStore`, giving the UI a stable ``card_id`` it can
       reference in the timeline and audit view.
    """
    answer_text = payload.answer.strip()
    if not answer_text:
        raise HTTPException(
            status_code=400,
            detail="Answer must be a non-empty string",
        )
    engine = _interview_engine()
    try:
        await engine.record_answer(
            property_id=property_id,
            qid=payload.qid,
            answer_text=answer_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = _card_store()
    card = _build_sandbox_answer_card(
        property_id=property_id,
        qid=payload.qid,
        answer=answer_text,
        language=payload.language,
    )
    stored = await store.save(card)
    return SandboxAnswerResponse(
        property_id=property_id,
        qid=payload.qid,
        card_id=stored.card_id,
    )


@router.post(
    "/properties/{property_id}/sandbox/preview-reply",
    response_model=PreviewReplyResponse,
)
async def preview_sandbox_reply(
    property_id: str,
    payload: PreviewReplyRequest,
) -> PreviewReplyResponse:
    """Generate a time-aware preview reply + guardrail check.

    Backs the V1 sandbox screen where the PM tests the same
    question at different temporal distances — e.g. a wifi
    request 24 h before arrival should land in
    :attr:`BookingStage.PRE_ARRIVAL`, while the same question 4 h
    before arrival should land in :attr:`BookingStage.CHECKIN`,
    so the two are stored as **different** patterns by the live
    past-conversation pipeline.  The endpoint reuses
    :func:`classify_stage_by_window` so the sandbox prediction
    agrees with what gets persisted at runtime.

    The endpoint also runs :class:`RefusalExtractor` over the
    generated reply, so a "no door code without passport"
    pattern surfaces as ``requires_document`` in
    ``refusal_signals`` and the PM can spot guardrails before
    flipping the property live.

    Args:
        property_id: Channel id of the property under test.
        payload: Hypothetical guest message + stay window.

    Raises:
        HTTPException: 400 when ``guest_message`` is empty after
            stripping, 503 when no sandbox generator was wired at
            lifespan start.
    """
    guest_message = payload.guest_message.strip()
    if not guest_message:
        raise HTTPException(
            status_code=400,
            detail="guest_message must be a non-empty string",
        )
    generator = _sandbox_generator()
    stage = classify_stage_by_window(
        message_sent_at=payload.message_sent_at,
        arrival_date=payload.arrival_date,
        departure_date=payload.departure_date,
    )
    pm_facts = await _collect_pm_facts(property_id)
    example_reply = await generator.generate(
        property_id=property_id,
        guest_message=guest_message,
        language=payload.language,
        pm_facts=pm_facts,
    )
    extractor = RefusalExtractor()
    signals = extractor.extract(example_reply)
    return PreviewReplyResponse(
        property_id=property_id,
        predicted_stage=stage.value if stage is not None else "",
        example_reply=example_reply,
        generated_by=getattr(generator, "name", "unknown"),
        language=payload.language,
        refusal_signals=tuple(
            _refusal_signal_to_response(signal) for signal in signals
        ),
        guardrail_summary=_summarise_refusals(signals),
    )


def _refusal_signal_to_response(
    signal: RefusalSignal,
) -> RefusalSignalResponse:
    """Translate a :class:`RefusalSignal` into the wire form."""
    return RefusalSignalResponse(
        refusal_type=signal.refusal_type.value,
        language=signal.language.value,
        trigger_phrase=signal.trigger_phrase,
        conditional_clause=signal.conditional_clause,
        confidence=signal.confidence,
    )


async def _collect_pm_facts(property_id: str) -> tuple[str, ...]:
    """Pull PM-confirmed facts for ``property_id`` from the live store.

    The sandbox preview-reply endpoint must answer guest questions
    against the same memory the live conversation pipeline uses;
    silently skipping the lookup leaves the LLM blind to every PM
    correction the operator has typed in.  We resolve the
    ``customer_id`` via the property profile and fall back to an
    empty tuple — newest-first ordering, matching the live prompt
    layout — when either the store or the profile is unavailable.

    Failures are swallowed: this is a sandbox, not a hot path, so a
    misconfigured fact store cannot 500 the preview.

    Args:
        property_id: Channel id of the property under preview.

    Returns:
        Tuple of fact text snippets, newest-first; empty when the
        store, profile, or facts are absent.
    """
    fact_store = _pm_fact_store_optional()
    if fact_store is None:
        return ()
    try:
        profile = await _profile_store().get(property_id)
    except HTTPException:
        return ()
    except Exception:  # noqa: BLE001 - sandbox is best-effort
        return ()
    if profile is None or not profile.customer_id:
        return ()
    try:
        facts = await fact_store.list_facts(
            customer_id=profile.customer_id,
            property_channel_id=property_id,
        )
    except Exception:  # noqa: BLE001 - sandbox is best-effort
        return ()
    # Newest first so the LLM weighs the freshest correction
    # heaviest — :class:`PmFact` carries ``created_at`` UTC.
    ordered = sorted(facts, key=lambda f: f.created_at, reverse=True)
    return tuple(f.fact_text for f in ordered if f.fact_text.strip())


def _summarise_refusals(
    signals: tuple[RefusalSignal, ...],
) -> str:
    """Produce a short human-readable guardrail summary.

    Returns the sorted, comma-separated set of refusal-type
    values found in ``signals``.  An empty input yields an empty
    string so the UI can simply check truthiness to decide
    whether to render the guardrail badge.
    """
    if not signals:
        return ""
    types = sorted({signal.refusal_type.value for signal in signals})
    return ", ".join(types)


class SandboxReadinessResponse(BaseModel):
    """Wire form of :class:`SandboxReadiness`.

    The UI polls this endpoint after every sandbox answer to decide
    whether the "go live" CTA should unlock.  ``ready`` is the only
    field it strictly needs; the counters and timestamp are surfaced
    so the screen can render progress ("2 / 3 answered") without a
    separate roundtrip.
    """

    property_id: str
    ready: bool
    answered_count: int
    required_count: int
    last_answer_at: datetime | None


@router.get(
    "/interview/sandbox/{property_id}/readiness",
    response_model=SandboxReadinessResponse,
)
async def interview_sandbox_readiness(
    property_id: str,
) -> SandboxReadinessResponse:
    """Return whether the sandbox 3-question gate has cleared.

    Cendra V1 onboarding step 15 — the property is "ready for live"
    once the PM has supplied the configured number of non-empty
    answers (default 3).  The signal is computed on every call from
    the ``interview_answers`` table, so retracting an answer flips
    the gate back to ``ready=False`` without manual cleanup.
    """
    service = _readiness_service()
    snapshot = await service.compute(property_id)
    return _readiness_to_response(snapshot)


def _readiness_to_response(
    snapshot: SandboxReadiness,
) -> SandboxReadinessResponse:
    """Project a :class:`SandboxReadiness` into its wire form."""
    return SandboxReadinessResponse(
        property_id=snapshot.property_id,
        ready=snapshot.ready,
        answered_count=snapshot.answered_count,
        required_count=snapshot.required_count,
        last_answer_at=snapshot.last_answer_at,
    )


@router.get(
    "/properties/{property_id}/memory",
    response_model=MemoryRecallResponse,
)
async def property_memory(
    property_id: str,
    q: str = Query(default="", max_length=512),
    customer_id: str = Query(default="", max_length=128),
    conversation_id: str = Query(default="", max_length=128),
    as_of: str = Query(default="", max_length=64),
) -> MemoryRecallResponse:
    """Return recalled PM-confirmed facts for a property.

    Backs the V2 "what does Brain remember?" side panel.  The handler
    pulls rows from the PM fact store keyed by
    ``(customer_id, property_channel_id)`` and projects them into
    the wire shape, ordered newest-first by ``created_at``.  ``q`` is
    accepted for forward compatibility but does not currently filter
    rows — the panel lists everything the PM has confirmed for the
    pair.

    Args:
        property_id: Channel id of the property being recalled.
        q: Reserved free-text filter — forwarded to the response so
            the UI can echo what the user typed; not yet used to
            filter rows.
        customer_id: Optional explicit tenant scope.  When omitted
            the handler backfills it from the property profile.
        conversation_id: Optional conversation scope used by the
            cascade panel — does not affect fact selection.
        as_of: Optional ISO-8601 timestamp.  When supplied, only
            facts whose ``created_at`` is ``<= as_of`` are
            returned, so the panel can answer "what was the wifi
            password on April 28?" without the freshest correction
            shadowing earlier entries.  Empty (default) keeps
            live-chat semantics.

    Raises:
        HTTPException: 400 when ``as_of`` is provided but cannot be
            parsed as an ISO-8601 timestamp.
        HTTPException: 503 when the PM fact store is not configured
            or when ``customer_id`` is missing — the store cannot be
            queried without a tenant scope.
    """
    scope_ids = _cascade_scope_ids(
        property_id=property_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
    )
    cutoff = _parse_as_of(as_of)
    facts = await _facts_from_pm_store(
        customer_id=customer_id,
        property_id=property_id,
        as_of=cutoff,
    )
    if facts is None:
        raise HTTPException(
            status_code=503,
            detail="PM fact store not available",
        )
    return MemoryRecallResponse(
        property_id=property_id,
        query=q,
        facts=facts,
        scopes=scope_ids,
    )


def _parse_as_of(value: str) -> "datetime | None":
    """Parse the ``as_of`` query parameter into an aware datetime.

    Empty / whitespace-only input returns ``None`` so the store
    falls back to the unconditional list_facts contract.  Invalid
    timestamps raise ``HTTPException(400)`` rather than degrading
    silently — temporal queries are deterministic by design.

    Args:
        value: Raw query-string value forwarded by FastAPI.

    Returns:
        Timezone-aware UTC datetime or ``None`` for empty input.
    """
    if not value or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="as_of must be an ISO-8601 timestamp",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


async def _facts_from_pm_store(
    *,
    customer_id: str,
    property_id: str,
    as_of: "datetime | None" = None,
) -> tuple[MemoryFactResponse, ...] | None:
    """Project the PM fact store into the memory-recall wire shape.

    The PM-fact store is keyed by ``(customer_id, property_channel_id)``
    so the lookup needs a customer scope.  When the caller does not
    pass one explicitly we resolve it from the
    :class:`PropertyProfileStore` — V1 UI surfaces the property by id
    without asking the operator to type a tenant id, so requiring it
    on the wire would force a useless round-trip through the V2 panel.
    Only when both the explicit query parameter and the profile lookup
    fail to yield a customer scope do we degrade to ``None``.

    Args:
        customer_id: Tenant scoping the recall.  When empty we attempt
            to backfill it from the property profile before giving up.
        property_id: Property scoping the recall.  Empty values still
            surface customer-wide rows (rows whose
            ``property_channel_id`` is empty) per the store contract.
        as_of: Optional cut-off forwarded to
            :meth:`PmFactStore.list_facts`.  ``None`` preserves the
            live-chat contract; any datetime restricts the response
            to facts known at that point in time.

    Returns:
        A tuple of :class:`MemoryFactResponse` rows ordered
        newest-first, or ``None`` when no recall is possible.
    """
    store = _pm_fact_store_optional()
    if store is None:
        return None
    resolved_customer = customer_id or await _resolve_customer_id(property_id)
    if not resolved_customer:
        return None
    try:
        rows = await store.list_facts(
            customer_id=resolved_customer,
            property_channel_id=property_id,
            as_of=as_of,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("profile.pm_fact_recall_failed err=%s", exc)
        return ()
    _emit_memory_recall_metric(tier="pm_facts", hit=bool(rows))
    return tuple(
        MemoryFactResponse(
            text=row.fact_text,
            valid_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
        if row.fact_text
    )


async def _resolve_customer_id(property_id: str) -> str:
    """Fetch ``customer_id`` for ``property_id`` via the profile store.

    Used as a fallback when callers do not pass ``customer_id`` on the
    wire — the UI knows the property channel id but not the tenant
    GUID.  Failures degrade to an empty string so the caller can
    decide whether to surface a 503 or simply return an empty list.

    Args:
        property_id: Channel id whose owning customer is needed.

    Returns:
        The resolved ``customer_id`` or empty string when unavailable.
    """
    if not property_id:
        return ""
    try:
        profile = await _profile_store().get(property_id)
    except HTTPException:
        return ""
    except Exception:  # noqa: BLE001 - resolution is best-effort
        return ""
    if profile is None:
        return ""
    return profile.customer_id or ""


@router.get(
    "/memory/timeline",
    response_model=TimelineResponse,
)
async def memory_timeline(
    property_id: str = Query(default="", max_length=128),
    customer_id: str = Query(default="", max_length=128),
    conversation_id: str = Query(default="", max_length=128),
    top_k: int = Query(
        default=_TIMELINE_DEFAULT_TOP_K,
        ge=1,
        le=_TIMELINE_MAX_TOP_K,
    ),
    from_time: str = Query(default="", max_length=64),
    to_time: str = Query(default="", max_length=64),
) -> TimelineResponse:
    """Return a time-ordered episode timeline for a cascade scope.

    Exactly one of ``property_id`` / ``customer_id`` /
    ``conversation_id`` must be supplied — a timeline is always
    anchored to a single subject.  Supplying more than one returns
    400 because unioning timelines across subjects would obscure the
    "past → present" chain the V2 narrative view renders.

    ``from_time`` / ``to_time`` accept ISO-8601 timestamps and are
    inclusive bounds on the row ``created_at`` in the PM fact
    store, which is the authoritative chronological record.
    """
    scope_ids = _cascade_scope_ids(
        property_id=property_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
    )
    if len(scope_ids) != 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "Exactly one of property_id / customer_id / "
                "conversation_id must be provided"
            ),
        )
    time_from = _parse_iso_datetime(from_time)
    time_to = _parse_iso_datetime(to_time)
    if time_from is not None and time_to is not None and time_from > time_to:
        raise HTTPException(
            status_code=400,
            detail="from_time must be <= to_time",
        )
    scope = scope_ids[0]
    pm_episodes = await _timeline_from_pm_store(
        customer_id=customer_id,
        property_id=property_id,
        top_k=top_k,
        time_from=time_from,
        time_to=time_to,
    )
    case_episodes = await _timeline_from_case_store(
        property_id=property_id,
        top_k=top_k,
        time_from=time_from,
        time_to=time_to,
    )
    if pm_episodes is None and case_episodes is None:
        raise HTTPException(
            status_code=503,
            detail="No timeline source configured",
        )
    merged: list[TimelineEpisodeResponse] = []
    if pm_episodes is not None:
        merged.extend(pm_episodes)
    if case_episodes is not None:
        merged.extend(case_episodes)
    # Newest-first ordering across both sources, capped at top_k.
    merged.sort(key=lambda e: e.valid_at, reverse=True)
    episodes = tuple(merged[:top_k])
    return TimelineResponse(
        scope=scope,
        time_from=from_time,
        time_to=to_time,
        episodes=episodes,
    )


async def _timeline_from_pm_store(
    *,
    customer_id: str,
    property_id: str,
    top_k: int,
    time_from: datetime | None,
    time_to: datetime | None,
) -> tuple[TimelineEpisodeResponse, ...] | None:
    """Project the PM fact store into the timeline wire shape.

    PM corrections are stamped with ``created_at`` so they form a
    natural chronological feed: each row becomes one episode whose
    ``valid_at`` is the row's create time and whose ``content`` is
    the manager-confirmed text.  When a ``time_from`` / ``time_to``
    bound is supplied we enforce it inclusively.

    Args:
        customer_id: Tenant scoping the timeline.  Without it the
            handler yields ``None`` so the caller emits 503.
        property_id: Property scoping the timeline; empty means
            "customer-wide" rows only.
        top_k: Maximum number of episodes to return.
        time_from: Inclusive lower bound on ``created_at`` (or
            ``None`` for unbounded).
        time_to: Inclusive upper bound on ``created_at`` (or
            ``None`` for unbounded).

    Returns:
        Tuple of episodes ordered newest-first, capped at ``top_k``,
        or ``None`` when no recall is possible.
    """
    store = _pm_fact_store_optional()
    if store is None or not customer_id:
        return None
    try:
        rows = await store.list_facts(
            customer_id=customer_id,
            property_channel_id=property_id,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("profile.pm_timeline_fallback_failed err=%s", exc)
        return ()
    episodes: list[TimelineEpisodeResponse] = []
    for row in rows:
        if not row.fact_text:
            continue
        if time_from is not None and row.created_at < time_from:
            continue
        if time_to is not None and row.created_at > time_to:
            continue
        episodes.append(
            TimelineEpisodeResponse(
                name="pm_correction",
                source="pm_fact_store",
                content=row.fact_text,
                valid_at=row.created_at.isoformat() if row.created_at else "",
            ),
        )
        if len(episodes) >= top_k:
            break
    return tuple(episodes)


async def _timeline_from_case_store(
    *,
    property_id: str,
    top_k: int,
    time_from: datetime | None,
    time_to: datetime | None,
) -> tuple[TimelineEpisodeResponse, ...] | None:
    """Project the bootstrap-loaded DecisionCase archive into the timeline.

    Mümin 2026-05-13 (PR #E): the original timeline only carried
    PM corrections, so the 885-conversation 323133 archive ingested
    by ``POST /onboarding/bootstrap`` stayed invisible.  This helper
    reads :class:`DecisionCaseStore` (filtered by ``property_id``)
    and renders each case as one timeline episode anchored on the
    case's ``decision_at`` — the moment the guest's message arrived,
    NOT the bootstrap wall-clock — so the chronology reflects the
    actual operational history.

    Args:
        property_id: Property scoping the timeline.  Empty value
            returns an empty tuple (timeline is always
            property-anchored on the bootstrap path).
        top_k: Cap on returned episodes.  Newest-first.
        time_from: Inclusive lower bound on ``decision_at`` /
            ``created_at``.
        time_to: Inclusive upper bound on ``decision_at`` /
            ``created_at``.

    Returns:
        Tuple of episodes (newest-first, top_k capped) or ``None``
        when no DecisionCaseStore is wired so the caller can treat
        the absence as "case archive unavailable" rather than "no
        history".
    """
    store = _decision_case_store_optional()
    if store is None or not property_id:
        return None
    try:
        cases = await store.search(
            property_id=property_id,
            limit=max(1, min(top_k * 4, 1000)),
            offset=0,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "profile.case_timeline_fetch_failed err=%s", exc,
        )
        return ()
    episodes: list[TimelineEpisodeResponse] = []
    for case in cases:
        # ``decision_at`` is the guest-message anchor (Mümin
        # round-4 fix); fall back to ``created_at`` when missing.
        anchor = getattr(case, "decision_at", None) or case.created_at
        if anchor is None:
            continue
        if time_from is not None and anchor < time_from:
            continue
        if time_to is not None and anchor > time_to:
            continue
        message = (case.message_text or "").strip()
        if not message:
            continue
        action = case.decision.action_type.value
        scenario = case.scenario.value
        content = (
            f"[{scenario} → {action}] {message}"
            if message
            else f"[{scenario} → {action}]"
        )
        episodes.append(
            TimelineEpisodeResponse(
                name=scenario,
                source="decision_case_store",
                content=content,
                valid_at=anchor.isoformat(),
            ),
        )
        if len(episodes) >= top_k:
            break
    return tuple(episodes)


@router.get(
    "/memory/audit/sample",
    response_model=MemoryAuditSampleResponse,
)
async def memory_audit_sample(
    property_id: str = Query(default="", max_length=128),
    customer_id: str = Query(default="", max_length=128),
    conversation_id: str = Query(default="", max_length=128),
    sample_size: int = Query(
        default=_AUDIT_SAMPLE_DEFAULT_SIZE,
        ge=1,
        le=_AUDIT_SAMPLE_MAX_SIZE,
    ),
) -> MemoryAuditSampleResponse:
    """Return a random sample of facts persisted under one cascade scope.

    Backs the V2 "what does Brain actually remember?" audit panel and
    addresses **Risk 4** (LLM extraction errors): operators can refresh
    the panel to get a different slice of facts each time and flag
    any hallucinated or mis-attributed line for correction.  Unlike
    :pyfunc:`memory_timeline`, the ordering is random — the audit
    panel is not about chronology, it is about coverage.

    Exactly one of ``property_id`` / ``customer_id`` /
    ``conversation_id`` must be supplied; supplying more than one
    returns 400 because audit always anchors to a single subject.

    Raises:
        HTTPException: 400 when zero or multiple scopes are supplied,
            503 when the PM fact store is unavailable or
            ``customer_id`` is missing (the store cannot be queried
            without a tenant scope).
    """
    scope_ids = _cascade_scope_ids(
        property_id=property_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
    )
    if len(scope_ids) != 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "Exactly one of property_id / customer_id / "
                "conversation_id must be provided"
            ),
        )
    scope = scope_ids[0]
    episodes = await _audit_sample_from_pm_store(
        customer_id=customer_id,
        property_id=property_id,
        sample_size=sample_size,
    )
    if episodes is None:
        raise HTTPException(
            status_code=503,
            detail="PM fact store not available",
        )
    return MemoryAuditSampleResponse(
        scope=scope,
        sample_size=sample_size,
        episodes=episodes,
    )


async def _audit_sample_from_pm_store(
    *,
    customer_id: str,
    property_id: str,
    sample_size: int,
) -> tuple[MemoryAuditEpisodeResponse, ...] | None:
    """Project a random PM-fact-store slice into the audit wire shape.

    Sampling without replacement uses :pyfunc:`random.sample` over
    the full row set — small N keeps the cost negligible and avoids
    any DB-side ``ORDER BY RANDOM()`` round trip.  ``customer_id``
    is required because the store is keyed by tenant; without it
    the handler cannot honour the audit's "anchor to a single
    subject" guarantee.

    Returns:
        Tuple of audit episodes capped at ``sample_size``, or
        ``None`` when the store is unavailable / unscoped.
    """
    import random

    store = _pm_fact_store_optional()
    if store is None or not customer_id:
        return None
    try:
        rows = await store.list_facts(
            customer_id=customer_id,
            property_channel_id=property_id,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("profile.pm_audit_sample_failed err=%s", exc)
        return ()
    pool = [row for row in rows if row.fact_text]
    if not pool:
        return ()
    sample = random.sample(pool, min(sample_size, len(pool)))
    return tuple(
        MemoryAuditEpisodeResponse(
            name="pm_correction",
            source="pm_fact_store",
            content=row.fact_text,
            valid_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in sample
    )


# ── Internals ─────────────────────────────────────────────────── #


def _rate_plan_to_response(
    rate_plan: RatePlanWithCalendar,
) -> RatePlanWithCalendarResponse:
    """Translate a :class:`RatePlanWithCalendar` into the wire form."""
    return RatePlanWithCalendarResponse(
        rate_plan_id=rate_plan.rate_plan_id,
        channel_entity_id=rate_plan.channel_entity_id,
        property_channel_id=rate_plan.property_channel_id,
        property_pms_id=rate_plan.property_pms_id,
        name=rate_plan.name,
        title=rate_plan.title,
        currency=rate_plan.currency,
        rate_mode=rate_plan.rate_mode,
        is_active=rate_plan.is_active,
        calendar=tuple(
            _calendar_day_to_response(day) for day in rate_plan.calendar
        ),
        occupancy_options=tuple(
            _occupancy_to_response(option)
            for option in rate_plan.occupancy_options
        ),
    )


def _calendar_day_to_response(day: CalendarDay) -> CalendarDayResponse:
    """Translate a :class:`CalendarDay`."""
    return CalendarDayResponse(
        date=day.date,
        note=day.note,
        stop_sell=day.stop_sell,
        count_available_units=day.count_available_units,
        price=day.price,
    )


def _occupancy_to_response(option: OccupancyOption) -> OccupancyOptionResponse:
    """Translate an :class:`OccupancyOption`."""
    return OccupancyOptionResponse(
        occupancy=option.occupancy,
        is_primary=option.is_primary,
        rate=option.rate,
    )


def _summary_to_response(summary: PropertySummary) -> PropertySummaryResponse:
    """Translate a :class:`PropertySummary` into the picker wire form."""
    return PropertySummaryResponse(
        channel_entity_id=summary.channel_entity_id,
        pms_id=summary.pms_id,
        title=summary.title,
        is_active=summary.is_active,
        city=summary.city,
        country=summary.country,
        property_type=summary.property_type,
        max_occupancy=summary.max_occupancy,
        bedrooms=summary.bedrooms,
        bathrooms=summary.bathrooms,
        base_price=summary.base_price,
        base_currency=summary.base_currency,
        listing_id=summary.listing_id,
    )


def _profile_to_response(
    profile: PropertyProfile,
) -> PropertyKnowledgeResponse:
    """Translate the domain object into the HTTP wire form."""
    return PropertyKnowledgeResponse(
        property_channel_id=profile.property_channel_id,
        pms_id=profile.pms_id,
        customer_id=profile.customer_id,
        org_id=profile.org_id,
        provider_type=profile.provider_type,
        title=profile.title,
        is_active=profile.is_active,
        city=profile.city,
        country=profile.country,
        property_type=profile.property_type,
        max_occupancy=profile.max_occupancy,
        bedrooms=profile.bedrooms,
        bathrooms=profile.bathrooms,
        base_currency=profile.base_currency,
        base_price=profile.base_price,
        knowledge_percentage=profile.knowledge_percentage,
        amenity_codes=profile.amenity_codes,
        image_count=profile.image_count,
        room_count=profile.room_count,
        description_languages=profile.description_languages,
        coverage_ratio=profile.coverage_ratio,
        sections=tuple(
            _section_to_response(section) for section in profile.sections
        ),
        review_aggregate=_review_to_response(profile.review_aggregate),
        static_payload=dict(profile.static_payload),
        built_at=profile.built_at.isoformat(),
    )


def _section_to_response(
    section: KnowledgeSection,
) -> KnowledgeSectionResponse:
    """Translate a :class:`KnowledgeSection`."""
    return KnowledgeSectionResponse(
        name=section.name,
        item_count=section.item_count,
        last_ingested_at=(
            section.last_ingested_at.isoformat()
            if section.last_ingested_at
            else None
        ),
        notes=section.notes,
    )


def _review_to_response(
    aggregate: ReviewAggregate,
) -> ReviewAggregateResponse:
    """Translate a :class:`ReviewAggregate`."""
    return ReviewAggregateResponse(
        total=aggregate.total,
        with_rating=aggregate.with_rating,
        average_rating=aggregate.average_rating,
        latest_review_at=(
            aggregate.latest_review_at.isoformat()
            if aggregate.latest_review_at
            else None
        ),
    )


def _thread_to_response(thread: UnansweredThread) -> UnansweredThreadResponse:
    """Translate an :class:`UnansweredThread` to the wire form."""
    return UnansweredThreadResponse(
        conversation_id=thread.conversation_id,
        property_id=thread.property_id,
        last_guest_message=thread.last_guest_message,
        last_guest_sent_at=thread.last_guest_sent_at.isoformat(),
        example_reply=thread.example_reply,
        generated_by=thread.generated_by,
        language=thread.language,
        generated_at=thread.generated_at.isoformat(),
        needs_review_reason=thread.needs_review_reason,
    )


def _question_to_response(
    question: InterviewQuestion,
) -> SandboxQuestionResponse:
    """Translate an :class:`InterviewQuestion` to the sandbox wire form."""
    return SandboxQuestionResponse(
        qid=question.qid,
        stage=question.stage,
        topic=question.topic,
        prompt_text=question.prompt_text,
        priority=question.priority.value,
        depends_on_qids=question.depends_on_qids,
        triggered_by_events=question.triggered_by_events,
    )


# ── Cascade scope helpers + card construction ────────────────── #


_SCOPE_PROPERTY_PREFIX: Final[str] = "property"
_SCOPE_CUSTOMER_PREFIX: Final[str] = "customer"
_SCOPE_CONVERSATION_PREFIX: Final[str] = "conversation"
_SANDBOX_WORKFLOW: Final[str] = "onboarding_sandbox_answer"


def _cascade_scope_ids(
    *,
    property_id: str,
    customer_id: str,
    conversation_id: str,
) -> tuple[str, ...]:
    """Build the cascade tuple ``(property, customer, conversation)``.

    Empty ids are dropped so callers can pass partial pointers
    without pre-filtering.  Order is preserved because the recall
    UI renders the same order when displaying per-scope results.
    """
    scopes: list[str] = []
    if property_id:
        scopes.append(f"{_SCOPE_PROPERTY_PREFIX}:{property_id}")
    if customer_id:
        scopes.append(f"{_SCOPE_CUSTOMER_PREFIX}:{customer_id}")
    if conversation_id:
        scopes.append(
            f"{_SCOPE_CONVERSATION_PREFIX}:{conversation_id}"
        )
    return tuple(scopes)


def _parse_iso_datetime(value: str) -> "datetime | None":
    """Parse an ISO-8601 string to UTC datetime; empty → ``None``.

    Accepts the trailing ``Z`` shorthand that browsers emit.
    Raises :class:`HTTPException` (400) on malformed input so the
    UI gets a clear "bad range" signal instead of silent drop.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ISO-8601 timestamp: {value!r}",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_sandbox_answer_card(
    *,
    property_id: str,
    qid: str,
    answer: str,
    language: str,
) -> DecisionCard:
    """Build a :class:`DecisionCard` that records a sandbox answer.

    The card carries a ``LOG_DECISION`` action (no side effect beyond
    persistence) tagged with ``PATTERN_LEARNED`` because each answer
    teaches the engine about the PM's operating preferences.
    """
    reasoning = (
        ReasoningRow(
            kind=EvidenceKind.MANUAL,
            label=f"pm_answer qid={qid}",
            weight=1.0,
            reference_id=qid,
        ),
    )
    action = PreparedAction(
        action_type=CardActionKind.LOG_DECISION.value,
        payload={
            "qid": qid,
            "answer": answer,
            "language": language,
            "source": "onboarding_sandbox",
        },
        reversibility=ReversibilityTier.GREEN,
        undo_window_seconds=60,
    )
    return DecisionCard(
        property_id=property_id,
        workflow=_SANDBOX_WORKFLOW,
        context_tag=ContextTag.PATTERN_LEARNED.value,
        title=f"Sandbox answer captured ({qid})",
        reasoning=reasoning,
        action=action,
        trust_footer="Stored in observe mode for memory recall.",
        autonomy_state=AutonomyState.OBSERVE,
    )




def _emit_memory_recall_metric(*, tier: str, hit: bool) -> None:
    """Forward one memory recall outcome to the Prometheus exporter.

    Best-effort — any exporter exception is swallowed so a broken
    metrics registry can never break the recall response on the
    live-chat hot path.
    """
    try:
        from brain_engine.observability.exporters.prometheus_exporter import (
            build_default_exporter,
        )

        exporter = build_default_exporter()
        if hit:
            exporter.record_memory_hit(tier=tier)
        else:
            exporter.record_memory_miss(tier=tier)
    except Exception:  # noqa: BLE001 — never break recall
        return
