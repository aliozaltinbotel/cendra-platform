"""Pattern and blocker API endpoints — REST interface for Cendra frontend.

Exposes the decision-pattern subsystem over HTTP:

- **DecisionCase logging**: Record operational decisions with full context.
- **DecisionCase search**: Query historical cases by scenario, property, etc.
- **PatternRule management**: View active rules, trigger extraction.
- **Blocker CRUD**: Create, resolve, and query active blockers.
- **Calendar intelligence**: Analyse gaps and scheduling feasibility.

All endpoints follow the existing Brain Engine API conventions:
- APIRouter with ``/api/v1`` prefix.
- Dependency injection for services.
- Pydantic request/response models.
- Structured logging.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from brain_engine.blockers.engine import (
    BlockerEngine,
    BlockerStore,
    InMemoryBlockerStore,
)
from brain_engine.blockers.models import BlockerSeverity, BlockerType
from brain_engine.calendar.evaluator import CalendarEvaluator
from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.extractor import (
    PatternExtractor,
    _merge_subsumed_rules,
)
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    DecisionType,
    PatternRule,
    PatternScope,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.pattern_miner import (
    _resolve_pattern_rule_contradictions,
)
from brain_engine.patterns.stage_labels import (
    format_stage_group,
    lookup_stage_short,
)
from brain_engine.patterns.store import (
    DecisionCaseStore,
    InMemoryDecisionCaseStore,
    InMemoryPatternRuleStore,
    PatternRuleStore,
)
from brain_engine.patterns.validator import PatternValidator

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["patterns"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LogDecisionRequest(BaseModel):
    """Request to log a DecisionCase.

    The ``resolution_type`` / ``successful`` / ``approved`` fields are
    optional but, when supplied, fold into a :class:`CaseOutcome`
    attached to the persisted case so :meth:`DecisionCase.is_learnable`
    returns True.  Without them the case lands as ``has_outcome=False``
    and PatternExtractor / PatternMiner skip it.  Callers that want
    the case to participate in mining must supply at least
    ``resolution_type``.
    """

    message_text: str = Field(description="Original guest message.")
    response_text: str = Field(default="", description="Engine response.")
    property_id: str = Field(description="Property identifier.")
    owner_id: str = Field(description="Owner identifier.")
    stage: str = Field(description="Booking stage (e.g. 'pre_arrival').")
    scenario: str = Field(
        description="Scenario (e.g. 'guest_count_mismatch')."
    )
    decision_type: str = Field(description="Decision type (e.g. 'approve').")
    decision_params: dict[str, Any] = Field(default_factory=dict)
    reservation_id: str | None = Field(default=None)
    guest_id: str | None = Field(default=None)
    message_language: str = Field(default="en")
    pms_data: dict[str, Any] = Field(default_factory=dict)
    calendar_data: dict[str, Any] = Field(default_factory=dict)
    ops_data: dict[str, Any] = Field(default_factory=dict)
    guest_data: dict[str, Any] = Field(default_factory=dict)
    executed_actions: list[str] = Field(default_factory=list)
    resolution_type: str | None = Field(
        default=None,
        description=(
            "Optional ResolutionType for the case outcome — e.g. "
            "'pm_approved', 'pm_denied'.  When omitted the case is "
            "stored without outcome and skipped by mining."
        ),
    )
    successful: bool | None = Field(
        default=None,
        description="Optional success flag attached to the outcome.",
    )
    approved: bool | None = Field(
        default=None,
        description="Optional PM approval flag attached to the outcome.",
    )
    revenue_impact: float | None = Field(
        default=None,
        description="Optional revenue impact in property currency.",
    )


class LogDecisionResponse(BaseModel):
    """Response after logging a DecisionCase."""

    case_id: str
    scenario: str
    stage: str


class CaseSearchRequest(BaseModel):
    """Request to search DecisionCases."""

    scenario: str | None = None
    property_id: str | None = None
    owner_id: str | None = None
    stage: str | None = None
    source_event_id: str | None = Field(
        default=None,
        description=(
            "Mümin 2026-05-15 round-5 #4 — restricts the result to "
            "cases whose ``origin.source_event_ids`` tuple contains "
            "the supplied upstream event id.  Lets callers drill "
            "from a rule's ``/origin.source_event_ids`` array back "
            "to the contributing cases."
        ),
    )
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of leading rows (after newest-first sort) to "
            "skip.  Combine with ``limit`` to paginate through the "
            "filtered set; ``total`` on the response gives the "
            "unfiltered count so callers can compute the last page."
        ),
    )


class CaseSearchResponse(BaseModel):
    """Response with search results.

    Mümin 2026-05-08 round-4 #3: ``total`` now reports the
    *unfiltered* count of cases matching the request filters, not the
    size of the returned page.  This lets callers paginate properly
    via ``offset`` / ``limit`` and know "how much data is there"
    without scanning every page.
    """

    cases: list[dict[str, Any]]
    total: int = Field(description="Total matching cases (unfiltered count).")
    offset: int = Field(
        default=0, description="Offset echoed from the request."
    )
    limit: int = Field(
        default=50, description="Limit echoed from the request."
    )
    has_more: bool = Field(
        default=False,
        description="True when more rows exist beyond the current page.",
    )


class ExtractPatternsRequest(BaseModel):
    """Request to trigger pattern extraction."""

    scenario: str = Field(description="Scenario to extract patterns for.")
    property_id: str = Field(description="Property identifier.")
    owner_id: str = Field(description="Owner identifier.")


class GateDecisionDTO(BaseModel):
    """Per-action gating outcome surfaced on the extract response.

    Mirrors :class:`brain_engine.patterns.extractor.GateDecision` —
    the dataclass is internal so the API has its own Pydantic model
    to keep the JSON contract decoupled from refactors of the
    learning pipeline.

    See :class:`ExtractPatternsResponse.gate_decisions` for usage.
    """

    action: str = Field(
        description="Action type the group represents (e.g. 'inform').",
    )
    accepted: bool = Field(
        description=(
            "True when the gate produced a rule for this action; "
            "False when one of the gates rejected the group."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Failed-gate tag — 'insufficient_support' or "
            "'low_confidence' — when accepted is False; null otherwise."
        ),
    )
    support_count: int = Field(description="Positive cases in the group.")
    counterexample_count: int = Field(
        description="Negative cases evaluated against the group.",
    )
    confidence: float | None = Field(
        default=None,
        description=(
            "Computed confidence in [0.0, 1.0], rounded to 4 dp.  "
            "Null when the support gate fired before confidence was "
            "computed."
        ),
    )
    min_support: int = Field(
        description="Support threshold the group was checked against.",
    )
    min_confidence: float = Field(
        description="Confidence threshold the group was checked against.",
    )


class ExtractPatternsResponse(BaseModel):
    """Response with extraction results."""

    rules_extracted: int
    total_cases: int
    positive_cases: int
    negative_cases: int
    defer_count: int = 0
    skipped_reasons: list[str]
    gate_decisions: list[GateDecisionDTO] = Field(default_factory=list)
    rules: list[dict[str, Any]]


class ActiveRulesResponse(BaseModel):
    """Response with active PatternRules.

    Mümin 2026-05-08 round-4 #3: ``total`` reports the unfiltered
    count of active rules matching the scope filters.  ``offset`` /
    ``limit`` echo the request and ``has_more`` flags whether more
    rows exist beyond the current page.
    """

    rules: list[dict[str, Any]]
    total: int = Field(description="Total matching rules (unfiltered count).")
    offset: int = Field(
        default=0, description="Offset echoed from the request."
    )
    limit: int = Field(
        default=50, description="Limit echoed from the request."
    )
    has_more: bool = Field(
        default=False,
        description="True when more rules exist beyond the current page.",
    )


class ScenarioStatsDTO(BaseModel):
    """Per-scenario summary surfaced on ``GET /patterns/scenarios``.

    Mümin 2026-05-08 round-4 #2: lets a UI populate a scenario filter
    dropdown for a property without scanning every rule, by listing
    the scenarios that actually have at least one active rule along
    with the count and freshness anchor.
    """

    scenario: str = Field(description="Scenario value (e.g. 'early_checkin').")
    rule_count: int = Field(
        description="Active rules for this scenario in the requested scope.",
    )
    last_seen_at: str | None = Field(
        default=None,
        description=(
            "ISO-8601 timestamp of the most recently observed evidence "
            "across the scenario's rules; null when no rule carries a "
            "``last_seen_at`` value."
        ),
    )


class ScenariosResponse(BaseModel):
    """Response listing scenarios with active rules in a scope."""

    scenarios: list[ScenarioStatsDTO]
    total: int = Field(description="Number of scenarios returned.")


class CreateBlockerRequest(BaseModel):
    """Request to create a blocker."""

    blocker_type: str = Field(description="Blocker type.")
    property_id: str = Field(description="Property identifier.")
    description: str = Field(description="Why this blocker exists.")
    reservation_id: str | None = None
    severity: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BlockerResponse(BaseModel):
    """Response with blocker details."""

    blocker_id: str
    blocker_type: str
    severity: str
    property_id: str
    reservation_id: str | None
    description: str
    is_active: bool
    created_at: str


class ResolveBlockerRequest(BaseModel):
    """Request to resolve a blocker."""

    blocker_id: str
    resolved_by: str


class GapAnalysisRequest(BaseModel):
    """Request to analyse calendar gaps."""

    calendar_data: dict[str, Any] = Field(description="Calendar data.")
    property_id: str = Field(description="Property identifier.")
    min_stay: int = Field(default=1, ge=1)


class GapAnalysisResponse(BaseModel):
    """Response with gap analysis results."""

    gaps: list[dict[str, Any]]
    total_gaps: int
    orphan_gaps: int


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------
#
# The router is mounted on the lifespan-managed FastAPI app where
# ``application.state.case_store`` / ``rule_store`` / ``blocker_store``
# point at the wired (typically Postgres-backed) implementations
# selected by ``DECISION_CASE_STORE_BACKEND`` /
# ``PATTERN_RULE_STORE_BACKEND`` and the matching DATABASE_URL.  The
# resolvers below pull the wired stores off ``app.state`` and fall
# back to module-level in-memory singletons only when the lifespan
# has not initialised them — that fallback exists for unit tests and
# scripts that import the router without running the full lifespan.
#
# Stateless helpers (``CaseBuilder``, ``FeatureBuilder``,
# ``PatternValidator``, ``CalendarEvaluator``) keep their module-level
# singletons because they hold no per-request state and re-creating
# them on every call would be wasted work.

_fallback_case_store = InMemoryDecisionCaseStore()
_fallback_rule_store = InMemoryPatternRuleStore()
_fallback_blocker_store = InMemoryBlockerStore()
_feature_builder = FeatureBuilder()
_case_builder = CaseBuilder(feature_builder=_feature_builder)
_validator = PatternValidator()
_calendar_evaluator = CalendarEvaluator()


def _resolve_case_store(request: Request) -> DecisionCaseStore:
    """Return the wired :class:`DecisionCaseStore` or the fallback."""
    store = getattr(request.app.state, "case_store", None)
    return store or _fallback_case_store


def _resolve_rule_store(request: Request) -> PatternRuleStore:
    """Return the wired :class:`PatternRuleStore` or the fallback."""
    store = getattr(request.app.state, "rule_store", None)
    return store or _fallback_rule_store


def _resolve_blocker_engine(request: Request) -> BlockerEngine:
    """Return a :class:`BlockerEngine` bound to the wired store.

    ``BlockerEngine`` is a thin wrapper over the store with no
    long-lived state, so constructing one per request is cheap and
    avoids cross-request leakage of any future internal caches.
    """
    store: BlockerStore = (
        getattr(request.app.state, "blocker_store", None)
        or _fallback_blocker_store
    )
    return BlockerEngine(store=store)


def _resolve_extractor(request: Request) -> PatternExtractor:
    """Build a :class:`PatternExtractor` over the wired case store."""
    return PatternExtractor(store=_resolve_case_store(request))


def _emit_pattern_rule_invalidated(*, scenario: str, scope: str) -> None:
    """Best-effort Prometheus emit for soft-invalidated rules.

    Mirror of the bootstrap-side helper — wraps the exporter in a
    try/except so a broken metrics registry never fails the
    extract endpoint.
    """
    try:
        from brain_engine.observability.exporters.prometheus_exporter import (
            build_default_exporter,
        )

        exporter = build_default_exporter()
        exporter.record_pattern_rule_invalidated(
            scenario=scenario,
            scope=scope,
        )
    except Exception:
        return


# ---------------------------------------------------------------------------
# DecisionCase endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/patterns/log-decision",
    response_model=LogDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def log_decision(
    payload: LogDecisionRequest,
    http_request: Request,
) -> LogDecisionResponse:
    """Log a DecisionCase from an operational interaction.

    This endpoint is called after every meaningful guest interaction to
    record the full decision context for learning.
    """
    try:
        stage = BookingStage(payload.stage)
        scenario = Scenario(payload.scenario)
        decision_type = DecisionType(payload.decision_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid enum value: {exc}",
        ) from exc

    outcome = _outcome_from_payload(payload)
    case = await _case_builder.build(
        message_text=payload.message_text,
        response_text=payload.response_text,
        property_id=payload.property_id,
        owner_id=payload.owner_id,
        stage=stage,
        scenario=scenario,
        decision_type=decision_type,
        decision_params=payload.decision_params,
        reservation_id=payload.reservation_id,
        guest_id=payload.guest_id,
        message_language=payload.message_language,
        pms_data=payload.pms_data,
        calendar_data=payload.calendar_data,
        ops_data=payload.ops_data,
        guest_data=payload.guest_data,
        executed_actions=tuple(payload.executed_actions),
        outcome=outcome,
    )

    case_store = _resolve_case_store(http_request)
    await case_store.store(case)

    logger.info(
        "decision_logged_via_api",
        case_id=case.case_id[:8],
        scenario=scenario.value,
    )

    return LogDecisionResponse(
        case_id=case.case_id,
        scenario=scenario.value,
        stage=stage.value,
    )


@router.post("/patterns/cases", response_model=CaseSearchResponse)
async def search_cases(
    payload: CaseSearchRequest,
    http_request: Request,
) -> CaseSearchResponse:
    """Search historical DecisionCases by criteria.

    Mümin 2026-05-08 round-4 #3: paginate via ``offset`` + ``limit``;
    ``total`` is the unfiltered count from the store's ``count``
    helper so callers can compute the last page and surface "how much
    data exists" in the UI.  Backward-compatible — existing callers
    that omit ``offset`` get the same first-page behaviour as before.
    """
    scenario = Scenario(payload.scenario) if payload.scenario else None
    stage = BookingStage(payload.stage) if payload.stage else None

    case_store = _resolve_case_store(http_request)
    cases = await case_store.search(
        scenario=scenario,
        property_id=payload.property_id,
        owner_id=payload.owner_id,
        stage=stage,
        source_event_id=payload.source_event_id,
        limit=payload.limit,
        offset=payload.offset,
    )
    total = await case_store.count(
        scenario=scenario,
        property_id=payload.property_id,
        owner_id=payload.owner_id,
        stage=stage,
        source_event_id=payload.source_event_id,
    )

    return CaseSearchResponse(
        cases=[
            {
                "case_id": c.case_id,
                "stage": c.stage.value,
                "scenario": c.scenario.value,
                "property_id": c.property_id,
                "owner_id": c.owner_id,
                "reservation_id": c.reservation_id,
                "decision_type": c.decision.action_type.value,
                "has_outcome": c.has_outcome,
                "pms_snapshot": dict(c.pms_snapshot or {}),
                "extracted_entities": dict(c.extracted_entities or {}),
                # Mümin round-5 #4 — surface the provenance trail
                # alongside the row so the response is self-explaining
                # (no follow-up /origin call needed to verify which
                # upstream event seeded the case).
                "foundation_scenario_id": c.foundation_scenario_id,
                "source_event_ids": list(c.origin.source_event_ids),
                "created_at": c.created_at.isoformat(),
            }
            for c in cases
        ],
        total=total,
        offset=payload.offset,
        limit=payload.limit,
        has_more=(payload.offset + len(cases)) < total,
    )


# ---------------------------------------------------------------------------
# PatternRule endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/patterns/extract",
    response_model=ExtractPatternsResponse,
)
async def extract_patterns(
    payload: ExtractPatternsRequest,
    http_request: Request,
) -> ExtractPatternsResponse:
    """Trigger pattern extraction for a scenario/property scope."""
    try:
        scenario = Scenario(payload.scenario)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid scenario: {exc}",
        ) from exc

    extractor = _resolve_extractor(http_request)
    result = await extractor.extract_patterns(
        scenario=scenario,
        property_id=payload.property_id,
        owner_id=payload.owner_id,
    )

    rule_store = _resolve_rule_store(http_request)

    # Sprint-1 bi-temporal step: every extract call covers a single
    # ``(scenario, property_id)`` bucket, so one ``get_active_rules``
    # round-trip is enough — fetch the existing actives once and feed
    # them to the resolver per new valid rule.  Falls back to an
    # empty list when the store does not implement the optional
    # ``get_active_rules`` (e.g. legacy in-memory stub used by some
    # tests) so the endpoint stays compatible.
    existing_rules: list[PatternRule] = []
    if hasattr(rule_store, "get_active_rules"):
        try:
            existing_rules = await rule_store.get_active_rules(
                scenario=scenario,
                scope=PatternScope.PROPERTY,
                scope_id=payload.property_id,
            )
        except Exception:
            logger.warning(
                "extract.rule_invalidation_fetch_failed",
                exc_info=True,
                property_id=payload.property_id,
                scenario=scenario.value,
            )

    # Rules being re-emitted by *this* extract run (same ``pattern_id``)
    # are refreshes, not contradictions — the UPSERT below replays their
    # row in place.  Feeding them back into
    # :func:`_resolve_pattern_rule_contradictions` would mis-classify the
    # cross-action pair (e.g. approve + deny over overlapping condition
    # slices) as a world-shift, return invalidated copies for the *other*
    # rule in the pair, and the trailing UPSERT loop would clobber the
    # freshly-stored row with ``active=False``.  Exclude self-refresh
    # candidates from the contradiction pool so genuine cross-call
    # invalidations still fire.
    new_pattern_ids = {rule.pattern_id for rule in result.rules}
    contradiction_candidates: list[PatternRule] = [
        existing
        for existing in existing_rules
        if existing.pattern_id not in new_pattern_ids
    ]

    invalidated_rules: list[PatternRule] = []
    validated_rules: list[dict[str, Any]] = []
    for rule in result.rules:
        validation = _validator.validate(rule)
        if validation.valid:
            await rule_store.store(rule)
            invalidated_rules.extend(
                _resolve_pattern_rule_contradictions(
                    rule,
                    contradiction_candidates,
                ),
            )
        validated_rules.append(
            {
                "pattern_id": rule.pattern_id,
                "scenario": rule.scenario.value,
                "action_type": rule.action.action_type.value,
                "conditions": rule.conditions,
                "confidence": rule.confidence,
                "risk_level": rule.risk_level.value,
                "stage": (
                    rule.stage.value if rule.stage is not None else None
                ),
                "execution_mode": rule.execution_mode.value,
                "support_count": rule.support_count,
                "counterexample_count": rule.counterexample_count,
                "rationale": rule.rationale,
                "valid": validation.valid,
                "validation_reasons": list(validation.reasons),
            }
        )

    # Persist invalidated candidates through the same UPSERT path —
    # store-level errors are logged, never bubbled, so a transient
    # write failure never breaks the extract response contract.
    for invalidated in invalidated_rules:
        try:
            await rule_store.store(invalidated)
            logger.info(
                "extract.rule_invalidated",
                pattern_id=invalidated.pattern_id,
                scenario=invalidated.scenario.value,
                invalid_at=(
                    invalidated.invalid_at.isoformat()
                    if invalidated.invalid_at is not None
                    else None
                ),
            )
            _emit_pattern_rule_invalidated(
                scenario=invalidated.scenario.value,
                scope=invalidated.scope.value,
            )
        except Exception:
            logger.exception(
                "extract.rule_invalidation_store_failed",
                pattern_id=invalidated.pattern_id,
            )

    # Mümin 2026-05-08 round-4 #5a: per-extract ``_merge_subsumed_rules``
    # only collapses rules emitted by *this* run.  When a previous
    # extract left a narrower sibling rule active in the store and the
    # current run produces (or refreshes) a broader one, the narrow
    # rule lingers in ``GET /patterns/rules`` and Mümin sees two
    # overlapping rules where one would suffice (218126 had
    # ``adults gte 1.5, hours gte -1185.65`` and
    # ``adults gte 2.0, hours gte -381.66`` co-existing — the latter is
    # strictly covered by the former).  After contradictions and
    # invalidations are persisted, sweep the now-active set across
    # ``(scope, scope_id, scenario)`` and deactivate any rule a sibling
    # already covers.
    if hasattr(rule_store, "get_active_rules") and hasattr(
        rule_store,
        "deactivate",
    ):
        try:
            active_rules = await rule_store.get_active_rules(
                scenario=scenario,
                scope=PatternScope.PROPERTY,
                scope_id=payload.property_id,
            )
            kept_rules = _merge_subsumed_rules(active_rules)
            kept_ids = {rule.pattern_id for rule in kept_rules}
            for rule in active_rules:
                if rule.pattern_id in kept_ids:
                    continue
                await rule_store.deactivate(rule.pattern_id)
                logger.info(
                    "extract.rule_subsumed",
                    pattern_id=rule.pattern_id,
                    scenario=rule.scenario.value,
                    scope_id=rule.scope_id,
                )
        except Exception:
            logger.warning(
                "extract.cross_db_subsumption_failed",
                exc_info=True,
                property_id=payload.property_id,
                scenario=scenario.value,
            )

    return ExtractPatternsResponse(
        rules_extracted=sum(1 for r in validated_rules if r["valid"]),
        total_cases=result.total_cases,
        positive_cases=result.positive_cases,
        negative_cases=result.negative_cases,
        defer_count=result.defer_count,
        skipped_reasons=list(result.skipped_reasons),
        gate_decisions=[
            GateDecisionDTO(
                action=g.action,
                accepted=g.accepted,
                reason=g.reason,
                support_count=g.support_count,
                counterexample_count=g.counterexample_count,
                confidence=g.confidence,
                min_support=g.min_support,
                min_confidence=g.min_confidence,
            )
            for g in result.gate_decisions
        ],
        rules=validated_rules,
    )


@router.get("/patterns/rules", response_model=ActiveRulesResponse)
async def get_active_rules(
    http_request: Request,
    scenario: str | None = None,
    scope: str | None = None,
    scope_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ActiveRulesResponse:
    """Get active PatternRules for a given scope.

    Mümin 2026-05-08 round-4 #3: ``total`` is the unfiltered count of
    matching active rules; the response slices ``[offset:offset+limit]``
    after the store's existing confidence-DESC ordering so callers can
    paginate without scanning every page.  Slicing is done in Python
    because the rule cardinality per scope is small (typically <50)
    and threading offset/limit through the store layer would force
    a Protocol change shared with bootstrap callers that expect the
    full active set.

    Mümin 2026-05-15 round-5 #1: each rule now also carries
    ``foundation_scenario_id`` plus the Excel ``stage_group`` and
    ``stage_excel`` strings derived from the foundation catalog, so
    the listing matches ``FOUNDATION_469_SCENARIOS.xlsx`` without a
    separate ``/origin`` round-trip.  Legacy / cross-scenario rules
    that pre-date PR #288 emit ``null`` for all three new fields.
    """
    s = Scenario(scenario) if scenario else None
    sc = PatternScope(scope) if scope else None

    rule_store = _resolve_rule_store(http_request)
    rules = await rule_store.get_active_rules(
        scenario=s,
        scope=sc,
        scope_id=scope_id,
    )
    total = len(rules)
    page = rules[offset : offset + limit]

    catalog_lookup = await _build_catalog_lookup_for_rules(
        http_request,
        page,
    )

    return ActiveRulesResponse(
        rules=[_project_rule(r, catalog_lookup) for r in page],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + len(page)) < total,
    )


async def _build_catalog_lookup_for_rules(
    http_request: Request,
    rules: list[PatternRule],
) -> dict[str, Any]:
    """Return a ``{scenario_id: FoundationScenario}`` dict for the page.

    Returns the empty dict when the foundation catalog store is not
    wired (it is optional in dev / unit-test paths) or when none of
    the supplied rules carries a ``foundation_scenario_id``.
    Otherwise issues one ``list_all()`` call and builds the in-memory
    index — cheaper than N async ``get()`` round-trips for the same
    page and matches the pattern already used by
    :func:`brain_engine.patterns.foundation_catalog_store.compute_forbidden_foundation_ids`.
    """
    needed: set[str] = {
        r.foundation_scenario_id for r in rules if r.foundation_scenario_id
    }
    if not needed:
        return {}
    catalog_store = _resolve_foundation_catalog_store(http_request)
    if catalog_store is None:
        return {}
    list_all = getattr(catalog_store, "list_all", None)
    if list_all is None:
        return {}
    try:
        rows = await list_all()
    except (AttributeError, RuntimeError, ConnectionError) as exc:
        logger.warning(
            "rules_listing.catalog_lookup_failed error=%s",
            exc,
            exc_info=True,
        )
        return {}
    return {row.scenario_id: row for row in rows if row.scenario_id in needed}


def _project_rule(
    rule: PatternRule,
    catalog_lookup: dict[str, Any],
) -> dict[str, Any]:
    """Render a :class:`PatternRule` into the listing's dict shape.

    Adds three fields beyond the legacy projection:

    * ``foundation_scenario_id`` — the dominant slug copied from
      :pyattr:`PatternRule.foundation_scenario_id`, or ``None`` for
      legacy rules.
    * ``stage_group`` — Excel ``Stage Group`` long form
      (``"Stage N — <label>"``) derived from the catalog row.
      ``None`` when the slug is missing or the catalog has no
      matching row.
    * ``stage_excel`` — Excel ``Stage`` short form (e.g.
      ``"Booking confirmation"``).  ``None`` when unavailable.

    The legacy ``stage`` field remains the
    :class:`~brain_engine.patterns.models.BookingStage` enum value so
    callers reading the pre-W5 projection keep their behaviour.
    """
    foundation_id = rule.foundation_scenario_id
    catalog_row: Any = (
        catalog_lookup.get(foundation_id) if foundation_id else None
    )
    if catalog_row is not None:
        stage_number = getattr(catalog_row, "stage_number", None)
        stage_label = getattr(catalog_row, "stage_label", "")
        stage_group = format_stage_group(stage_number, stage_label) or None
        stage_excel = lookup_stage_short(stage_number, stage_label) or None
    else:
        stage_group = None
        stage_excel = None
    return {
        "pattern_id": rule.pattern_id,
        "scenario": rule.scenario.value,
        "scope": rule.scope.value,
        "scope_id": rule.scope_id,
        "confidence": rule.confidence,
        "risk_level": rule.risk_level.value,
        "stage": rule.stage.value if rule.stage is not None else None,
        "stage_group": stage_group,
        "stage_excel": stage_excel,
        "foundation_scenario_id": foundation_id,
        "execution_mode": rule.execution_mode.value,
        "support_count": rule.support_count,
        "counterexample_count": rule.counterexample_count,
        "conditions": rule.conditions,
        "action_type": rule.action.action_type.value,
        "rationale": rule.rationale,
        "active": rule.active,
        "created_at": rule.created_at.isoformat(),
    }


@router.get("/patterns/scenarios", response_model=ScenariosResponse)
async def list_scenarios_with_rules(
    http_request: Request,
    scope_id: str | None = None,
    scope: str | None = None,
) -> ScenariosResponse:
    """List scenarios that have at least one active rule in a scope.

    Mümin 2026-05-08 round-4 #2: a UI filter dropdown needs to know
    *which* scenarios exist for an ``(org_id, property_id)`` pair
    without scanning every rule.  This endpoint groups
    ``rule_store.get_active_rules`` output by ``scenario`` and surfaces
    the per-scenario count plus the freshest ``last_seen_at`` anchor.

    Defaults ``scope`` to ``PROPERTY`` since that's the bucket Mümin's
    UI cares about; explicit ``scope`` values from
    :class:`PatternScope` (``GLOBAL``, ``OWNER``, ``PROPERTY``) are
    still honoured.
    """
    sc = PatternScope(scope) if scope else PatternScope.PROPERTY

    rule_store = _resolve_rule_store(http_request)
    rules = await rule_store.get_active_rules(scope=sc, scope_id=scope_id)

    grouped: dict[str, list[PatternRule]] = {}
    for rule in rules:
        grouped.setdefault(rule.scenario.value, []).append(rule)

    stats = [
        ScenarioStatsDTO(
            scenario=scenario_value,
            rule_count=len(scenario_rules),
            last_seen_at=max(
                r.last_seen_at for r in scenario_rules
            ).isoformat(),
        )
        for scenario_value, scenario_rules in sorted(grouped.items())
    ]

    return ScenariosResponse(scenarios=stats, total=len(stats))


# ---------------------------------------------------------------------------
# Rule origin (FL-12 — Ali's Turkish requirement #1)
# ---------------------------------------------------------------------------


class FoundationScenarioRef(BaseModel):
    """One foundation catalog entry referenced from a rule's origin.

    Returned inside :class:`RuleOriginResponse` for every entry of
    :pyattr:`PatternOrigin.foundation_scenario_ids`.  When the
    foundation catalog store is wired into the app, ``title``,
    ``stage_number``, and ``risk_level`` come from the catalog row
    so the UI can render a readable trail.  When the catalog store
    is absent (early bootstrap, tests with no wiring) the response
    still carries the ``scenario_id`` slug so the trail is never
    empty — the renderer can fall back to the slug.
    """

    scenario_id: str = Field(
        ...,
        description="Deterministic slug from the foundation registry.",
    )
    title: str = Field(
        default="",
        description="Human-readable scenario title when the catalog store is wired.",
    )
    stage_number: int | None = Field(
        default=None,
        description="1—9 hospitality stage number when known.",
    )
    risk_level: str = Field(
        default="",
        description="Foundation risk level (Low/Medium/High/Critical) when known.",
    )


class RuleOriginResponse(BaseModel):
    """Full provenance trail for one :class:`PatternRule`.

    Powers ``GET /patterns/rules/{rule_id}/origin``.  Ali's Turkish
    requirement #1 reads: *"Oluşturduğumuz rule hangi foundationa göre
    oluşturuldu bunu loglayan bir yapı kurmak mantıklı."*  This
    response object renders the answer: which foundation scenarios,
    upstream events, and proactive signals contributed to the rule.
    """

    rule_id: str = Field(..., description="The ``pattern_id`` of the rule.")
    foundation_scenarios: list[FoundationScenarioRef] = Field(
        default_factory=list,
        description="Foundation catalog rows that contributed to the rule.",
    )
    source_event_ids: list[str] = Field(
        default_factory=list,
        description="Upstream event identifiers (messages, PMS, vendor, …).",
    )
    contributing_signal_ids: list[str] = Field(
        default_factory=list,
        description="Proactive signal identifiers (empty until FL-09 lands).",
    )
    foundation_scenario_id: str | None = Field(
        default=None,
        description=(
            "FL-03 singular dominant foundation reference; ``None`` when "
            "the orchestrator has not classified the rule."
        ),
    )


def _resolve_foundation_catalog_store(request: Request) -> object | None:
    """Return the wired ``FoundationCatalogStore``, or ``None``.

    The store is optional from the API endpoint's perspective —
    Sprint 1 deploys do not yet wire it into ``app.state`` and the
    endpoint must keep returning a useful response (the slug list)
    rather than a 500 when the store is absent.  The duck-typed
    object only needs an ``async get(scenario_id)`` method.
    """
    return getattr(request.app.state, "foundation_catalog_store", None)


@router.get(
    "/patterns/rules/{rule_id}/origin",
    response_model=RuleOriginResponse,
)
async def get_rule_origin(
    rule_id: str,
    http_request: Request,
) -> RuleOriginResponse:
    """Return the foundation / event / signal trail for one rule.

    Closes Ali's Turkish requirement #1: every PatternRule must
    trace back to the foundation scenarios that birthed it.  The
    handler:

    1. Resolves the rule via the wired ``PatternRuleStore``.  A
       missing rule yields a 404 so the UI can distinguish a stale
       deep-link from a broken endpoint.
    2. Reads the rule's :class:`PatternOrigin`.  When the trail is
       empty (legacy rules mined before FL-16 lands) the response
       still carries the ``rule_id`` and the singular
       ``foundation_scenario_id`` from FL-03 — the UI's empty-state
       can fall back to that.
    3. Optionally enriches each foundation slug with the title and
       risk level from the foundation catalog store, when one is
       wired into ``app.state``.  The endpoint gracefully degrades
       to slug-only output when the catalog is absent.

    Args:
        rule_id: ``pattern_id`` of the rule.
        http_request: FastAPI request — used to resolve injected
            stores.

    Returns:
        Pydantic response with the full provenance trail.

    Raises:
        HTTPException: ``404`` if ``rule_id`` does not exist.
    """
    rule_store = _resolve_rule_store(http_request)
    rule = await rule_store.get(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule {rule_id} not found",
        )

    catalog_store = _resolve_foundation_catalog_store(http_request)
    foundation_refs: list[FoundationScenarioRef] = []
    for scenario_id in rule.origin.foundation_scenario_ids:
        ref = await _build_foundation_ref(scenario_id, catalog_store)
        foundation_refs.append(ref)

    return RuleOriginResponse(
        rule_id=rule.pattern_id,
        foundation_scenarios=foundation_refs,
        source_event_ids=list(rule.origin.source_event_ids),
        contributing_signal_ids=list(rule.origin.contributing_signal_ids),
        foundation_scenario_id=rule.foundation_scenario_id,
    )


async def _build_foundation_ref(
    scenario_id: str,
    catalog_store: object | None,
) -> FoundationScenarioRef:
    """Enrich a foundation slug with title + risk when possible.

    Degrades to the slug-only ``FoundationScenarioRef`` when the
    catalog store is not wired or when the slug is unknown to the
    catalog (e.g. an MD edit renamed it).  This keeps the endpoint
    useful even before FL-16 wires the catalog store into the app
    factory.
    """
    if catalog_store is None:
        return FoundationScenarioRef(scenario_id=scenario_id)
    try:
        scenario = await catalog_store.get(scenario_id)  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        return FoundationScenarioRef(scenario_id=scenario_id)
    if scenario is None:
        return FoundationScenarioRef(scenario_id=scenario_id)
    return FoundationScenarioRef(
        scenario_id=scenario_id,
        title=getattr(scenario, "title", ""),
        stage_number=getattr(scenario, "stage_number", None),
        risk_level=getattr(scenario, "risk_level", ""),
    )


# ---------------------------------------------------------------------------
# Blocker endpoints
# ---------------------------------------------------------------------------


@router.get("/blockers/active")
async def get_active_blockers(
    http_request: Request,
    property_id: str,
    reservation_id: str | None = None,
) -> dict[str, Any]:
    """Get active blockers for a property/reservation."""
    blocker_engine = _resolve_blocker_engine(http_request)
    blockers = await blocker_engine.get_active_blockers(
        property_id,
        reservation_id,
    )
    return {
        "blockers": [
            {
                "blocker_id": b.blocker_id,
                "blocker_type": b.blocker_type.value,
                "severity": b.severity.value,
                "description": b.description,
                "is_hard": b.is_hard,
                "created_at": b.created_at.isoformat(),
                "blocks_actions": [a.value for a in b.blocks_actions],
            }
            for b in blockers
        ],
        "total": len(blockers),
        "has_hard_blockers": any(b.is_hard for b in blockers),
    }


@router.post(
    "/blockers/create",
    response_model=BlockerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_blocker(
    payload: CreateBlockerRequest,
    http_request: Request,
) -> BlockerResponse:
    """Create a new blocker for a property/reservation."""
    try:
        blocker_type = BlockerType(payload.blocker_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid blocker type: {exc}",
        ) from exc

    severity = BlockerSeverity(payload.severity) if payload.severity else None

    blocker_engine = _resolve_blocker_engine(http_request)
    blocker = await blocker_engine.create_blocker(
        blocker_type=blocker_type,
        property_id=payload.property_id,
        description=payload.description,
        reservation_id=payload.reservation_id,
        severity=severity,
        metadata=payload.metadata,
    )

    return BlockerResponse(
        blocker_id=blocker.blocker_id,
        blocker_type=blocker.blocker_type.value,
        severity=blocker.severity.value,
        property_id=blocker.property_id,
        reservation_id=blocker.reservation_id,
        description=blocker.description,
        is_active=blocker.is_active,
        created_at=blocker.created_at.isoformat(),
    )


@router.post("/blockers/resolve")
async def resolve_blocker(
    payload: ResolveBlockerRequest,
    http_request: Request,
) -> dict[str, Any]:
    """Resolve an active blocker."""
    blocker_engine = _resolve_blocker_engine(http_request)
    resolved = await blocker_engine.resolve_blocker(
        payload.blocker_id,
        payload.resolved_by,
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Blocker {payload.blocker_id} not found.",
        )
    return {"resolved": True, "blocker_id": payload.blocker_id}


# ---------------------------------------------------------------------------
# Calendar endpoints
# ---------------------------------------------------------------------------


@router.post("/calendar/gaps", response_model=GapAnalysisResponse)
async def analyze_gaps(payload: GapAnalysisRequest) -> GapAnalysisResponse:
    """Analyse calendar gaps for a property."""
    gaps = _calendar_evaluator.analyze_gaps(
        payload.calendar_data,
        payload.property_id,
        payload.min_stay,
    )
    return GapAnalysisResponse(
        gaps=[
            {
                "gap_start": str(g.gap_start),
                "gap_end": str(g.gap_end),
                "gap_nights": g.gap_nights,
                "is_orphan": g.is_orphan,
                "sellability_score": g.sellability_score,
                "value_if_filled": round(g.value_if_filled, 3),
            }
            for g in gaps
        ],
        total_gaps=len(gaps),
        orphan_gaps=sum(1 for g in gaps if g.is_orphan),
    )


# ---------------------------------------------------------------------------
# Backwards-compatible aliases
# ---------------------------------------------------------------------------
#
# A handful of older import sites (notably extension scripts) imported
# the bare ``_case_store`` / ``_rule_store`` / ``_extractor`` /
# ``_blocker_engine`` symbols directly off this module.  The wired
# stores now live behind resolvers that need a ``Request`` to read
# ``app.state``, so these globals can no longer be authoritative.
# Aliasing them to the in-memory fallbacks preserves attribute access
# for those legacy imports without affecting routed traffic.
_case_store = _fallback_case_store
_rule_store = _fallback_rule_store
_blocker_store = _fallback_blocker_store
_blocker_engine = BlockerEngine(store=_fallback_blocker_store)
_extractor = PatternExtractor(store=_fallback_case_store)


def _outcome_from_payload(
    payload: LogDecisionRequest,
) -> CaseOutcome | None:
    """Construct a :class:`CaseOutcome` from optional request fields.

    Returns ``None`` when the payload carries no outcome signal so
    the default empty outcome is used downstream.  When at least one
    of ``resolution_type``, ``successful``, ``approved``, or
    ``revenue_impact`` is supplied, all four are folded into a single
    :class:`CaseOutcome` ready for persistence.

    Invalid ``resolution_type`` strings raise ``HTTPException(422)``
    so the API surface degrades cleanly instead of silently storing
    an unlearnable case.
    """
    fields_present = (
        payload.resolution_type is not None
        or payload.successful is not None
        or payload.approved is not None
        or payload.revenue_impact is not None
    )
    if not fields_present:
        return None
    resolution: ResolutionType | None = None
    if payload.resolution_type is not None:
        try:
            resolution = ResolutionType(payload.resolution_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid resolution_type: {exc}",
            ) from exc
    return CaseOutcome(
        successful=payload.successful,
        approved=payload.approved,
        resolution_type=resolution,
        revenue_impact=payload.revenue_impact,
    )
