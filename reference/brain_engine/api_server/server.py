"""FastAPI AG-UI protocol server for the Airbnb Brain Engine.

Exposes a single POST / endpoint that accepts RunAgentInput and returns
a Server-Sent Events stream following the AG-UI protocol specification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from api_server.bootstrap.autonomy import wire as wire_autonomy
from api_server.bootstrap.collab import wire as wire_collab
from api_server.bootstrap.decision_case import (
    wire as wire_decision_case,
)

# Botel PMS bootstrap retired 2026-04-28 — Brain Engine reads only
# from the unified onboarding-api GraphQL gateway.  Import kept out
# of the import block so server startup no longer touches the
# legacy module.
from api_server.bootstrap.elasticsearch import wire as wire_elasticsearch
from api_server.bootstrap.elevenlabs import wire as wire_elevenlabs
from api_server.bootstrap.evidence import wire as wire_evidence
from api_server.bootstrap.experiments import (
    wire as wire_experiments,
)
from api_server.bootstrap.interview import wire as wire_interview
from api_server.bootstrap.memory import wire as wire_memory
from api_server.bootstrap.narrative import wire as wire_narrative
from api_server.bootstrap.negotiation import wire as wire_negotiation
from api_server.bootstrap.onboarding import wire as wire_onboarding
from api_server.bootstrap.ops_logger import wire as wire_ops_logger
from api_server.bootstrap.pattern_rule import (
    wire as wire_pattern_rule,
)
from api_server.bootstrap.reasoning import wire as wire_reasoning
from api_server.bootstrap.telegram_bot import wire as wire_telegram_bot
from api_server.bootstrap.temporal_analysis import (
    wire as wire_temporal_analysis,
)
from api_server.bootstrap.unified_data import wire as wire_unified_data
from api_server.bootstrap.voice import wire as wire_voice
from api_server.reservation_merger import (
    merge_calendars,
    merge_reservation_contexts,
)
from api_server.middleware import setup_middleware
from api_server.schemas import (
    AgentState,
    Role,
    RunAgentInput,
)
from brain_engine.analysis.orchestrator import (
    FoundationAnalysisOrchestrator,
)
from brain_engine.api import mockup_loader
from brain_engine.api.card_endpoints import (
    router as card_router,
)
from brain_engine.api.cendra_adapter import _deps, configure_dependencies
from brain_engine.api.cendra_adapter import router as cendra_router
from brain_engine.api.conversation_memory import (
    load_conversation_history,
    save_conversation_turn,
)
from brain_engine.api.intelligence_endpoints import (
    router as intelligence_router,
)
from brain_engine.api.interview_endpoints import (
    router as interview_router,
)
from brain_engine.api.bootstrap_intent_endpoints import (
    configure_bootstrap_intent_deps,
)
from brain_engine.api.bootstrap_intent_endpoints import (
    router as bootstrap_intent_router,
)
from brain_engine.api.memory_endpoints import router as memory_router
from brain_engine.api.onboarding_endpoints import (
    configure_onboarding_deps,
)
from brain_engine.api.onboarding_endpoints import (
    router as onboarding_router,
)
from brain_engine.api.pattern_endpoints import router as pattern_router
from brain_engine.api.profile_endpoints import (
    configure_profile_deps,
)
from brain_engine.api.profile_endpoints import (
    router as profile_router,
)
from brain_engine.api.team_endpoints import (
    router as team_router,
)
from brain_engine.api.temporal_analysis_endpoints import (
    router as temporal_analysis_router,
)
from brain_engine.api.workflow_endpoints import configure_workflow_deps
from brain_engine.api.workflow_endpoints import router as workflow_router
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.approval.models import ActionType
from brain_engine.approval.notifier import TelegramApprovalNotifier
from brain_engine.autonomy import (
    AutonomyEngine,
    TrustMeterService,
    TrustMeterView,
)
from brain_engine.autonomy.engine import AutonomyStore
from brain_engine.blockers.engine import BlockerEngine, BlockerStore
from brain_engine.cards import CardStore
from brain_engine.causal import (
    CausalChain,
    CausalEdge,
    CausalGraph,
    CausalNavigationError,
    CausalNavigationService,
)
from brain_engine.causal import (
    event_key as causal_event_key,
)
from brain_engine.conversation.models import (
    CalendarDay,
    ConversationMessage,
    ConversationRequest,
    ReservationContext,
    SenderType,
)
from brain_engine.conversation.pm_facts import (
    InMemoryPmFactStore,
    PgPmFactStore,
    PmFactStore,
)
from brain_engine.conversation.regenerate_service import (
    set_pm_fact_store,
)
from brain_engine.conversation.service import ConversationService
from brain_engine.evidence import (
    EvidenceBundle,
    EvidenceError,
    EvidenceQuery,
    EvidenceService,
)
from brain_engine.exceptions import (
    ApprovalNotFoundError,
)
from brain_engine.experiments.ab_test_engine import ExperimentRegistry
from brain_engine.experiments.store import ExperimentStore
from brain_engine.fallback.config_validator import ConfigValidator
from brain_engine.fallback.fallback_chain import (
    build_cleaner_fallback_chain,
)
from brain_engine.fallback.gap_resolver import GapResolver, GapType
from brain_engine.gestures.prompts import MemoryPromptAggregator
from brain_engine.guest_intelligence.benefit_recommender import (
    BenefitRecommender,
)
from brain_engine.guest_intelligence.loyalty_scorer import LoyaltyScorer
from brain_engine.guest_intelligence.profile_builder import GuestProfileBuilder
from brain_engine.guest_intelligence.risk_flag import RiskFlagSystem
from brain_engine.integrations.messaging.telegram_bot import TelegramBot
from brain_engine.integrations.unified_data import (
    GraphqlPmsFetcher,
    UnifiedDataGraphQLClient,
    fetch_calendar_window,
    fetch_reservation_context,
)
from brain_engine.integrations.voice.elevenlabs import (
    ElevenLabsClient,
)
from brain_engine.interview import (
    InterviewAnswerStore,
    InterviewEngine,
    VoiceTranscriber,
)
from brain_engine.memory.factory import (
    FullSystem,
    MemorySystem,
    create_full_system,
)
from brain_engine.narrative import (
    NarrativeError,
    NarrativeService,
    RenderStyle,
    TimelineRange,
    VoiceSynthesisUnavailable,
)
from brain_engine.negotiation import (
    NegotiationOffer,
    NegotiationSessionManager,
    NegotiationTarget,
    VendorChannelRegistry,
)
from brain_engine.onboarding import (
    OnboardingError,
    OnboardingRequest,
    OnboardingService,
)
from brain_engine.orchestrator import (
    ExecutionOrchestrator,
    build_execution_orchestrator,
)
from brain_engine.owner_profile import (
    InMemoryOwnerProfileStore,
    OwnerProfileStore,
    PgOwnerProfileStore,
)
from brain_engine.patterns.foundation_catalog_store import (
    InMemoryFoundationCatalogStore,
)
from brain_engine.patterns.foundation_registry import (
    compute_doc_hash,
    load_foundation_examples,
    load_foundation_scenarios,
)
from brain_engine.patterns.intelligent_classifier_factory import (
    DEFAULT_FOUNDATION_PATH,
)
from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger
from brain_engine.patterns.router import PatternRuleRouter
from brain_engine.patterns.scenario_matcher import ScenarioMatcher
from brain_engine.patterns.store import DecisionCaseStore, PatternRuleStore
from brain_engine.patterns.wiring import CloseCallable
from brain_engine.preferences.enforcer import PolicyEnforcer
from brain_engine.preferences.learner import PreferenceLearner
from brain_engine.preferences.store import PreferenceStore
from brain_engine.profiles import (
    InMemoryPropertyProfileStore,
    PgPropertyProfileStore,
    PropertyProfileStore,
)
from brain_engine.sandbox import (
    ExampleReplyGenerator,
    InMemoryUnansweredThreadStore,
    LLMExampleReplyGenerator,
    PgUnansweredThreadStore,
    TemplateExampleReplyGenerator,
    UnansweredThreadStore,
)
from brain_engine.scheduler import NightlyScheduler
from brain_engine.staticity import StaticityClassifier, StaticityGuard
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter
from brain_engine.streaming.current_emitter import (
    reset_current_emitter,
    set_current_emitter,
)
from brain_engine.streaming.event_types import EventType
from brain_engine.team import (
    HandoffStore,
    MentionStore,
)
from config.settings import Settings

logger = logging.getLogger(__name__)

# Structured logger for the C8.2 reservation-context provenance
# diagnostic.  ``server.py`` historically wires only stdlib
# logging, which defaults to WARNING and silently drops every
# INFO emitted from this module — verified on dev pod
# ``brain-engine-d54f889c6-mljhz`` (PR #322 follow-up).  Routing
# the single diagnostic through structlog matches the format the
# rest of the platform already emits (e.g.
# ``brain_engine/integrations/unified_data/pms_fetcher.py:54``)
# and lands the line in stdout so ``kubectl logs`` can grep it.
_c8_2_diag_log = structlog.get_logger("c8_2_diagnostic")

# ── Global state managed across lifespan ──────────────────────────────────────
_settings: Settings | None = None
_brain_engine_ready: bool = False
_elevenlabs_client: ElevenLabsClient | None = None
_telegram_bot: TelegramBot | None = None
_memory: MemorySystem | None = None
# Mümin 2026-05-13 (PR #F): one shared fan-out across every
# DecisionCase write path (bootstrap, live, regenerate, ...).
# Built once in lifespan, injected wherever cases land.
_memory_fanout: Any = None

# ── New systems (Phases 1-4) ─────────────────────────────────────────────
_approval_gateway: ApprovalGateway | None = None
_approval_notifier: TelegramApprovalNotifier | None = None
_preference_store: PreferenceStore | None = None
_preference_learner: PreferenceLearner | None = None
_policy_enforcer: PolicyEnforcer | None = None
_config_validator: ConfigValidator | None = None
_gap_resolver: GapResolver | None = None
_guest_profile_builder: GuestProfileBuilder | None = None
_loyalty_scorer: LoyaltyScorer | None = None
_benefit_recommender: BenefitRecommender | None = None
_risk_flag_system: RiskFlagSystem | None = None

# ── Smart Engine (singleton) ─────────────────────────────────────────
_scoring_engine: Any = None
_city_knowledge: Any = None
_full_system: FullSystem | None = None

# ── DecisionCase store (Stage 2-C wiring) ────────────────────────────
# Built once in lifespan via build_decision_case_store(); the close
# callable is awaited on shutdown so pool-owning backends release their
# asyncpg pool cleanly.  Backend is selected by the env var
# DECISION_CASE_STORE_BACKEND (memory | dual | postgres).
_case_store: DecisionCaseStore | None = None
_case_store_close: CloseCallable | None = None

# ── PatternRule store + router (Fix #4b — runtime rule plumbing) ──────
# Same lifecycle contract as _case_store: built once in lifespan,
# closed on shutdown.  The router is a thin stateless wrapper injected
# into ConversationService so learned rules can influence live
# conversations once a feature dict is available (Fix #4c).
_rule_store: PatternRuleStore | None = None
_rule_store_close: CloseCallable | None = None
_rule_router: PatternRuleRouter | None = None

# ── A/B experiment store + registry ─────────────────────────────────
# Same lifecycle contract as the stores above.  ``_experiment_store``
# satisfies :class:`ExperimentStore`; ``_experiment_registry`` is the
# in-process consumer fed by the durable store on warm-up.  Closed
# on shutdown when the factory owned the asyncpg pool.
_experiment_store: ExperimentStore | None = None
_experiment_store_close: CloseCallable | None = None
_experiment_registry: ExperimentRegistry | None = None

# ── Ops DecisionCase logger (Gap #1 — ops-autonomy learning) ──────────
# Thin façade over _case_store.  Built in lifespan, passed into ops
# flows so cleaner fallbacks, vendor dispatches, negotiations and
# quality-acceptance events become DecisionCases.  A None case_store
# makes the logger a no-op, so ops flows can always hold a reference.
_ops_logger: OpsDecisionLogger | None = None

# ── Negotiation session manager (Gap #4 part 4 — HTTP entry point) ──────
# Lifespan-scoped singleton that owns in-flight negotiation sessions.
# Each session runs :meth:`Negotiator.negotiate` as a background task
# and exposes :meth:`feed_reply` so webhook handlers can push parsed
# counterparty text back into the orchestrator's receive side.  The
# manager persists nothing — crash recovery is deliberately not in
# scope; authoritative outcomes live in the DecisionCase store.
_negotiation_manager: NegotiationSessionManager | None = None

# ── Vendor channel registry (Gap #4 follow-up — per-vendor transport) ──
# Maps vendor_name → transport spec (telegram chat id, WhatsApp phone,
# or log-only) so the session manager can auto-resolve a SendText
# callable for every negotiation without the caller wiring one by hand.
# Populated at runtime via POST /api/ops/vendor-channel.
_vendor_channels: VendorChannelRegistry | None = None

# ── Narrative service (Gap #2 — property timeline text + voice) ─────────
# Composes events from DecisionCaseStore and GuestHistoryStore, renders
# them as deterministic text (optionally rewritten by an LLM) and pipes
# the result into ElevenLabs when voice output is requested.  Wired in
# lifespan once its dependencies are ready; read by the
# ``/api/memory/property/{property_id}/timeline`` endpoint.
_narrative_service: NarrativeService | None = None

# ── Unified GraphQL data client (Gap #2 cross-provider history) ────────
# Optional adapter that fans out to the Cendra onboarding-api
# ``reservations`` GraphQL field so the timeline can blend cross-provider
# bookings (Hostaway, Lodgify, Calry, Channex, Guesty, WhatsApp) on top
# of the in-house DecisionCase + GuestHistory sources.  Construction is
# env-var driven (UNIFIED_DATA_*) and fails soft so a missing customerId
# simply omits the source instead of breaking startup.  The httpx client
# is owned here and aclosed on shutdown.
_unified_data_client: UnifiedDataGraphQLClient | None = None

# ── Property profile store (onboarding step 5 — "what Brain knows") ─────
# Holds aggregate PropertyProfile snapshots built by the
# PropertyProfileHarvester during the V2 bootstrap pipeline.  A default
# in-memory store is always installed so the knowledge endpoint returns
# a deterministic 404 for unknown properties rather than 503.  When the
# onboarding-api GraphQL client is configured, the harvester is attached
# to the bootstrap pipeline and starts filling this store on every run.
_property_profile_store: PropertyProfileStore = InMemoryPropertyProfileStore()
# Held when ``PROPERTY_PROFILE_STORE_BACKEND=postgres`` so shutdown can
# release the asyncpg pool symmetrically to the other Postgres-backed
# stores.  Default ``None`` = in-memory store, no pool to release.
_property_profile_store_close: Any = None

# ── Owner flexibility profile store (§10 preference tier) ───────────────
# Default in-memory store keeps unit tests + dev shells working without
# Postgres; lifespan swaps in :class:`PgOwnerProfileStore` when
# ``OWNER_PROFILE_STORE_BACKEND=postgres`` is set.  Backed by migration
# 014 (``owner_flexibility_profiles``).  A misconfigured Postgres setup
# is non-fatal — the lifespan logs a warning and keeps the in-memory
# default so the orchestrator preference tier still has a store to call.
_owner_profile_store: OwnerProfileStore = InMemoryOwnerProfileStore()
_owner_profile_store_close: Any = None
# Built once in lifespan after every backing store is wired (owner
# profile + blocker + pattern + staticity).  Held here so the
# conversation endpoint factory can inject the same orchestrator into
# every :class:`ConversationService` it creates without re-walking the
# wiring contract.  ``None`` keeps the legacy LLM-only path active.
_execution_orchestrator: ExecutionOrchestrator | None = None

# ── PM-confirmed knowledge store (PM Chat correction loop) ──────────────
# Persists every PM reply that fills a knowledge gap (WiFi password,
# parking rules, late-checkout decisions, …) so the next guest message
# gets the answer instead of the AI repeating the original BRAIN flag.
# A default in-memory store is always installed so unit tests and the
# dev path keep working without a Postgres connection; the lifespan
# swaps in :class:`PgPmFactStore` when ``PM_FACT_STORE_BACKEND=postgres``.
_pm_fact_store: PmFactStore = InMemoryPmFactStore()
# Held when the Postgres backend is selected so shutdown can release
# the asyncpg pool symmetrically to the other Postgres-backed stores.
_pm_fact_store_close: Any = None

# ── Unanswered-thread sandbox store + generator (onboarding step 12) ────
# Collects guest threads whose last message is still awaiting a PM reply
# and caches the AI-generated example reply the PM can approve, edit, or
# discard.  The default ``TemplateExampleReplyGenerator`` always ships so
# the sandbox endpoint never 503s — production deployments can swap it
# out for an LLM-backed generator by reassigning ``_sandbox_generator``
# before the bootstrap pipeline is constructed.
_unanswered_thread_store: UnansweredThreadStore = InMemoryUnansweredThreadStore()
_sandbox_generator: ExampleReplyGenerator = TemplateExampleReplyGenerator()
# Held when ``SANDBOX_STORE_BACKEND=postgres`` so shutdown can release
# the asyncpg pool symmetrically to the other Postgres-backed stores.
_sandbox_store_close: Any = None

# ── Evidence service (GAP L — decision evidence read model) ─────────────
# Fans out across PatternRuleStore, DecisionCaseStore, BlockerStore and
# MemoryPromptAggregator to assemble EvidenceBundle objects for the
# ``/api/decisions/{decision_id}/evidence`` endpoint.  The service is
# safe to call with a subset of adapters because missing ones just
# contribute empty pick tuples.
_evidence_service: EvidenceService | None = None

# ── Blocker + prompt infrastructure (GAP L follow-up) ────────────────────
# In-memory defaults feed the evidence composer while persistent
# implementations are still being designed.  They are singletons so
# that future lifespan commits can swap the backing implementation
# without touching the evidence wiring.
_blocker_store: BlockerStore | None = None
_prompt_aggregator: MemoryPromptAggregator | None = None

# ── Autonomy + Trust Meter (V2 — per-workflow OBSERVE→AUTOPILOT) ────────
# AutonomyEngine owns per-(property, workflow) state.  TrustMeterService
# is a read-only projection that the V2 wireframe band consumes via
# ``GET /v2/properties/{property_id}/trust-meter``.  Backend is selected
# by AUTONOMY_STORE_BACKEND ("memory" | "postgres", default "memory");
# Postgres URL falls back to DATABASE_URL.  A misconfigured Postgres
# setup is non-fatal — lifespan logs a warning and reverts to the
# in-memory store, so the endpoint stays up (losing only durability).
_autonomy_store: AutonomyStore | None = None
_autonomy_store_close: Any = None
_autonomy_engine: AutonomyEngine | None = None
_trust_meter_service: TrustMeterService | None = None

# ── Interview engine (V2 — proactive PM Q&A) ────────────────────────────
# Owns the InterviewAnswerStore (in-memory or Postgres) and the
# InterviewEngine that surfaces the next question to ask.  Backend is
# selected by INTERVIEW_STORE_BACKEND ("memory" | "postgres", default
# "memory") with the URL falling back to DATABASE_URL.  A misconfigured
# Postgres setup is non-fatal: the lifespan reverts to the in-memory
# store so the four interview endpoints stay reachable (losing only
# durability across restarts).
_interview_store: InterviewAnswerStore | None = None
_interview_store_close: Any = None
_interview_engine: InterviewEngine | None = None
_voice_transcriber: VoiceTranscriber | None = None
_voice_transcriber_close: Any = None

# ── Decision card store (V2 — five-slot UI artefact lifecycle) ──────────
# Holds proposed cards through PENDING → CONFIRMED/DISMISSED/EXPIRED.
# Wired in lifespan.  Default backend is InMemoryCardStore; setting
# CARD_STORE_BACKEND=postgres swaps in PgCardStore against the
# ``decision_cards`` table provisioned by migration 005.  The close
# callable is stashed at module scope so the shutdown path can
# release the pool symmetrically to the other Postgres-backed stores.
_card_store: CardStore | None = None
_card_store_close: Any = None

# Phase 3 + Phase 4 tenant subsystem handles.  See
# ``api_server/bootstrap/multi_tenant.py``: a single ``wire_multi_tenant``
# call inside the lifespan replaces what used to be two inline blocks.
_multi_tenant_handles: Any = None

# Stage 1 orphan-recovery reaper task (sweeps stuck warming/queued
# property_state rows back to failed).  Only started when the SSoT is
# enabled; cancelled on lifespan shutdown.
_bootstrap_reaper_task: Any = None

# ── Team mention + handoff stores (V2 — collaboration) ─────────────────
# In-memory only for now — the data is short-lived (the receiving
# teammate either acts on the handoff or it ages out into the audit
# log).  Postgres backing can be added later mirroring the
# InMemory contract.
_mention_store: MentionStore | None = None
_handoff_store: HandoffStore | None = None

# ── Causal navigation service (Gap #3 — temporal causal links) ──────────
# Reuses the NarrativeService composer to fetch the property's events
# inside a window, then runs the CausalGraphBuilder's rule suite to
# produce a directed graph plus optional ancestor/descendant walks.
# Read by the ``/api/memory/property/{property_id}/causal`` endpoint.
_causal_service: CausalNavigationService | None = None

# ── Nightly scheduler (Fix #5 — automate continual learning cycles) ─────
# Owns an AsyncIOScheduler and triggers NightlyConsolidator.run_nightly
# once per day (plus MonthlyEvaluator.evaluate on the 1st of each month)
# so the learning loop advances without requiring an external cron to
# call POST /api/memory/consolidate.
_nightly_scheduler: NightlyScheduler | None = None

# ── Onboarding service (Etap 4 — historical DecisionCase bootstrap) ─────
# Replays archived PMS conversations through the learning pipeline so a
# freshly-provisioned property does not start with a cold DecisionCase
# cache.  Wired only when both _case_store and the Botel PMS client are
# available; otherwise the endpoint responds 503.
_onboarding_service: OnboardingService | None = None

# Store received photos: chat_id -> list of {file_id, timestamp, file_url}
_received_photos: dict[str, list[dict]] = {}
# Registered cleaners: chat_id -> {name, phone, registered_at}
_registered_cleaners: dict[str, dict] = {}

# ── Autonomous orchestrator routing ──────────────────────────────────────
from datetime import UTC

from brain_engine.orchestrator.response_router import (
    response_router as _response_router,
)

# Demo claims store: claim_id -> claim data
_claims: dict[str, dict[str, Any]] = {}


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single SSE event string.

    Args:
        event_type: The AG-UI event type name.
        data: JSON-serialisable payload.

    Returns:
        A properly formatted SSE text block.
    """
    payload = json.dumps({"type": event_type, **data}, default=str, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


_ISO_DATETIME_PATTERN: Final = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T",
)


def _iso_date_only(value: str) -> str:
    """Return only the calendar-date portion of an ISO datetime.

    R11 — Sandbox UI test C4 (2026-05-19): UI rendered "May 18, 2026"
    and "14:00" as two separate panel inputs, but the AG-UI payload
    shipped the date field as ``"2026-05-18T06:47:47.758Z"`` — a raw
    PMS ``createdAt`` timestamp leaked into the check-in slot.  The
    formatter then echoed it verbatim per the authoritative-snapshot
    contract, so the guest saw ``"...06:47:47.758Z"`` in the reply.

    Brain cannot reach into the UI to fix the upstream leak, but it
    CAN refuse to forward the corrupted time portion.  Stripping the
    time leaves the date intact (which is what the UI actually
    selected) and lets ``check_in_time`` — a separate field shipped
    verbatim ("14:00") — carry the wall-clock value.

    Behaviour:

    * ``"2026-05-18"``  → ``"2026-05-18"`` (already date-only).
    * ``"2026-05-18T14:00:00"`` → ``"2026-05-18"``.
    * ``"2026-05-18T06:47:47.758Z"`` → ``"2026-05-18"``.
    * Anything that does not match the ``YYYY-MM-DD T…`` pattern is
      returned unchanged — non-ISO inputs (free-text dates, weekday
      labels) flow through verbatim and remain caller responsibility.
    """
    match = _ISO_DATETIME_PATTERN.match(value or "")
    return match.group(1) if match is not None else value


def _reservation_context_from_state(
    raw_state: dict[str, Any],
) -> ReservationContext | None:
    """Build a :class:`ReservationContext` from the AG-UI run state.

    Accepts both ``reservation_context`` (preferred, structured) and
    flat top-level keys (``check_in``, ``check_out``, ``adults`` …)
    that the test harness or older clients may send.  Returns ``None``
    when no reservation fields are present so the pipeline keeps the
    pre-existing ``reservation_context is None`` semantics for callers
    that genuinely ship no booking.

    ``check_in`` and ``check_out`` go through :func:`_iso_date_only`
    so a UI mistake that leaks a full ISO timestamp (R11 / C4) is
    truncated to just the calendar date — the wall-clock portion is
    expected on the separate ``check_in_time`` / ``check_out_time``
    fields.

    Args:
        raw_state: The ``state`` dict from the AG-UI ``RunAgentInput``.

    Returns:
        Populated :class:`ReservationContext`, or ``None`` when the
        payload carried no reservation fields.
    """
    nested = raw_state.get("reservation_context")
    source: dict[str, Any] = (
        nested if isinstance(nested, dict) else dict(raw_state)
    )

    def _str(*keys: str) -> str:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _int(*keys: str) -> int:
        for key in keys:
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    def _payment_status() -> str:
        """Read the payment-status toggle, normalising to a tri-state.

        UI sends one of:

        * ``true`` / ``false`` (the toggle) — ``True`` ⇒ ``"paid"``,
          ``False`` ⇒ ``"unpaid"``.
        * ``"paid"`` / ``"unpaid"`` (older clients shipping a literal).
        * Missing entirely ⇒ ``""`` (unknown — the formatter then
          skips the field rather than guessing).

        ``guest_has_paid`` is accepted as an alias because the UI
        label reads "Guest has paid" — a future renaming on the
        frontend may carry that key verbatim.
        """
        for key in (
            "payment_status",
            "guest_has_paid",
            "is_paid",
            "paid",
        ):
            value = source.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, bool):
                return "paid" if value else "unpaid"
            text = str(value).strip().lower()
            if text in {"true", "1", "yes", "paid"}:
                return "paid"
            if text in {"false", "0", "no", "unpaid"}:
                return "unpaid"
            return text
        return ""

    ctx = ReservationContext(
        status=_str("status", "reservation_status"),
        check_in=_iso_date_only(
            _str("check_in", "check_in_date", "checkIn"),
        ),
        check_out=_iso_date_only(
            _str("check_out", "check_out_date", "checkOut"),
        ),
        check_in_time=_str("check_in_time", "checkInTime"),
        check_out_time=_str("check_out_time", "checkOutTime"),
        guest_name=_str("guest_name", "guestName"),
        num_guests=_int("adults", "num_guests", "numGuests"),
        num_children=_int("children", "num_children"),
        property_name=_str("property_name", "propertyName"),
        booking_channel=_str("booking_channel", "channel_code", "channel"),
        current_time=_str(
            "current_time", "message_sent_at", "messageSentAt",
        ),
        total_price=_str("total_price", "amount"),
        currency=_str("currency"),
        payment_status=_payment_status(),
    )
    return ctx if ctx.has_data() else None


def _availability_calendar_from_state(
    raw_state: dict[str, Any],
) -> list[CalendarDay]:
    """Pick up an ``availability_calendar`` snapshot off the AG-UI state.

    Mirrors :func:`_reservation_context_from_state`: the UI is the
    authoritative source of truth when it ships a calendar window
    (sandbox form, PM-corrected blocks, demo seeds), and the engine
    must NOT overwrite it with a fresh GraphQL fetch — that would
    erase the operator's intent.  Returns an empty list when the
    state holds nothing usable so the caller can fall back to the
    upstream resolver.

    Accepts both the canonical ``availability_calendar`` key and the
    aliases ``calendar`` / ``availability`` so older clients keep
    working without a migration.
    """
    raw = (
        raw_state.get("availability_calendar")
        or raw_state.get("calendar")
        or raw_state.get("availability")
    )
    if not isinstance(raw, list):
        return []
    days: list[CalendarDay] = []
    for entry in raw:
        if isinstance(entry, CalendarDay):
            days.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        try:
            days.append(CalendarDay(**entry))
        except (TypeError, ValueError):
            # Skip malformed rows rather than fail the whole turn —
            # an empty list forces the GraphQL fallback, which still
            # protects the prompt from fabricated availability.
            continue
    return days


_CALENDAR_PRE_WINDOW_DAYS: Final[int] = 7
_CALENDAR_POST_WINDOW_DAYS: Final[int] = 30
_CALENDAR_DEFAULT_LOOKAHEAD_DAYS: Final[int] = 45


def _calendar_window_for_reservation(
    reservation: ReservationContext | None,
) -> tuple[str, str] | None:
    """Pick a sensible ``(from, to)`` ISO window for the calendar fetch.

    When the turn carries a reservation snapshot we widen the window
    around the booking — guests typically ask about extensions a few
    days before / after the existing dates, and the conversation
    pipeline needs that context up front rather than on a tool call.
    Without a reservation we fall back to today / today + 45 days so
    pre-booking enquiries still get a real answer instead of the
    "unknown — please defer" branch.

    Args:
        reservation: Reservation snapshot from the request, when known.

    Returns:
        ``(from_iso, to_iso)`` covering at least the requested window,
        or ``None`` when no usable anchor date can be derived.
    """
    from datetime import date, datetime, timedelta

    def _parse(value: str) -> date | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None

    anchor_in: date | None = None
    anchor_out: date | None = None
    if reservation is not None:
        anchor_in = _parse(reservation.check_in)
        anchor_out = _parse(reservation.check_out)

    if anchor_in is None and anchor_out is None:
        today = datetime.now(UTC).date()
        return (
            today.isoformat(),
            (today + timedelta(days=_CALENDAR_DEFAULT_LOOKAHEAD_DAYS))
            .isoformat(),
        )

    start = (anchor_in or anchor_out) - timedelta(
        days=_CALENDAR_PRE_WINDOW_DAYS,
    )
    end = (anchor_out or anchor_in) + timedelta(
        days=_CALENDAR_POST_WINDOW_DAYS,
    )
    if end <= start:
        end = start + timedelta(days=_CALENDAR_DEFAULT_LOOKAHEAD_DAYS)
    return start.isoformat(), end.isoformat()


async def _resolve_availability_window(
    *,
    customer_id: str,
    org_id: str,
    property_channel_id: str,
    reservation: ReservationContext | None,
) -> list[CalendarDay]:
    """Pull the availability calendar for the active turn.

    Reads ``unified_rateplans`` exclusively through the onboarding-api
    GraphQL layer — the PMS API is no longer touched on the read path.
    The window is derived from the reservation snapshot when present;
    otherwise a 45-day look-ahead is used so pre-booking enquiries
    still hit the calendar.  Failures collapse to an empty list so the
    prompt block falls through to its "unknown — defer" branch.
    """
    if _unified_data_client is None or not customer_id:
        return []
    if not property_channel_id:
        return []
    window = _calendar_window_for_reservation(reservation)
    if window is None:
        return []
    from_iso, to_iso = window
    try:
        return await fetch_calendar_window(
            client=_unified_data_client,
            customer_id=customer_id,
            org_id=org_id,
            property_channel_id=property_channel_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
    except Exception:
        logger.warning(
            "graphql_calendar_window_fetch_failed",
            customer_id=customer_id,
            property_channel_id=property_channel_id,
            from_iso=from_iso,
            to_iso=to_iso,
            exc_info=True,
        )
        return []


async def _invoke_regenerate(
    *,
    pm_question: str,
    pm_answer: str,
    customer_id: str,
    org_id: str,
    property_id: str,
    message_id: str,
) -> Any:
    """Persist the PM-supplied fact AND regenerate the guest reply.

    Used by the AG-UI handler when the frontend sends ``state.pm_input``
    after the PM answered a brain question in the PM Chat column.

    Routes through :func:`regenerate_with_knowledge` so the answer lands
    in the active :class:`PmFactStore` for ``(customer_id,
    property_channel_id)`` before regeneration runs.  Without this
    persistence step every fresh conversation re-asked the same gap —
    Mümin's "wifi şifresini girdim, öğrenmedi" bug.  The fact text is
    framed as ``Q: <gap>\\nA: <answer>`` when a question is supplied so
    the live-chat read path injects self-describing context into the
    next prompt.

    Returns the RegenerateResponse object whose ``.message`` is the new
    guest-facing reply text.
    """
    from brain_engine.conversation.regenerate_service import (
        UpdateKnowledgeRequest,
        regenerate_with_knowledge,
    )
    knowledge_text = (
        f"Q: {pm_question}\nA: {pm_answer}" if pm_question else pm_answer
    )
    request = UpdateKnowledgeRequest(
        customer_id=customer_id,
        org_id=org_id,
        message_id=message_id,
        property_channel_id=property_id,
        knowledge_update=knowledge_text,
        ai_message="",
        guest_message="",
        regenerate_response=True,
    )
    return await regenerate_with_knowledge(request)


def _c8_2_provenance_snapshot(
    *,
    ui: ReservationContext | None,
    graphql: ReservationContext | None,
    merged: ReservationContext | None,
    history: list[ConversationMessage],
) -> str:
    """Serialise reservation-context provenance for the C8.2 probe.

    The C8.2 Sandbox UI test (2026-05-19) reported the brain echoing
    a stale ``May 18`` date when the UI itself displayed ``May 14``.
    The stale value can leak from three places: the UI shipped it
    inside ``state.reservation_context``; the GraphQL ``unified_*``
    fetch returned a row that had not yet been re-indexed; or the
    Redis-replayed assistant history dragged a prior reply forward.
    This helper bundles all three sources, plus the most recent
    assistant turns the prepend logic pulled in, into a single JSON
    string so a one-line grep on the diagnostic log recovers the
    full picture without depending on a JSON log formatter.

    Args:
        ui: Reservation snapshot derived from the AG-UI ``raw_state``.
        graphql: Reservation snapshot freshly fetched from the
            unified-data GraphQL layer (``None`` when the GraphQL
            client is offline or the call raised).
        merged: The post-merge snapshot that is actually forwarded
            to the conversation pipeline.
        history: The full ``conv_messages`` list at the point where
            Redis history has already been prepended.

    Returns:
        JSON string with keys ``ui`` / ``graphql`` / ``merged``
        (each a ``ReservationContext.model_dump()`` or ``None``),
        ``history_total`` (total prepended + current turn count),
        and ``recent_bot_texts`` (up to the last three non-empty
        assistant utterances).
    """
    recent_bot_texts: list[str] = [
        message.text
        for message in history[-6:]
        if message.sender_type == SenderType.BOT and message.text
    ]
    payload: dict[str, object] = {
        "ui": ui.model_dump() if ui is not None else None,
        "graphql": (
            graphql.model_dump() if graphql is not None else None
        ),
        "merged": (
            merged.model_dump() if merged is not None else None
        ),
        "history_total": len(history),
        "recent_bot_texts": recent_bot_texts[-3:],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


async def _run_agent_stream(
    run_input: RunAgentInput,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Drive ConversationService and stream AG-UI SSE events.

    Instantiates an AGUIEmitter bound to the run and binds it to the
    ``_current_emitter`` ContextVar INSIDE the generator so deep subsystems
    (memory, RAG, guardrails, cognitive, intent) can publish events via
    ``brain_engine.streaming.emit_helpers``. ConversationService.process
    is driven concurrently; emitted events are drained to SSE frames.

    Note: ``state.property_id`` is expected to be the short
    ``propertyChannelId`` (e.g. "323133"). Cendra UUIDs are resolved on
    the UI side via ``POST /Property/GetChannels`` before the brain run
    is started — this keeps the brain pipeline a leaf node with no
    inbound HTTP to PMS on the hot path.
    """
    run_id = run_input.run_id or str(uuid.uuid4())
    thread_id = run_input.thread_id or str(uuid.uuid4())

    emitter = AGUIEmitter(run_id=run_id)
    token = set_current_emitter(emitter)
    drain_task: asyncio.Task | None = None
    try:
        yield _sse_event("RUN_STARTED", {"run_id": run_id, "thread_id": thread_id})

        state = AgentState()
        if run_input.state:
            state = AgentState(**run_input.state)
        yield _sse_event("STATE_SNAPSHOT", {"snapshot": state.model_dump()})

        raw_state = run_input.state or {}
        guest_id = str(raw_state.get("guest_id", "")) or thread_id
        # Frontend AG-UI sends ``property_channel_id`` (the V1 onboarding
        # contract); legacy clients still pass ``property_id``.  Accept
        # either so the ConversationService receives a non-empty id and
        # the ``_load_property_knowledge`` cache lookup can hit.
        property_id = str(
            raw_state.get("property_channel_id")
            or raw_state.get("property_id")
            or "",
        )

        # ── PM-answer short-circuit ───────────────────────────────────────── #
        # When the frontend sends state.pm_input = {question, answer} (the PM
        # has answered a brain question in PM Chat), bypass the normal
        # ConversationService pipeline and call regenerate_response instead.
        # This branch runs BEFORE the user_message guard so it works even when
        # the client sends a SYSTEM message (no USER turn) as a signal.
        pm_input = raw_state.get("pm_input") or {}
        if isinstance(pm_input, dict) and pm_input.get("answer"):
            regen_response = await _invoke_regenerate(
                pm_question=pm_input.get("question", ""),
                pm_answer=pm_input.get("answer", ""),
                customer_id=str(raw_state.get("customer_id", "")),
                org_id=str(raw_state.get("org_id", "")),
                property_id=property_id,
                message_id=str(raw_state.get("message_header_id", "")) or thread_id,
            )
            final_text = getattr(regen_response, "message", "") or ""
            if final_text:
                msg_id = str(uuid.uuid4())
                yield _sse_event("TEXT_MESSAGE_START",
                                 {"message_id": msg_id, "role": "assistant"})
                yield _sse_event("TEXT_MESSAGE_CONTENT",
                                 {"message_id": msg_id, "delta": final_text})
                yield _sse_event("TEXT_MESSAGE_END", {"message_id": msg_id})
                await save_conversation_turn(
                    redis=_deps.get("redis"),
                    guest_id=guest_id,
                    property_id=property_id,
                    user_message=f"PM: {pm_input.get('answer', '')}",
                    assistant_reply=final_text,
                )
            yield _sse_event(
                "RUN_FINISHED",
                {"run_id": run_id, "thread_id": thread_id},
            )
            return
        # ─────────────────────────────────────────────────────────────────── #

        user_message = ""
        for msg in reversed(run_input.messages):
            if msg.role == Role.USER:
                user_message = msg.content
                break

        if not user_message:
            yield _sse_event("RUN_FINISHED", {"run_id": run_id, "thread_id": thread_id})
            return

        logger.info(
            "Processing message for thread=%s (%d msgs in history): %s",
            thread_id, len(run_input.messages), user_message[:80],
        )

        conv_messages = [
            ConversationMessage(
                text=msg.content,
                sender_type=SenderType.GUEST if msg.role == Role.USER else SenderType.BOT,
            )
            for msg in run_input.messages
            if msg.content
        ]
        if not conv_messages:
            conv_messages = [ConversationMessage(text=user_message, sender_type=SenderType.GUEST)]

        # Hydrate from Redis only when client sends a single (latest) message.
        # Frontend AG-UI contract: send only the new user message + persistent
        # thread_id; brain prepends prior turns from Redis. If client sent the
        # full history (multi-message), skip the load to avoid duplication.
        if len(run_input.messages) <= 1:
            loaded_history = await load_conversation_history(
                redis=_deps.get("redis"),
                guest_id=guest_id,
                property_id=property_id,
            )
            if loaded_history:
                prepend = [
                    ConversationMessage(
                        text=h["content"],
                        sender_type=(
                            SenderType.GUEST if h.get("role") == "user"
                            else SenderType.BOT
                        ),
                    )
                    for h in loaded_history
                ]
                conv_messages = prepend + conv_messages

        customer_id_value = str(raw_state.get("customer_id", ""))
        org_id_value = str(raw_state.get("org_id", ""))
        reservation_id_value = str(raw_state.get("reservation_id", ""))

        # R10 — always pull the reservation snapshot from the
        # unified GraphQL layer in parallel with whatever the UI
        # shipped in ``state``.  The UI's snapshot is then merged
        # *over* the GraphQL one: UI values win when non-empty
        # (sandbox dropdown overrides for testing), GraphQL fills
        # every field the UI forgot or corrupted.  This closes
        # 2026-05-19 Sandbox tests C3 / C4 / C7 where the UI
        # displayed correct data from ES but shipped it
        # incorrectly to brain.  Failures collapse to ``None`` so
        # an offline GraphQL never breaks the live chat.
        ui_reservation_context = _reservation_context_from_state(raw_state)
        graphql_reservation_context: ReservationContext | None = None
        if _unified_data_client is not None and property_id:
            try:
                graphql_reservation_context = await fetch_reservation_context(
                    client=_unified_data_client,
                    customer_id=customer_id_value,
                    org_id=org_id_value,
                    property_channel_id=property_id,
                    reservation_id=reservation_id_value,
                )
            except Exception:
                logger.warning(
                    "graphql_reservation_context_fetch_failed",
                    exc_info=True,
                )
        reservation_context = merge_reservation_contexts(
            ui=ui_reservation_context,
            graphql=graphql_reservation_context,
        )

        _c8_2_diag_log.info(
            "c8_2_reservation_context_provenance",
            thread_id=thread_id,
            property_id=property_id,
            reservation_id=reservation_id_value,
            payload=_c8_2_provenance_snapshot(
                ui=ui_reservation_context,
                graphql=graphql_reservation_context,
                merged=reservation_context,
                history=conv_messages,
            ),
        )

        # Mirror for the availability calendar: UI sandbox form
        # may carry PM corrections (per-day status flips), but the
        # GraphQL window is authoritative for everything else.
        # The merger keeps the UI's per-day overrides where they
        # exist and fills missing dates from GraphQL.
        ui_calendar = _availability_calendar_from_state(raw_state)
        graphql_calendar = await _resolve_availability_window(
            customer_id=customer_id_value,
            org_id=org_id_value,
            property_channel_id=property_id,
            reservation=reservation_context,
        )
        availability_calendar = merge_calendars(
            ui=ui_calendar,
            graphql=graphql_calendar,
        )

        conv_request = ConversationRequest(
            customer_id=customer_id_value,
            org_id=org_id_value,
            property_id=property_id,
            reservation_id=reservation_id_value,
            listing_id=str(raw_state.get("listing_id", "")),
            conversation_id=str(raw_state.get("conversation_id", thread_id)),
            message_id=str(raw_state.get("message_header_id", "")),
            messages=conv_messages,
            guest_name=str(raw_state.get("guest_name", "")),
            guest_language=str(raw_state.get("guest_language", "")),
            channel=str(raw_state.get("channel", "whatsapp")),
            reservation_context=reservation_context,
            availability_calendar=availability_calendar,
        )

        # Pattern-rule consult fetcher: GraphQL-only.  Constructed
        # per-request so the customer scope is bound on every turn;
        # ``None`` falls through to LLM-only behaviour without making
        # any HTTP call.
        pms_fetcher = (
            GraphqlPmsFetcher(
                client=_unified_data_client,
                customer_id=customer_id_value,
                org_id=org_id_value,
                property_channel_id=property_id,
            )
            if (_unified_data_client is not None and customer_id_value)
            else None
        )

        svc = ConversationService(
            case_store=_case_store,
            rule_router=_rule_router,
            pms_fetcher=pms_fetcher,
            profile_store=_property_profile_store,
            # R2 wiring — owner_flexibility_profiles surface
            # (amenity carve-outs, fee rules, check-in policies,
            # local recommendations) joins ``state.property_knowledge``
            # before the LLM call.  The module-level store is the same
            # instance the orchestrator's preference tier already uses,
            # so the conversation pipeline reads the exact snapshot
            # that downstream booking decisions consult.
            owner_profile_store=_owner_profile_store,
            pm_fact_store=_pm_fact_store,
            reservation_prefetcher=getattr(
                request.app.state, "reservation_prefetcher", None,
            ),
            memory_fanout=_memory_fanout,
            # Memory READ wiring — the fan-out above already WRITES
            # every guest turn to episodic / semantic / KG; without
            # this the sandbox service had no ``memory_system`` so
            # ``_load_memory_context`` short-circuited and the guest
            # agent could never recall a fact it had stored.  Same
            # ``_full_system is not None`` guard as the pipeline below
            # for the rare pre-readiness request.
            memory_system=(
                _full_system.memory
                if _full_system is not None
                else None
            ),
            # R3 wiring — the same GuardrailPipeline the Cendra
            # adapter uses to validate guest replies (Format, Lexical,
            # Repeat, RepeatQuestion, Contradiction, Hallucination).
            # ``_full_system`` is built once in lifespan; when the
            # startup hook has not yet completed (rare — pre-readiness
            # request) the attribute resolves to ``None`` and the
            # validation step short-circuits per the R3 contract.
            guardrail_pipeline=(
                _full_system.guardrails
                if _full_system is not None
                else None
            ),
        )
        response_holder: dict[str, Any] = {}

        async def _drive() -> None:
            try:
                response_holder["response"] = await svc.process(conv_request)
            except Exception as e:
                logger.exception("ConversationService.process failed")
                emitter.emit(EventType.RUN_ERROR, {"run_id": run_id, "error": str(e)})
            finally:
                emitter.close()

        drain_task = asyncio.create_task(_drive())

        async for event in emitter.stream():
            yield event.to_sse()

        # Wait for the pipeline task to finish so response_holder is populated.
        # emitter.stream() exits as soon as emitter.close() is called in _drive's
        # finally block, which happens after svc.process returns — but awaiting
        # explicitly also propagates cancellation cleanly on client disconnect.
        await drain_task

        # Emit the final agent response as TEXT_MESSAGE_* events. The pipeline
        # itself only populates state.agent_response (no intra-pipeline streaming),
        # so without this bridge the AG-UI client (test UI, frontend) sees
        # RUN_STARTED → lifecycle events → RUN_FINISHED with no assistant text.
        # Uppercase event names + "delta" payload key match the app.js parser
        # in api_server/static/test_ui/app.js.
        response = response_holder.get("response")
        final_text = getattr(response, "message", "") or ""
        if final_text:
            msg_id = str(uuid.uuid4())
            yield _sse_event(
                "TEXT_MESSAGE_START",
                {"message_id": msg_id, "role": "assistant"},
            )
            yield _sse_event(
                "TEXT_MESSAGE_CONTENT",
                {"message_id": msg_id, "delta": final_text},
            )
            yield _sse_event(
                "TEXT_MESSAGE_END",
                {"message_id": msg_id},
            )

        # Persist the new turn so future requests can hydrate from Redis.
        # Note: only the latest USER message (`user_message`) is persisted as
        # the user side of this turn. Per AG-UI contract clients send a single
        # new user message per run; if a client sends multiple new user
        # messages in one run, only the last is saved and earlier ones are
        # lost from Redis history (they still flow into the LLM for this turn).
        if final_text and user_message:
            await save_conversation_turn(
                redis=_deps.get("redis"),
                guest_id=guest_id,
                property_id=property_id,
                user_message=user_message,
                assistant_reply=final_text,
            )

        yield _sse_event("RUN_FINISHED", {"run_id": run_id, "thread_id": thread_id})
    finally:
        reset_current_emitter(token)
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()


async def _handle_telegram_message(message: dict[str, Any]) -> None:
    """Process a single Telegram message (used by both polling and webhook).

    Routing priority:
      1. Commands (/start, /register, /approve, /deny)
      2. Active orchestrator (cleaner/PMS/vendor responding to workflow)
      3. Photos and /done (with orchestrator delivery)
      4. Fallback help message
    """
    from brain_engine.api import mockup_loader

    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")
    from_user = message.get("from", {})
    first_name = from_user.get("first_name", "Unknown")

    if not chat_id:
        return

    # ── /start — register + auto-match to mockup data ──── #
    if text.startswith("/start"):
        await _handle_start_command(chat_id, first_name)
        return

    # ── /register <contact_id> — explicit registration ──── #
    if text.startswith("/register"):
        await _handle_register_command(chat_id, text, first_name)
        return

    # ── /approve <request_id> [always] ──────────────────── #
    if text.startswith("/approve") and _approval_gateway:
        await _handle_approve_command(chat_id, text, first_name)
        return

    # ── /deny <request_id> ──────────────────────────────── #
    if text.startswith("/deny") and _approval_gateway:
        await _handle_deny_command(chat_id, text, first_name)
        return

    # ── /done — cleaning complete ───────────────────────── #
    if text.startswith("/done"):
        await _handle_done_command(chat_id, text, first_name)
        return

    # ── Photos ──────────────────────────────────────────── #
    photos = message.get("photo", [])
    if photos:
        await _handle_photo(chat_id, message)
        return

    # ── Route text to active orchestrator ───────────────── #
    orch = _response_router.get_orchestrator(chat_id)
    if orch:
        contact = mockup_loader.find_contact_by_chat_id(chat_id)
        role = contact.get("role", "unknown") if contact else "unknown"
        if role == "pms":
            orch.deliver_pms_response(chat_id, text)
        else:
            orch.deliver_text(chat_id, text)
        logger.info("Routed text from %s (%s) to orchestrator %s", first_name, role, orch.process_id)
        return

    # ── Fallback ────────────────────────────────────────── #
    if text and _telegram_bot:
        await _telegram_bot.send_message(
            chat_id=int(chat_id),
            text=(
                "Merhaba! Brain Engine aktif.\n\n"
                "Komutlar:\n"
                "/start — Kayit ol\n"
                "/register &lt;id&gt; — Kimlik bagla (ornek: /register cleaner-aybuke)\n"
                "/done — Temizlik tamamlandi"
            ),
        )


async def _handle_start_command(chat_id: str, first_name: str) -> None:
    """Handle /start command with auto-matching to mockup contacts.

    Args:
        chat_id: Telegram chat ID.
        first_name: User's Telegram first name.
    """
    from brain_engine.api import mockup_loader

    _registered_cleaners[chat_id] = {"name": first_name, "chat_id": chat_id}

    # Try auto-match by first name
    matched = mockup_loader.auto_match_by_name(first_name, chat_id)

    if matched and _telegram_bot:
        role_tr = {"cleaner": "Temizlikci", "vendor": "Usta", "pms": "Yonetici"}
        role_label = role_tr.get(matched["role"], matched["role"])
        await _telegram_bot.send_message(
            chat_id=int(chat_id),
            text=(
                f"<b>Hosgeldiniz, {first_name}!</b>\n\n"
                f"Otomatik eslestirme: <b>{role_label}</b>\n"
                f"ID: {matched.get('contact_id', matched.get('name', ''))}\n\n"
                "Brain Engine sizinle iletisime gectiginde "
                "otomatik bildirim alacaksiniz."
            ),
            parse_mode="HTML",
        )
    elif _telegram_bot:
        await _telegram_bot.send_message(
            chat_id=int(chat_id),
            text=(
                f"<b>Hosgeldiniz, {first_name}!</b>\n\n"
                "Kaydiniz alindi. Eslestirme icin:\n"
                "/register cleaner-aybuke\n"
                "/register cleaner-efe\n"
                "/register cleaner-mumin\n"
                "/register Can\n\n"
                "Veya yoneticinize chat ID'nizi iletin: "
                f"<code>{chat_id}</code>"
            ),
            parse_mode="HTML",
        )
    logger.info("Start: %s (chat_id=%s) matched=%s", first_name, chat_id, bool(matched))


async def _handle_register_command(
    chat_id: str,
    text: str,
    first_name: str,
) -> None:
    """Handle /register <contact_id> command.

    Args:
        chat_id: Telegram chat ID.
        text: Full message text.
        first_name: User's first name.
    """
    from brain_engine.api import mockup_loader

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        if _telegram_bot:
            await _telegram_bot.send_message(
                chat_id=int(chat_id),
                text="Kullanim: /register &lt;contact_id&gt;\nOrnek: /register cleaner-aybuke",
            parse_mode="HTML",
            )
        return

    contact_id = parts[1].strip()
    success = mockup_loader.update_chat_id(contact_id, chat_id)

    if success and _telegram_bot:
        await _telegram_bot.send_message(
            chat_id=int(chat_id),
            text=f"<b>Kayit basarili!</b>\n{contact_id} -> chat_id {chat_id}",
            parse_mode="HTML",
        )
    elif _telegram_bot:
        await _telegram_bot.send_message(
            chat_id=int(chat_id),
            text=f"'{contact_id}' bulunamadi. Gecerli ID'ler: cleaner-aybuke, cleaner-efe, cleaner-mumin, Can",
        )
    logger.info("Register: %s -> %s (success=%s)", contact_id, chat_id, success)


async def _handle_approve_command(
    chat_id: str,
    text: str,
    first_name: str,
) -> None:
    """Handle /approve <request_id> [always] command.

    Args:
        chat_id: Telegram chat ID.
        text: Full message text.
        first_name: User's first name.
    """
    parts = text.split()
    if len(parts) < 2:
        return
    request_id = parts[1]
    apply_rule = "always" in text.lower()
    rule_scope = "always" if apply_rule else "this_time"
    try:
        await _approval_gateway.submit_response(
            request_id=request_id,
            approved=True,
            owner_id=chat_id,
            message=f"Approved via Telegram by {first_name}",
            apply_rule=apply_rule,
            rule_scope=rule_scope,
        )
        if _telegram_bot:
            scope_text = " (rule: always)" if apply_rule else ""
            await _telegram_bot.send_message(
                chat_id=int(chat_id),
                text=f"<b>Approved</b> {request_id}{scope_text}",
                parse_mode="HTML",
            )
    except Exception as exc:
        logger.error("Approve command failed: %s", exc)
        if _telegram_bot:
            await _telegram_bot.send_message(chat_id=int(chat_id), text=f"Error: {exc}")


async def _handle_deny_command(
    chat_id: str,
    text: str,
    first_name: str,
) -> None:
    """Handle /deny <request_id> command.

    Args:
        chat_id: Telegram chat ID.
        text: Full message text.
        first_name: User's first name.
    """
    parts = text.split()
    if len(parts) < 2:
        return
    request_id = parts[1]
    try:
        await _approval_gateway.submit_response(
            request_id=request_id,
            approved=False,
            owner_id=chat_id,
            message=f"Denied via Telegram by {first_name}",
        )
        if _telegram_bot:
            await _telegram_bot.send_message(
                chat_id=int(chat_id),
                text=f"<b>Denied</b> {request_id}",
                parse_mode="HTML",
            )
    except Exception as exc:
        logger.error("Deny command failed: %s", exc)
        if _telegram_bot:
            await _telegram_bot.send_message(chat_id=int(chat_id), text=f"Error: {exc}")


async def _handle_done_command(
    chat_id: str,
    text: str,
    first_name: str,
) -> None:
    """Handle /done command — marks cleaning as complete.

    If an orchestrator is active, delivers the /done signal to it.

    Args:
        chat_id: Telegram chat ID.
        text: Full message text (may include notes after /done).
        first_name: User's first name.
    """
    photo_count = len(_received_photos.get(chat_id, []))

    # Extract notes after /done
    notes = text[5:].strip() if len(text) > 5 else ""

    # Deliver to orchestrator if active
    orch = _response_router.get_orchestrator(chat_id)
    if orch:
        orch.deliver_done(chat_id, notes)
        if _telegram_bot:
            await _telegram_bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"<b>Temizlik tamamlandi!</b>\n"
                    f"Fotograflar: {photo_count}\n"
                    f"Notlar: {notes or '(yok)'}\n\n"
                    "Tesekkurler! Yonetici bilgilendirildi."
                ),
                parse_mode="HTML",
            )
        logger.info("Done delivered to orchestrator %s from %s", orch.process_id, first_name)
        return

    # No orchestrator — basic response
    if _telegram_bot:
        await _telegram_bot.send_message(
            chat_id=int(chat_id),
            text=(
                f"<b>Temizlik tamamlandi!</b>\n"
                f"Fotograflar: {photo_count}\n\n"
                "Tesekkurler!"
            ),
            parse_mode="HTML",
        )
    logger.info("Done (no orchestrator) by chat_id=%s, photos=%d", chat_id, photo_count)


async def _handle_photo(chat_id: str, message: dict[str, Any]) -> None:
    """Handle incoming photo — store and deliver to orchestrator.

    Args:
        chat_id: Telegram chat ID.
        message: Full Telegram message dict.
    """
    photos = message.get("photo", [])
    best_photo = max(photos, key=lambda p: p.get("file_size", 0))
    file_id = best_photo.get("file_id", "")
    caption = message.get("caption", "")

    if chat_id not in _received_photos:
        _received_photos[chat_id] = []
    _received_photos[chat_id].append({
        "file_id": file_id,
        "caption": caption,
        "timestamp": message.get("date", 0),
    })
    count = len(_received_photos[chat_id])

    # Deliver to orchestrator if active
    orch = _response_router.get_orchestrator(chat_id)
    if orch:
        orch.deliver_photo(chat_id, file_id, caption)

    if _telegram_bot:
        await _telegram_bot.send_message(
            chat_id=int(chat_id),
            text=f"Foto {count} alindi. Devam edin veya /done yazin.",
        )
    logger.info("Photo from chat_id=%s, total=%d, orchestrator=%s", chat_id, count, bool(orch))


async def _telegram_polling_loop() -> None:
    """Background task: long-poll Telegram for updates (no HTTPS needed)."""
    offset = 0
    logger.info("Telegram polling loop started")
    while True:
        try:
            updates = await _telegram_bot.get_updates(offset=offset, timeout=30)
            for update in updates:
                offset = update.get("update_id", 0) + 1
                msg = update.get("message")
                if msg:
                    await _handle_telegram_message(msg)
        except asyncio.CancelledError:
            logger.info("Telegram polling stopped")
            return
        except Exception as exc:
            logger.error("Telegram polling error: %s", exc)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage application startup and shutdown.

    On startup: load settings, initialize the brain engine components.
    On shutdown: clean up resources.
    """
    global _settings, _brain_engine_ready, _elevenlabs_client, _telegram_bot, _memory
    global _approval_gateway, _approval_notifier, _preference_store, _preference_learner, _policy_enforcer
    global _config_validator, _gap_resolver
    global _guest_profile_builder, _loyalty_scorer, _benefit_recommender, _risk_flag_system
    global _case_store, _case_store_close
    global _rule_store, _rule_store_close, _rule_router
    global _experiment_store, _experiment_store_close
    global _experiment_registry
    global _ops_logger
    global _negotiation_manager
    global _vendor_channels
    global _narrative_service
    global _evidence_service
    global _blocker_store
    global _prompt_aggregator
    global _causal_service
    global _nightly_scheduler
    global _onboarding_service

    logger.info("Starting Airbnb Brain Engine AG-UI server...")
    _settings = Settings()
    logger.info("Settings loaded. LLM model: %s", _settings.llm_model)

    # Route every legacy ``litellm.acompletion(model="gpt-4o-mini", ...)``
    # call through Azure OpenAI before any subsystem boots.  Without
    # this the conversation pipeline / sandbox / pattern agents fall
    # through to public OpenAI and 401 on the dev cluster, where the
    # tenant only has an Azure resource provisioned.
    try:
        from brain_engine.models.azure_routing import (
            configure_litellm_for_azure,
        )
        configure_litellm_for_azure()
    except Exception as exc:
        logger.warning(
            "azure_openai_routing_setup_failed (%s): %s",
            type(exc).__name__, exc,
        )

    # Wire LiteLLM → Langfuse trace pipeline.  LiteLLM ships a
    # native callback that POSTs every ``acompletion`` /
    # ``completion`` round-trip to a Langfuse instance derived from
    # ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST``
    # environment variables.  Enabling the callback once at startup
    # captures every conversation pipeline LLM call (8+ litellm
    # call-sites) without code changes at the per-call surface.
    # Failure to register the callback (missing langfuse SDK,
    # invalid keys, network blip) must never block startup — we
    # log and continue with traces silently dropped, matching the
    # legacy "no observability is acceptable" stance.
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get(
        "LANGFUSE_SECRET_KEY",
    ):
        try:
            import litellm
            litellm.success_callback = ["langfuse"]
            litellm.failure_callback = ["langfuse"]
            logger.info(
                "litellm_langfuse_callback_registered "
                "(host=%s)",
                os.environ.get("LANGFUSE_HOST", "default"),
            )
        except Exception as exc:
            logger.warning(
                "litellm_langfuse_callback_failed (%s): %s",
                type(exc).__name__, exc,
            )

    # Cognitive memory system construction + initialisation is
    # delegated to the bootstrap section.  See
    # ``api_server/bootstrap/memory.py`` for the I/O-bearing
    # initialise step and the shutdown contract.  The shutdown
    # branch in this lifespan stays unchanged.
    _memory = await wire_memory(application, settings=_settings)

    # ── Shared memory fan-out (PR #F) ─────────────────────────────────
    # Build a single :class:`MemoryFanOut` and inject it into every
    # DecisionCase write path so the timeline / semantic / KG
    # surfaces receive bootstrap, live, regenerate, and nightly
    # consolidator events uniformly.  Falls back to
    # :class:`NullMemoryFanOut` when the memory backends are absent.
    global _memory_fanout
    from brain_engine.memory.fanout import (
        MemoryFanOut as _MemoryFanOut,
    )
    from brain_engine.memory.fanout import (
        NullMemoryFanOut as _NullMemoryFanOut,
    )
    if _memory is not None and (
        getattr(_memory, "episodic", None) is not None
        or getattr(_memory, "semantic", None) is not None
        or getattr(_memory, "knowledge_graph", None) is not None
    ):
        _memory_fanout = _MemoryFanOut(
            episodic=getattr(_memory, "episodic", None),
            semantic=getattr(_memory, "semantic", None),
            knowledge_graph=getattr(_memory, "knowledge_graph", None),
        )
    else:
        _memory_fanout = _NullMemoryFanOut()
    application.state.memory_fanout = _memory_fanout

    # ── DecisionCase store ────────────────────────────────────────────
    # See ``api_server/bootstrap/decision_case.py`` for backend
    # selection, error handling, and shutdown contract.  The store is
    # also exposed on ``application.state.case_store`` for readers
    # that have already migrated off the module global.
    _case_store, _case_store_close = await wire_decision_case(
        application,
    )

    # ── PatternRule store + router ────────────────────────────────────
    # See ``api_server/bootstrap/pattern_rule.py`` for backend
    # selection, error handling, and shutdown contract.  The store
    # and router are also exposed on ``application.state`` for
    # readers that have already migrated off the module globals.
    _rule_store, _rule_store_close, _rule_router = await wire_pattern_rule(
        application,
    )

    # ── A/B experiment store + registry ─────────────────────────────
    # See ``api_server/bootstrap/experiments.py``.  The registry
    # outlives any one process: ``warm_from_store`` rehydrates
    # experiments and per-variant tallies on every cold start so
    # ``min_trials_per_arm`` is reached across pod rollouts.
    (
        _experiment_store,
        _experiment_store_close,
        _experiment_registry,
    ) = await wire_experiments(application)

    # ── Ops DecisionCase logger ──────────────────────────────────────
    # See ``api_server/bootstrap/ops_logger.py``.  Always constructed,
    # even when the case store is None — the logger handles that
    # internally as a no-op so downstream call sites can hold a
    # permanent reference.
    _ops_logger = wire_ops_logger(application, case_store=_case_store)

    # The NegotiationSessionManager is built lower down, after
    # Telegram / WhatsApp are constructed, so its VendorChannelRegistry
    # can bind to the real transports rather than to None.

    # ── ElevenLabs voice client ──────────────────────────────────────
    # See ``api_server/bootstrap/elevenlabs.py``.  The shutdown
    # branch ``await _elevenlabs_client.close()`` lower in this
    # lifespan stays unchanged.
    _elevenlabs_client = wire_elevenlabs(application, settings=_settings)

    # Telegram bot construction is delegated to the bootstrap
    # section.  Polling-task creation and shutdown still live in
    # this lifespan because they depend on module-level handlers
    # (``_handle_telegram_message``, ``_telegram_polling_loop``)
    # that read other globals not yet extracted.
    _telegram_bot = wire_telegram_bot(application, settings=_settings)

    # ── Negotiation: vendor channels + session manager ───────────────
    # See ``api_server/bootstrap/negotiation.py`` for the wiring.
    # The registry and manager are built in tandem because the
    # manager's send_resolver IS the registry.  Shutdown
    # (``await _negotiation_manager.close_all()``) stays in this
    # lifespan.
    _vendor_channels, _negotiation_manager = wire_negotiation(
        application,
        telegram_bot=_telegram_bot,
        ops_logger=_ops_logger,
    )

    # ── Initialize Phase 1-4 systems ─────────────────────────────────────
    _preference_store = PreferenceStore()
    _preference_learner = PreferenceLearner(preference_store=_preference_store)
    _policy_enforcer = PolicyEnforcer(preference_store=_preference_store)

    # Wrap TelegramBot in approval-aware notifier
    if _telegram_bot:
        _approval_notifier = TelegramApprovalNotifier(telegram_bot=_telegram_bot)
        logger.info("TelegramApprovalNotifier initialized.")
    else:
        _approval_notifier = None

    _approval_gateway = ApprovalGateway(
        notifier=_approval_notifier,
        preference_store=_preference_store,
    )
    _config_validator = ConfigValidator()
    _gap_resolver = GapResolver(
        notifier=_telegram_bot,
        voice_client=_elevenlabs_client,
    )
    _loyalty_scorer = LoyaltyScorer()
    _benefit_recommender = BenefitRecommender()
    _risk_flag_system = RiskFlagSystem()

    if _memory:
        _guest_profile_builder = GuestProfileBuilder(
            guest_history=_memory.guest_history if hasattr(_memory, "guest_history") else None,
            episodic=_memory.episodic if hasattr(_memory, "episodic") else None,
            knowledge_graph=_memory.knowledge_graph if hasattr(_memory, "knowledge_graph") else None,
        )

    # ── Initialize Smart Engine ─────────────────────────────────────────
    from brain_engine.smart_engine.city_knowledge import (
        CityKnowledgeGraph as _CKG,
    )
    from brain_engine.smart_engine.scoring_engine import ScoringEngine as _SE
    global _scoring_engine, _city_knowledge
    _scoring_engine = _SE()
    _city_knowledge = _CKG(_scoring_engine)
    logger.info("Smart Engine initialized: ScoringEngine + CityKnowledgeGraph")

    logger.info("Phase 1-4 systems initialized: Approval, Preferences, Fallback, Guest Intelligence")

    # ── Initialize Full System (Blueprint v5: reasoning + continual learning + API) ──
    global _full_system
    _full_system = create_full_system(
        redis_url=_settings.redis_url,
        qdrant_url=_settings.qdrant_url,
        llm_model=_settings.llm_model,
        case_store=_case_store,
        rule_store=_rule_store,
    )
    await _full_system.initialize()

    configure_dependencies(
        cognitive_controller=_full_system.memory.cognitive,
        complexity_router=_full_system.complexity_router,
        llm_router=_full_system.llm_router,
        guardrail_pipeline=_full_system.guardrails,
        interaction_recorder=_full_system.interaction_recorder,
        skill_engine=_full_system.skill_engine,
        nightly_consolidator=_full_system.nightly_consolidator,
        monthly_evaluator=_full_system.monthly_evaluator,
        adaptive_autonomy=_full_system.adaptive_autonomy,
        stakeholder_model=_full_system.stakeholder,
        memory_system=_full_system.memory,
        business_classifier=_full_system.business_classifier,
        ops_session_manager=_full_system.ops_session_manager,
        durable_pipeline=_full_system.durable_pipeline,
    )

    # Inject remaining deps directly (automation, IoT, guest memory, task queue)
    from brain_engine.api.cendra_adapter import _deps
    _deps["automation_engine"] = _full_system.automation_engine
    _deps["iot_processor"] = _full_system.iot_processor
    _deps["guest_memory_store"] = _full_system.guest_memory_store
    _deps["task_queue"] = _full_system.task_queue
    _deps["worker_pool"] = _full_system.worker_pool
    _deps["redis"] = _full_system.redis_client

    # Rules API + Templates + Active Processes deps
    from brain_engine.onboarding.template_store import TemplateStore
    from brain_engine.scheduler.follow_up_store import FollowUpStore
    _deps["template_store"] = TemplateStore(redis_url=_settings.redis_url)
    _deps["follow_up_store"] = FollowUpStore(redis_url=_settings.redis_url)
    _deps["active_process_store"] = _full_system.memory.active_process_store
    _deps["elevenlabs_client"] = _elevenlabs_client
    _deps["elevenlabs_phone_number_id"] = _settings.elevenlabs_phone_number_id if _settings else ""
    _deps["telegram_bot"] = _telegram_bot

    # Load mockup data and configure workflow endpoints
    mockup_loader.load_all()
    configure_workflow_deps(_deps)

    logger.info(
        "Full system initialized: %d dependencies wired, WorkerPool %s",
        len(_deps),
        "running" if _full_system.worker_pool and _full_system.worker_pool.is_running else "off",
    )

    # ── Unified Data GraphQL client ──────────────────────────────────
    # Hoisted out of the narrative section because three downstream
    # sections (narrative source list, conversation archive loader,
    # profile harvester) all consume the same client + workspace
    # identifiers.  See ``api_server/bootstrap/unified_data.py`` for
    # the env-var contract and the shutdown contract.
    global _unified_data_client
    (
        _unified_data_client,
        _unified_customer_id,
        _unified_org_id,
        _unified_provider_type,
    ) = wire_unified_data(application)

    # ── Elasticsearch property enrichment (opt-in) ──────────────────────
    # Builds the direct-ES property reader BEFORE the onboarding section
    # so ``wire_onboarding`` can inject it into the harvester.  Returns
    # ``None`` (flag off / no key / init error) → GraphQL-only harvest.
    wire_elasticsearch(application)

    # ── Narrative service (Gap #2) ──────────────────────────────────────
    # See ``api_server/bootstrap/narrative.py`` for adapter
    # activation, the swallowed-error contract, and the
    # unified-client clearing rule preserved from the original
    # inline section.  The bootstrap returns the (possibly
    # cleared) unified client so the module global stays in sync
    # with downstream-disable behaviour.
    _narrative_service, _unified_data_client = wire_narrative(
        application,
        case_store=_case_store,
        memory=_memory,
        elevenlabs_client=_elevenlabs_client,
        unified_data_client=_unified_data_client,
        unified_customer_id=_unified_customer_id,
        unified_org_id=_unified_org_id,
        unified_provider_type=_unified_provider_type,
        settings=_settings,
    )

    # ── Evidence service (GAP L) ────────────────────────────────────────
    # See ``api_server/bootstrap/evidence.py`` for the blocker
    # backend selection (memory / postgres + memory fallback),
    # the prompt aggregator wiring, and the four evidence
    # adapters.  The bootstrap returns the close handle so the
    # existing shutdown branch on ``_blocker_store_close`` keeps
    # working unchanged.
    (
        _evidence_service,
        _blocker_store,
        _blocker_store_close,
        _prompt_aggregator,
    ) = await wire_evidence(
        application,
        rule_store=_rule_store,
        case_store=_case_store,
        memory=_memory,
        settings=_settings,
    )

    # ── Autonomy + Trust Meter (V2) ─────────────────────────────────────
    # See ``api_server/bootstrap/autonomy.py`` for the backend
    # selection (memory / postgres + memory fallback) and the
    # TrustMeterService composition.  The bootstrap returns the
    # close handle so the existing shutdown branch on
    # ``_autonomy_store_close`` keeps working unchanged.
    global _autonomy_store, _autonomy_store_close
    global _autonomy_engine, _trust_meter_service
    (
        _autonomy_store,
        _autonomy_store_close,
        _autonomy_engine,
        _trust_meter_service,
    ) = await wire_autonomy(application)

    # ── Interview engine (V2 proactive PM Q&A) ──────────────────────────
    # See ``api_server/bootstrap/interview.py`` for the backend
    # selection (memory / postgres + memory fallback) and the
    # InterviewEngine composition.  The bootstrap returns the
    # close handle so the existing shutdown branch on
    # ``_interview_store_close`` keeps working unchanged.  Note
    # that ``configure_interview_deps`` is NOT called inside the
    # bootstrap because it also needs the voice transcriber wired
    # below — the glue stays in lifespan.
    global _interview_store, _interview_store_close, _interview_engine
    (
        _interview_store,
        _interview_store_close,
        _interview_engine,
    ) = await wire_interview(application)

    # ── Property profile store backend selection ───────────────────────
    # Default is the in-memory store installed at module scope so the
    # knowledge endpoint and live-chat profile-cache lookup never 503.
    # Setting ``PROPERTY_PROFILE_STORE_BACKEND=postgres`` swaps in the
    # PgPropertyProfileStore against the ``property_profiles`` table
    # (migration 012) so harvested snapshots survive pod restarts and
    # autoscaler-driven evictions.  URL falls back to ``DATABASE_URL``.
    # A misconfigured Postgres setup is non-fatal: we log a warning and
    # keep the in-memory default so an ops mistake cannot bring the
    # knowledge surface down.  This must run BEFORE ``wire_voice`` and
    # ``wire_onboarding`` below — both read ``_property_profile_store``
    # at call time and pass it into their own constructors.
    global _property_profile_store, _property_profile_store_close
    _profile_store_backend = os.getenv(
        "PROPERTY_PROFILE_STORE_BACKEND", "memory",
    ).lower()
    if _profile_store_backend == "postgres":
        _profile_store_db_url = (
            os.getenv("PROPERTY_PROFILE_STORE_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
        if _profile_store_db_url:
            try:
                _pg_profile_store = await PgPropertyProfileStore.from_url(
                    _profile_store_db_url,
                )
                _property_profile_store = _pg_profile_store
                _property_profile_store_close = _pg_profile_store.close
                logger.info(
                    "Property profile store wired (backend=postgres)",
                )
            except Exception as exc:
                logger.warning(
                    "PgPropertyProfileStore init failed — "
                    "falling back to in-memory: %s (%s)",
                    exc,
                    type(exc).__name__,
                )
        else:
            logger.warning(
                "PROPERTY_PROFILE_STORE_BACKEND=postgres but no "
                "DATABASE_URL — falling back to in-memory store.",
            )

    # ── PM-fact store backend selection ────────────────────────────────
    # Mirrors the property-profile pattern above.  Default is the
    # in-memory store installed at module scope so PM Chat replies
    # still flow through ``_store_knowledge_update`` even when no
    # Postgres is configured (dev shells, unit tests).  Setting
    # ``PM_FACT_STORE_BACKEND=postgres`` swaps in :class:`PgPmFactStore`
    # against the ``property_pm_facts`` table (migration 013) so
    # manager corrections survive pod restarts and autoscaler-driven
    # evictions.  URL falls back to ``DATABASE_URL``.  A misconfigured
    # Postgres setup is non-fatal — we log a warning and keep the
    # in-memory default so an ops mistake cannot brick the regenerate
    # endpoint.  ``set_pm_fact_store`` rewires the module-level store
    # used by :func:`regenerate_with_knowledge` so the live-chat read
    # path and the PM Chat write path share one store instance.
    global _pm_fact_store, _pm_fact_store_close
    _pm_fact_backend = os.getenv(
        "PM_FACT_STORE_BACKEND", "memory",
    ).lower()
    if _pm_fact_backend == "postgres":
        _pm_fact_db_url = (
            os.getenv("PM_FACT_STORE_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
        if _pm_fact_db_url:
            try:
                _pg_pm_fact_store = await PgPmFactStore.from_url(
                    _pm_fact_db_url,
                )
                _pm_fact_store = _pg_pm_fact_store
                _pm_fact_store_close = _pg_pm_fact_store.close
                logger.info(
                    "PM fact store wired (backend=postgres)",
                )
            except Exception as exc:
                logger.warning(
                    "PgPmFactStore init failed — "
                    "falling back to in-memory: %s (%s)",
                    exc,
                    type(exc).__name__,
                )
        else:
            logger.warning(
                "PM_FACT_STORE_BACKEND=postgres but no DATABASE_URL "
                "— falling back to in-memory store.",
            )
    set_pm_fact_store(_pm_fact_store)
    application.state.pm_fact_store = _pm_fact_store

    # ── Sprint 9 forward-path: reservation prefetcher ──────────────────
    # ``BRAIN_LEAD_TIME_FETCH_ENABLED`` (default off) gates a per-property
    # GraphQL index that resolves ``reservation.data.createdAt`` so
    # ``case_builder._compute_lead_time`` produces a real value on every
    # newly-ingested ``DecisionCase`` instead of the legacy ``0.0``.
    # Bootstrap-time gate keeps the no-op path bit-for-bit identical when
    # the flag is unset — the prefetcher is simply not constructed and
    # ``ConversationService`` falls through to the pre-Sprint-9 call.
    application.state.reservation_prefetcher = None
    _lead_time_flag = (
        os.environ.get("BRAIN_LEAD_TIME_FETCH_ENABLED", "")
        .strip()
        .lower()
    )
    if (
        _lead_time_flag in ("1", "true", "yes", "on")
        and _unified_data_client is not None
        and _unified_customer_id
    ):
        try:
            from brain_engine.conversation.reservation_prefetcher import (
                ReservationPrefetcher,
            )

            application.state.reservation_prefetcher = ReservationPrefetcher(
                client=_unified_data_client,
                customer_id=_unified_customer_id,
                org_id=_unified_org_id,
                provider_type=_unified_provider_type,
            )
            logger.info(
                "ReservationPrefetcher wired (Sprint 9 forward-path) "
                "customer=%s org=%s provider=%s",
                _unified_customer_id,
                _unified_org_id or "—",
                _unified_provider_type or "—",
            )
        except Exception as exc:
            logger.warning(
                "ReservationPrefetcher init skipped: %s (%s)",
                exc,
                type(exc).__name__,
            )

    # ── Owner flexibility store + ExecutionOrchestrator wiring ─────────
    # Symmetric with the property-profile / pm-fact bootstraps above.
    # Default backend is the module-level ``InMemoryOwnerProfileStore``
    # so unit tests and dev shells keep working without Postgres.
    # Setting ``OWNER_PROFILE_STORE_BACKEND=postgres`` swaps in
    # :class:`PgOwnerProfileStore` against migration 014's
    # ``owner_flexibility_profiles`` table.  URL falls back to
    # ``DATABASE_URL``.  A misconfigured Postgres setup is non-fatal —
    # we keep the in-memory store so the §10 priority chain still has
    # a preference tier to walk.
    global _owner_profile_store, _owner_profile_store_close
    _owner_profile_backend = os.getenv(
        "OWNER_PROFILE_STORE_BACKEND", "memory",
    ).lower()
    if _owner_profile_backend == "postgres":
        _owner_profile_db_url = (
            os.getenv("OWNER_PROFILE_STORE_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
        if _owner_profile_db_url:
            try:
                _pg_owner_store = await PgOwnerProfileStore.from_url(
                    _owner_profile_db_url,
                )
                _owner_profile_store = _pg_owner_store
                _owner_profile_store_close = _pg_owner_store.close
                logger.info(
                    "Owner profile store wired (backend=postgres)",
                )
            except Exception as exc:
                logger.warning(
                    "PgOwnerProfileStore init failed — "
                    "falling back to in-memory: %s (%s)",
                    exc,
                    type(exc).__name__,
                )
        else:
            logger.warning(
                "OWNER_PROFILE_STORE_BACKEND=postgres but no "
                "DATABASE_URL — falling back to in-memory store.",
            )
    application.state.owner_profile_store = _owner_profile_store
    # Surface the property profile cache so the REST conversation
    # dependency (get_conversation_service) can load property knowledge
    # the same way the AG-UI SSE handler does.  Assigned AFTER the
    # backend cutover above so it captures the Postgres-backed store.
    application.state.property_profile_store = _property_profile_store

    # ExecutionOrchestrator wires the §10 priority chain on top of the
    # already-configured stores.  Tiers whose dependencies are absent
    # (blocker engine, pattern store, staticity guard) fall back to the
    # no-op resolvers baked into :class:`ExecutionOrchestrator`, so the
    # orchestrator stays usable even on minimally-configured envs.
    #
    # Branch 5 wires the upper tiers: blocker (tier 2), safety (tier
    # 3) and learned (tier 4) all become active when their backing
    # stores are present so the §10 chain stops short-circuiting on
    # preference for every turn.  ``BlockerEngine`` is composed here
    # from the already-built blocker store so the orchestrator and
    # the EvidenceService share one source of blocker truth.  The
    # ``StaticityGuard`` is constructed with a fresh in-memory
    # classifier — the classifier owns no persistent state and is
    # safe to instantiate per process.
    _blocker_engine = (
        BlockerEngine(_blocker_store) if _blocker_store is not None else None
    )
    _staticity_guard = StaticityGuard(classifier=StaticityClassifier())
    global _execution_orchestrator
    _execution_orchestrator = build_execution_orchestrator(
        owner_profile_store=_owner_profile_store,
        blocker_engine=_blocker_engine,
        staticity_guard=_staticity_guard,
        pattern_store=_rule_store,
    )
    application.state.orchestrator = _execution_orchestrator
    # Surface the PM fact store to the profile router so the
    # ``/properties/{id}/memory`` and ``/memory/timeline`` endpoints
    # render the Postgres-backed PM-correction history.  Captured
    # corrections live in ``property_pm_facts`` and feed the
    # V2 / Sandbox v2 history panel without any KG dependency.
    # Mümin 2026-05-13 (PR #E): ``/memory/timeline`` reads from
    # the PM fact store + the historical DecisionCaseStore so the
    # bootstrap-loaded archive surfaces chronologically alongside
    # PM corrections.  Without this wire-up the timeline only
    # carries PM-confirmed facts and the property's 6-month
    # archive ingested by ``POST /onboarding/bootstrap`` stays
    # invisible to the V2 timeline panel.
    configure_profile_deps(
        {
            "pm_fact_store": _pm_fact_store,
            "decision_case_store": _case_store,
        },
    )

    # ── Voice transcriber + post-Interview / Profile glue ──────────────
    # See ``api_server/bootstrap/voice.py`` for the Whisper backend
    # construction and the ``configure_interview_deps`` /
    # ``configure_profile_deps`` wiring that depends on both the
    # InterviewEngine (R14) and the freshly built transcriber.  The
    # bootstrap returns the close handle so the existing shutdown
    # branch on ``_voice_transcriber_close`` keeps working unchanged.
    global _voice_transcriber, _voice_transcriber_close
    # ``global`` for the sandbox names is hoisted here because the
    # actual rebinding lives further down in the "Sandbox backend
    # selection" section — Python forbids ``global X`` *after* any
    # read of ``X`` in the same function body, so it must precede
    # any reference to those names within ``lifespan``.
    global _sandbox_generator, _unanswered_thread_store, _sandbox_store_close
    _voice_transcriber, _voice_transcriber_close = wire_voice(
        application,
        settings=_settings,
        interview_engine=_interview_engine,
        interview_store=_interview_store,
        property_profile_store=_property_profile_store,
        unanswered_thread_store=_unanswered_thread_store,
        sandbox_generator=_sandbox_generator,
    )

    # ── V2 collaboration: card store + team mention/handoff ────────────
    # See ``api_server/bootstrap/collab.py`` for backend selection
    # (memory / postgres + memory fallback) on the card store and
    # the in-memory mention/handoff stores.  The bootstrap returns
    # the card-store close handle so the existing shutdown branch
    # on ``_card_store_close`` keeps working unchanged.
    global _card_store, _card_store_close, _mention_store, _handoff_store
    (
        _card_store,
        _card_store_close,
        _mention_store,
        _handoff_store,
    ) = await wire_collab(application)

    # ── Background reasoning: causal navigator + nightly scheduler ─────
    # See ``api_server/bootstrap/reasoning.py`` for the
    # CausalNavigationService composition (skipped when the
    # narrative service is missing) and the NightlyScheduler that
    # registers daily/monthly continual-learning jobs.  The
    # bootstrap returns the scheduler so the existing shutdown
    # branch on ``_nightly_scheduler.shutdown(wait=False)`` keeps
    # working unchanged.
    _causal_service, _nightly_scheduler = wire_reasoning(
        application,
        narrative_service=_narrative_service,
        full_system=_full_system,
        settings=_settings,
    )

    # ── V1 onboarding bootstrap (archive loader + service + harvester) ─
    # See ``api_server/bootstrap/onboarding.py`` for the three-section
    # contract.  Returns the locals the V2 OnboardingBootstrapPipeline
    # still consumes; the ``_onboarding_service`` module global is
    # mirrored so existing readers stay untouched.
    (
        _archive_loader,
        _onboarding_service,
        _profile_harvester,
    ) = wire_onboarding(
        application,
        unified_data_client=_unified_data_client,
        unified_customer_id=_unified_customer_id,
        unified_org_id=_unified_org_id,
        unified_provider_type=_unified_provider_type,
        case_store=_case_store,
        property_profile_store=_property_profile_store,
        card_store=_card_store,
    )

    # ── Sandbox backend selection (V1 onboarding step 14) ──────────────
    # Both the example-reply generator and the unanswered-thread store
    # default to in-memory so the API never 503s; flipping the env vars
    # below swaps in the LLM-backed generator and the Postgres store.
    # Failures are non-fatal — we log a warning and keep the in-memory
    # default so an ops misconfiguration cannot bring the sandbox down.
    # ``global`` for these three names is declared further up, before
    # the first read in the profile-deps wiring block, because Python
    # rejects late ``global`` declarations as a SyntaxError.
    _sandbox_store_backend = os.getenv(
        "SANDBOX_STORE_BACKEND", "memory"
    ).lower()
    if _sandbox_store_backend == "postgres":
        _sandbox_db_url = (
            os.getenv("SANDBOX_STORE_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
        if _sandbox_db_url:
            try:
                _pg_sandbox_store = await PgUnansweredThreadStore.from_url(
                    _sandbox_db_url,
                )
                _unanswered_thread_store = _pg_sandbox_store
                _sandbox_store_close = _pg_sandbox_store.close
                logger.info(
                    "Sandbox store wired (backend=postgres)",
                )
            except Exception as exc:
                logger.warning(
                    "PgUnansweredThreadStore init failed — "
                    "falling back to in-memory: %s (%s)",
                    exc,
                    type(exc).__name__,
                )
        else:
            logger.warning(
                "SANDBOX_STORE_BACKEND=postgres but no DATABASE_URL — "
                "falling back to in-memory store.",
            )

    _sandbox_generator_backend = os.getenv(
        "SANDBOX_GENERATOR_BACKEND", "template"
    ).lower()
    if _sandbox_generator_backend in {
        "anthropic", "openai", "azure_openai",
    }:
        try:
            from brain_engine.models.factory import init_chat_model

            _sandbox_default_model = {
                "anthropic": "claude-sonnet-4-6",
                "openai": "gpt-4o-mini",
                "azure_openai": os.getenv(
                    "AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini",
                ),
            }[_sandbox_generator_backend]
            _sandbox_model_id = os.getenv(
                "SANDBOX_GENERATOR_MODEL", _sandbox_default_model,
            )
            _sandbox_extra_kwargs: dict[str, Any] = {}
            if _sandbox_generator_backend == "azure_openai":
                _sandbox_extra_kwargs = {
                    "azure_endpoint": os.getenv(
                        "AZURE_OPENAI_ENDPOINT", "",
                    ),
                    "api_version": os.getenv(
                        "AZURE_OPENAI_API_VERSION", "",
                    ),
                    "api_key": os.getenv(
                        "AZURE_OPENAI_API_KEY", "",
                    ) or None,
                }
            _sandbox_chat_model = init_chat_model(
                f"{_sandbox_generator_backend}:{_sandbox_model_id}",
                **_sandbox_extra_kwargs,
            )
            _sandbox_generator = LLMExampleReplyGenerator(
                _sandbox_chat_model,
                profile_store=_property_profile_store,
            )
            logger.info(
                "Sandbox generator wired (backend=%s, model=%s)",
                _sandbox_generator_backend,
                _sandbox_model_id,
            )
        except Exception as exc:
            logger.warning(
                "LLMExampleReplyGenerator init failed — "
                "falling back to template: %s (%s)",
                exc,
                type(exc).__name__,
            )

    # Re-publish the (possibly swapped) sandbox store + generator to
    # the profile router so the unanswered-threads endpoint sees the
    # new backend and the time-aware ``preview-reply`` endpoint can
    # invoke the same generator the bootstrap pipeline uses.
    configure_profile_deps(
        {
            "unanswered_thread_store": _unanswered_thread_store,
            "sandbox_generator": _sandbox_generator,
        },
    )

    # Sprint 6 W1 + W5 bridge — build the Foundation Analysis
    # Orchestrator once at lifespan and publish it on ``app.state``
    # so every call site (bootstrap HistoricalCaseExtractor, live
    # ConversationService, /api/admin/foundation/* audit router)
    # shares the same in-memory matcher + catalog.  ``None`` when
    # the foundation markdown is missing or parses to zero
    # scenarios — every consumer treats this slot as optional and
    # falls back to its pre-W1 behaviour.
    _foundation_orchestrator: FoundationAnalysisOrchestrator | None = None
    _foundation_scenarios_count = 0
    try:
        _examples = load_foundation_examples(DEFAULT_FOUNDATION_PATH)
        if _examples:
            _catalog_store = InMemoryFoundationCatalogStore()
            _full_scenarios = load_foundation_scenarios(
                DEFAULT_FOUNDATION_PATH,
            )
            if _full_scenarios:
                await _catalog_store.upsert_many(
                    _full_scenarios,
                    doc_hash=compute_doc_hash(DEFAULT_FOUNDATION_PATH) or "",
                )
            _foundation_orchestrator = FoundationAnalysisOrchestrator(
                scenario_matcher=ScenarioMatcher(_examples),
                foundation_catalog=_catalog_store,
            )
            _foundation_scenarios_count = len(_examples)
            logger.info(
                "foundation_orchestrator.built "
                "scenarios=%d catalog_rows=%d",
                _foundation_scenarios_count,
                len(_full_scenarios),
            )
        else:
            logger.warning(
                "foundation_orchestrator.skipped — examples empty",
            )
    except Exception:
        logger.exception("foundation_orchestrator.build_failed")
    app.state.foundation_orchestrator = _foundation_orchestrator

    if _archive_loader is not None and _case_store is not None:
        # The pipeline assembly + the Redis-vs-memory event-bus /
        # job-store choice live in ``pipeline_factory`` so the Stage 2
        # bootstrap worker can build the identical pipeline out of
        # process.  Behaviour here is unchanged — same deps, same
        # constructor arguments.
        from api_server.bootstrap.pipeline_factory import (
            build_bootstrap_pipeline,
        )
        _bootstrap_pipeline, _bootstrap_job_store = build_bootstrap_pipeline(
            archive_loader=_archive_loader,
            case_store=_case_store,
            rule_store=_rule_store,
            profile_harvester=_profile_harvester,
            sandbox_generator=_sandbox_generator,
            sandbox_store=_unanswered_thread_store,
            foundation_orchestrator=_foundation_orchestrator,
            memory_fanout=_memory_fanout,
            profile_customer_id=_unified_customer_id or "",
            profile_org_id=_unified_org_id or "",
            profile_provider_type=_unified_provider_type or "",
            redis_client=_full_system.redis_client,
        )
        configure_onboarding_deps(
            {
                "onboarding_bootstrap_pipeline": _bootstrap_pipeline,
                "onboarding_job_store": _bootstrap_job_store,
            },
        )
        logger.info(
            "OnboardingBootstrapPipeline initialized "
            "(loader=%s, miner=%s, profile_harvester=%s)",
            getattr(_archive_loader, "name", None),
            _rule_store is not None,
            _profile_harvester is not None,
        )

        # Phase 3 + Phase 4 + Phase 5 in one call (see multi_tenant.py).
        from api_server.bootstrap.multi_tenant import wire_multi_tenant
        global _multi_tenant_handles
        _multi_tenant_handles = await wire_multi_tenant(
            env_customer_id=_unified_customer_id,
            env_org_id=_unified_org_id,
            env_provider_type=_unified_provider_type,
            pipeline_getter=lambda: _bootstrap_pipeline,
            profile_store=_property_profile_store,
            unified_data_client=_unified_data_client,
        )
        # Stage 1: wire the request-bootstrap endpoint against the
        # same state_store + dispatcher the Phase 4 trigger uses, so
        # the explicit UI path and the implicit middleware path dedup
        # through one property_state SSoT.  When PROPERTY_STATE_ENABLED
        # is off these are ``None`` and the endpoint answers 503.
        configure_bootstrap_intent_deps(
            {
                "state_store": _multi_tenant_handles.state_store,
                "dispatcher": _multi_tenant_handles.dispatcher,
                "pipeline_getter": lambda: _bootstrap_pipeline,
                # Self-heal: lets the dedup re-harvest a ``primed`` row
                # whose PropertyProfileStore entry is actually missing
                # (state outlived the profile after a pod restart).
                "profile_store": _property_profile_store,
            },
        )
        # Stage 1 orphan reaper: recovers warming/queued rows left
        # behind by a killed pod (they otherwise block re-attempts
        # forever via the in-flight dedup).  Sweeps once at startup
        # then periodically; only meaningful when the SSoT is wired.
        if _multi_tenant_handles.state_store is not None:
            from brain_engine.tenants import BootstrapReaper
            global _bootstrap_reaper_task
            _reaper = BootstrapReaper(_multi_tenant_handles.state_store)
            _bootstrap_reaper_task = asyncio.create_task(
                _reaper.run_forever(),
                name="bootstrap-reaper",
            )
            logger.info("bootstrap_reaper.started")
    else:
        logger.warning(
            "OnboardingBootstrapPipeline disabled "
            "(archive_loader=%s, case_store=%s)",
            getattr(_archive_loader, "name", None),
            _case_store is not None,
        )

    # Publish memory smoke dependencies once every collaborator is
    # live.  Missing pieces leave the corresponding key as ``None``
    # and the endpoint replies 503 with the missing-dep list.
    configure_memory_smoke_deps(
        {
            "archive_loader": _archive_loader,
            "case_store": _case_store,
            "rule_store": _rule_store,
            "rule_router": getattr(app.state, "rule_router", None),
            "episodic_memory": (
                _memory.episodic if _memory is not None else None
            ),
        },
    )

    # Publish the same tier handles to the read-only status router.
    # Independent of the smoke endpoint so a half-configured pod can
    # still answer the dashboard probe.
    configure_memory_status_deps(
        {
            "case_store": _case_store,
            "rule_store": _rule_store,
            "episodic_memory": (
                _memory.episodic if _memory is not None else None
            ),
        },
    )

    # Past-conversation viewer needs only the DecisionCase store —
    # refusal extraction is in-process and stateless.
    configure_past_conversations_deps(
        {"case_store": _case_store},
    )

    # A/B experiment router — only needs the registry; the
    # registry already owns its store handle.
    configure_experiments_deps(
        {"registry": _experiment_registry},
    )

    # Foundation audit router — builds a standalone
    # FoundationAnalysisOrchestrator from the in-memory matcher
    # already constructed by ``build_intelligent_classifier`` so
    # operators / PMs can POST sample text and inspect the
    # foundation decision via ``/api/admin/foundation/analyze``
    # without touching the live conversation pipeline.  When the
    # foundation markdown is missing (rare — see PR #285) the
    # orchestrator stays ``None`` and the audit endpoint reports
    # ``ready: false`` instead of crashing.
    # Foundation audit router shares the same orchestrator built
    # earlier in this lifespan.  No second copy of the matcher.
    # case_store + rule_store are forwarded too so the /coverage
    # endpoint can compute provenance ratios per property without
    # spinning up its own connection pool.
    configure_foundation_audit_deps(
        {
            "orchestrator": _foundation_orchestrator,
            "scenarios_count": _foundation_scenarios_count,
            "foundation_path": str(DEFAULT_FOUNDATION_PATH),
            "case_store": _case_store,
            "rule_store": _rule_store,
        },
    )

    # Phase 3 PR3b.1 — activate the read-only temporal analysis endpoint
    # by injecting the timeline stores + chat model.  Gated by the
    # default-off BRAIN_TEMPORAL_ANALYSIS_ENABLED flag, so this is inert
    # until an operator flips it.
    wire_temporal_analysis(application, settings=_settings, memory=_memory)

    _brain_engine_ready = True
    logger.info("Brain engine initialized and ready.")

    # Start Telegram polling in background
    polling_task = None
    if _telegram_bot:
        # Delete any existing webhook first (required for polling)
        await _telegram_bot.delete_webhook()
        polling_task = asyncio.create_task(_telegram_polling_loop())
        logger.info("Telegram polling started.")

    yield

    logger.info("Shutting down Airbnb Brain Engine AG-UI server...")
    if _nightly_scheduler is not None:
        _nightly_scheduler.shutdown(wait=False)
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    if _full_system:
        await _full_system.shutdown()
    if _memory:
        await _memory.shutdown()
    if _case_store_close is not None:
        await _case_store_close()
        logger.info("DecisionCase store closed")
    if _rule_store_close is not None:
        await _rule_store_close()
        logger.info("PatternRule store closed")
    if _experiment_store_close is not None:
        await _experiment_store_close()
        logger.info("Experiment store closed")
    if _blocker_store_close is not None:
        await _blocker_store_close()
        logger.info("Blocker store closed")
    if _autonomy_store_close is not None:
        await _autonomy_store_close()
        logger.info("Autonomy store closed")
    if _interview_store_close is not None:
        await _interview_store_close()
        logger.info("Interview answer store closed")
    if _card_store_close is not None:
        await _card_store_close()
        logger.info("Card store closed")
    if _sandbox_store_close is not None:
        try:
            await _sandbox_store_close()
            logger.info("Sandbox store closed")
        except Exception as exc:
            logger.warning("Sandbox store close failed: %s", exc)
    if _bootstrap_reaper_task is not None:
        # Stop the reaper before releasing the pool it queries.
        _bootstrap_reaper_task.cancel()
        try:
            await _bootstrap_reaper_task
        except asyncio.CancelledError:
            logger.info("bootstrap_reaper.stopped")
        except Exception as exc:
            logger.warning("bootstrap_reaper stop failed: %s", exc)
    if _multi_tenant_handles is not None and _multi_tenant_handles.close is not None:
        try:
            await _multi_tenant_handles.close()
            logger.info("Tenant registry pool closed")
        except Exception as exc:
            logger.warning(
                "Tenant registry close failed: %s (%s)",
                exc,
                type(exc).__name__,
            )
    if _property_profile_store_close is not None:
        try:
            await _property_profile_store_close()
            logger.info("Property profile store closed")
        except Exception as exc:
            logger.warning("Property profile store close failed: %s", exc)
    if _pm_fact_store_close is not None:
        try:
            await _pm_fact_store_close()
            logger.info("PM fact store closed")
        except Exception as exc:
            logger.warning("PM fact store close failed: %s", exc)
    if _owner_profile_store_close is not None:
        try:
            await _owner_profile_store_close()
            logger.info("Owner profile store closed")
        except Exception as exc:
            logger.warning("Owner profile store close failed: %s", exc)
    if _voice_transcriber_close is not None:
        await _voice_transcriber_close()
        logger.info("Voice transcriber closed")
    if _unified_data_client is not None:
        await _unified_data_client.aclose()
        logger.info("UnifiedDataGraphQLClient closed")
    if _negotiation_manager is not None:
        await _negotiation_manager.close_all()
        logger.info("NegotiationSessionManager closed")
    if _elevenlabs_client:
        await _elevenlabs_client.close()
    if _telegram_bot:
        await _telegram_bot.close()
    _brain_engine_ready = False


# ── FastAPI application ───────────────────────────────────────────────────────
_API_TAGS_METADATA = [
    {
        "name": "conversation",
        "description": "Guest Conversation Pipeline — Full guest messaging AI with 8-stage pipeline: "
        "preprocess → classify (18 business flags) → guardrails (ALWAYS/CONTEXTUAL) → "
        "prompt assembly (16 tones) → ReAct agent (9 tools) → post-process (tags, sentiment, tasks) → "
        "response assembly. Multi-tenant with per-customer settings (Redis-cached). "
        "Includes regenerate, RAG, upsell, reviews, WhatsApp, rule creation, and OPS coordination.",
    },
    {
        "name": "Guest Agent",
        "description": "Replaces Cendra's Guest Agent (LangGraph ReAct). "
        "Handles inbound guest messages with classification, RAG, guardrails, and auto-reply. "
        "Supports Cendra contract 16.1/16.2 fields: customer_id, workspace_id, correlation_id, "
        "property_context, reservation_context, tool_preferences.",
    },
    {
        "name": "Ops Agent",
        "description": "Replaces Cendra's OpsSessionWorkflow (963 lines). "
        "Manages contact cascade, multi-turn conversations, cost negotiation, PM approval. "
        "Extended contact types: cleaner, vendor, guest, owner, insurance, legal, custom.",
    },
    {
        "name": "Rules",
        "description": "Procedural Rules CRUD API. Create, read, update, delete behavioral rules per property. "
        "3 source types with priority: immutable > manual > sop > learned. "
        "10 categories: guest_communication, escalation, operations, timing, pricing, automation, "
        "vendor, safety, upsell, cleaning. Immutable rules cannot be modified or deleted.",
    },
    {
        "name": "Templates",
        "description": "Rule template system for rapid customer onboarding. "
        "Create templates with predefined rules, apply to single property or bulk-apply to multiple. "
        "Example: 'Turkey Standard' template with 10 rules applied to 50 properties at once.",
    },
    {
        "name": "Processes",
        "description": "Active process management (7th memory tier). Track ongoing operations: "
        "cleaning, maintenance, sales, complaints. Multi-participant support (any role). "
        "Process replies from cleaners, vendors, guests, owners, insurance, legal.",
    },
    {
        "name": "Learning",
        "description": "Adaptive autonomy, skill evolution, and memory consolidation. "
        "Brain Engine learns from every interaction WITHOUT training/fine-tuning. "
        "WorkGraph event consumer for learning from Cendra's event stream.",
    },
    {
        "name": "Intelligence",
        "description": "Accumulated knowledge per property: skills, scoring, city maturity, guest intelligence.",
    },
    {
        "name": "System",
        "description": "Health checks, metrics, and operational endpoints.",
    },
    {
        "name": "Knowledge",
        "description": "Knowledge base synchronization. Import/export property knowledge entries "
        "(SOPs, property info, house rules) for RAG-powered guest responses.",
    },
    {
        "name": "Automation",
        "description": "Event-driven automation rules. 9 templates: access codes, climate control, "
        "checkout cleanup, HVAC, night mode, security, review requests, between-bookings.",
    },
    {
        "name": "IoT",
        "description": "Smart device event processing. Handles smart lock, thermostat, sensor events. "
        "Detects anomalies (unexpected unlock, temperature spikes) and triggers actions.",
    },
    {
        "name": "Upsell",
        "description": "Revenue optimization engine. 4 upsell types: early check-in, late checkout, "
        "gap night filling, late check-in. Auto-applicable based on guest loyalty score.",
    },
    {
        "name": "Booking",
        "description": "Autonomous booking lifecycle management. From new booking to checkout: "
        "risk assessment, access codes, welcome messages, cleaning cascade, turnover coordination.",
    },
    {
        "name": "Approval",
        "description": "Owner approval workflow for high-value decisions. Approve/reject with comments. "
        "Auto-approve rules, urgency timeouts, WhatsApp/Telegram notifications. "
        "Each decision teaches Brain Engine to make better autonomous decisions.",
    },
    {
        "name": "Voice",
        "description": "ElevenLabs voice calling integration. Outbound phone calls to cleaners, vendors, "
        "guests. Transcript retrieval, call status tracking. Used by Cleaning Cascade for autonomous dispatch.",
    },
    {
        "name": "Telegram",
        "description": "Telegram bot integration. Receive cleaner photos, approval decisions, "
        "self-registration. Send notifications to managers and owners.",
    },
    {
        "name": "Scenario",
        "description": "Full turnover scenario orchestrator. 22-phase autonomous flow: "
        "call incoming guest → offer late checkout to departing guest → cleaner cascade → "
        "photo inspection → damage claims → vendor dispatch → payment → final verification. "
        "Supports dry_run (simulated) and real mode (ElevenLabs voice calls). "
        "Returns SSE stream of AG-UI events with real-time call transcripts, state transitions, "
        "slot fills, sentiment updates, and reasoning steps.",
    },
]

app = FastAPI(
    title="Brain Engine API",
    description=(
        "# Brain Engine — Autonomous Cognitive Brain for Cendra AI\n\n"
        "Brain Engine receives events (guest messages, bookings, ops events, contact replies) "
        "and returns **decisions + MCP tool calls**. Cendra executes actions through its infrastructure.\n\n"
        "## Key Capabilities\n"
        "- **Dual-Process Reasoning**: L1 (fast/FAQ) → L4 (deep strategy)\n"
        "- **7-Tier Cognitive Memory**: Working → Episodic → Semantic → Procedural → KG → Guest History → Active Processes\n"
        "- **Skill Evolution**: Learns from failures WITHOUT training/fine-tuning (frozen weights)\n"
        "- **Adaptive Autonomy**: L1 Suggest → L2 Act&Inform → L3 Silent → L4 Act&Learn\n"
        "- **Zero-Trust Safety**: Explicit stakeholder model, immutable rules, 6-layer guardrails\n"
        "- **Rules CRUD API**: Manual/immutable/SOP rules per property with template onboarding\n"
        "- **Proactive Scheduler**: Follow-up system for autonomous process management\n"
        "- **Multi-Tenancy**: Workspace-scoped memory isolation (Customer → Workspace → Property)\n"
        "- **Cendra Integration**: Azure Cognitive Search RAG + WorkGraph event consumer\n"
        "- **Scenario Flow**: 22-phase autonomous turnover with real ElevenLabs voice calls + SSE streaming\n\n"
        "## Architecture\n"
        "```\n"
        "Cendra → Brain Engine API → Decision + MCP Tool Calls → Cendra executes via Temporal\n"
        "```\n\n"
        "## API Groups\n"
        "- **Conversation Pipeline** — Full guest messaging AI (26 endpoints, 18 business flags, 9 ReAct tools)\n"
        "- **OPS Pipeline** — Operations coordination: generate/parse/classify/verify messages + PM agent\n"
        "- **Rule Creation** — Brain Engine workflow: 6-phase conversation with foundation-aware discovery\n"
        "- **Guest Agent** — Guest message processing\n"
        "- **Ops Agent** — Operational event handling + cleaning cascade\n"
        "- **Booking** — Autonomous booking lifecycle (risk, codes, welcome, turnover)\n"
        "- **Rules** — Procedural rules CRUD (5 endpoints)\n"
        "- **Templates** — Onboarding templates (7 endpoints)\n"
        "- **Processes** — Active process tracking (3 endpoints)\n"
        "- **Knowledge** — KB sync (import/export SOPs, property info)\n"
        "- **Automation** — Event-driven automation (9 templates)\n"
        "- **IoT** — Smart device events (locks, thermostats, sensors)\n"
        "- **Upsell** — Revenue optimization (4 upsell types)\n"
        "- **Approval** — Owner approval workflow + learning\n"
        "- **Voice** — ElevenLabs outbound calls + transcripts\n"
        "- **Telegram** — Bot integration for ops communication\n"
        "- **Scenario** — Full turnover flow (22 phases, real voice calls, SSE stream)\n"
        "- **Learning** — Skill evolution, consolidation\n"
        "- **Intelligence** — Property knowledge + guest scoring\n"
        "- **System** — Health, metrics, validation\n\n"
        "## Authentication\n"
        "All endpoints (except `/health`) require `X-API-Key` header.\n"
    ),
    version="3.2.0",
    lifespan=lifespan,
    openapi_tags=_API_TAGS_METADATA,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Apply middleware stack
setup_middleware(app)

# Mount Cendra API router (Blueprint v5 endpoints)
app.include_router(cendra_router)

# Mount Workflow router (cleaner dispatch, PMS approval, vendor ops)
app.include_router(workflow_router)

# Mount Interview router (V2 proactive PM Q&A: next-question, answer,
# coverage).  Dependencies are injected from lifespan via
# configure_interview_deps so the router stays import-safe.
app.include_router(interview_router)

# Mount Decision-Card router (V2 five-slot UI artefact lifecycle).
# CardStore is injected at lifespan start via configure_card_deps.
app.include_router(card_router)

# Mount Memory router (PM-authored fact CRUD over FactStore).
# Dependencies are injected from lifespan via configure_memory_deps.
app.include_router(memory_router)

# Mount Team router (mentions + handoffs).  Stores are wired at
# lifespan start via configure_team_deps.
app.include_router(team_router)

# OnboardingBootstrapPipeline is injected at lifespan start via
# configure_onboarding_deps.
app.include_router(onboarding_router)
app.include_router(bootstrap_intent_router)

# Mount Property profile + sandbox router.  Serves the V2 onboarding
# knowledge card and the 3-question sandbox preview.  Dependencies are
# injected from lifespan via configure_profile_deps.
app.include_router(profile_router)

# Mount Conversation pipeline router (guest messaging + OPS)
from brain_engine.api.conversation_endpoints import (
    router as conversation_router,
)

app.include_router(conversation_router)

# Mount Pattern router — DecisionCase / PatternRule / Blocker / Calendar
# REST surface used by the Cendra dashboard.  The router currently owns
# module-level in-memory stores; migrating to the injected Postgres
# stores used by the rest of the server is tracked as tech-debt.
app.include_router(pattern_router)

# Mount Intelligence router — upsell evaluation, sentiment / escalation
# / accuracy analytics and natural-language rule creation.  Like the
# pattern router it wires module-level in-memory stores for now.
app.include_router(intelligence_router)

# Mount Prometheus metrics router — exposes ``/metrics`` for the
# observability scraper.  See brain_engine_advisory.md §5 and ADR-0020.
# Network-level access is restricted via NetworkPolicy so the route
# does not need an auth gate.
from api_server.routers import metrics_router

app.include_router(metrics_router)

# Mount real-data memory + patterns smoke harness — exposes
# ``/api/admin/memory/smoke/{property_id}``.  Dependencies are
# published from the lifespan once the GraphQL loader, case store,
# rule store, rule router and episodic memory tier are all live.
from api_server.routers import (
    configure_experiments_deps,
    configure_foundation_audit_deps,
    configure_memory_smoke_deps,
    configure_memory_status_deps,
    configure_past_conversations_deps,
    experiments_router,
    foundation_audit_router,
    memory_smoke_router,
    memory_status_router,
    past_conversations_router,
)

app.include_router(memory_smoke_router)
app.include_router(memory_status_router)
app.include_router(past_conversations_router)
app.include_router(experiments_router)
app.include_router(foundation_audit_router)
app.include_router(temporal_analysis_router)

# Internal Test UI was retired with the Botel PMS REST adapter on
# 2026-04-28.  The router and its static bundle relied on
# ``BotelPMSClient`` / ``api_server.test_ui_router`` — both removed
# when Brain Engine collapsed onto the onboarding-api unified
# GraphQL gateway.  ``BRAIN_ENGINE_TEST_UI_ENABLED`` is now a no-op.


@app.post("/")
async def run_agent(request: Request) -> StreamingResponse:
    """AG-UI protocol endpoint.

    Accepts a RunAgentInput payload and returns a streaming SSE response
    containing AG-UI events (RUN_STARTED, TEXT_MESSAGE_*, STATE_DELTA,
    TOOL_CALL_*, RUN_FINISHED).

    The CopilotKit frontend consumes this stream to render real-time
    agent activity in the UI.
    """
    body = await request.json()
    run_input = RunAgentInput(**body)

    return StreamingResponse(
        _run_agent_stream(run_input, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health_check() -> dict[str, str | bool]:
    """Health check endpoint for monitoring and load balancers."""
    return {
        "status": "healthy",
        "brain_engine_ready": _brain_engine_ready,
        "memory_system": _memory is not None,
        "elevenlabs_configured": _elevenlabs_client is not None,
        "elevenlabs_api_key_set": bool(_settings and _settings.elevenlabs_api_key),
        "elevenlabs_agent_id_set": bool(_settings and _settings.elevenlabs_agent_id),
        "elevenlabs_phone_number_id_set": bool(_settings and _settings.elevenlabs_phone_number_id),
        "telegram_configured": _telegram_bot is not None,
        "version": "0.2.0",
    }


# ── Voice call endpoints temporarily disabled ───────────────────────────────


# ── Telegram webhook endpoints ───────────────────────────────────────────────


@app.post("/api/telegram/webhook", tags=["Telegram"])
async def telegram_webhook(request: Request):
    """Receive updates from Telegram Bot API (for HTTPS setups)."""
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if _settings and secret != _settings.telegram_webhook_secret:
        return JSONResponse(status_code=403, content={"error": "Invalid secret"})

    body = await request.json()
    message = body.get("message")
    if message:
        await _handle_telegram_message(message)
    return {"ok": True}


@app.get("/api/telegram/photos", tags=["Telegram"])
async def get_received_photos():
    """Get all photos received from cleaners."""
    return {"photos": _received_photos, "cleaners": _registered_cleaners}


@app.post("/api/telegram/send", tags=["Telegram"])
async def send_telegram_message(request: Request):
    """Send a message to a cleaner via Telegram.

    Body: {"chat_id": "123456", "text": "Your assignment..."}
    """
    if not _telegram_bot:
        return JSONResponse(status_code=503, content={"error": "Telegram bot not configured"})

    body = await request.json()
    chat_id = body.get("chat_id", "")
    text = body.get("text", "")

    if not chat_id or not text:
        return JSONResponse(status_code=400, content={"error": "chat_id and text required"})

    try:
        result = await _telegram_bot.send_message(chat_id=int(chat_id), text=text)
        return {"ok": True, "message_id": result.message_id}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Damage Claims (Demo API) ────────────────────────────────────────────────


@app.post("/api/claims", tags=["Ops Agent"])
async def submit_claim(request: Request):
    """Submit a damage claim (demo API).

    Body: {
        "reservation_id": "RES001",
        "guest_name": "John",
        "property_id": "PROP001",
        "damage_description": "Cracked TV screen in living room",
        "severity": 4,
        "estimated_cost": 500.00,
        "photo_file_ids": ["file_id_1", "file_id_2"],
        "items": [{"name": "TV", "cost": 500}]
    }
    """
    body = await request.json()
    claim_id = f"CLM-{uuid.uuid4().hex[:8].upper()}"

    from datetime import datetime, timedelta
    now = datetime.utcnow()

    claim = {
        "claim_id": claim_id,
        "reservation_id": body.get("reservation_id", ""),
        "guest_name": body.get("guest_name", ""),
        "property_id": body.get("property_id", ""),
        "damage_description": body.get("damage_description", ""),
        "severity": body.get("severity", 1),
        "estimated_cost": body.get("estimated_cost", 0),
        "photo_file_ids": body.get("photo_file_ids", []),
        "items": body.get("items", []),
        "status": "submitted",
        "submitted_at": now.isoformat() + "Z",
        "deadline": (now + timedelta(hours=24)).isoformat() + "Z",
        "timeline": [
            {"event": "claim_submitted", "timestamp": now.isoformat() + "Z"},
        ],
    }
    _claims[claim_id] = claim

    logger.info(
        "Claim %s submitted: %s — $%.2f",
        claim_id, claim["damage_description"], claim["estimated_cost"],
    )

    return {
        "claim_id": claim_id,
        "status": "submitted",
        "message": f"Claim {claim_id} submitted successfully. Review within 24 hours.",
        "deadline": claim["deadline"],
    }


@app.get("/api/claims", tags=["Ops Agent"])
async def list_claims():
    """List all submitted claims."""
    return {"claims": list(_claims.values()), "total": len(_claims)}


@app.get("/api/claims/{claim_id}", tags=["Ops Agent"])
async def get_claim(claim_id: str):
    """Get details of a specific claim."""
    claim = _claims.get(claim_id)
    if not claim:
        return JSONResponse(status_code=404, content={"error": "Claim not found"})
    return claim


@app.put("/api/claims/{claim_id}/status", tags=["Ops Agent"])
async def update_claim_status(claim_id: str, request: Request):
    """Update claim status (demo).

    Body: {"status": "under_review" | "approved" | "denied" | "paid"}
    """
    claim = _claims.get(claim_id)
    if not claim:
        return JSONResponse(status_code=404, content={"error": "Claim not found"})

    body = await request.json()
    new_status = body.get("status", "")
    valid = ["submitted", "under_review", "approved", "denied", "paid"]
    if new_status not in valid:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid status. Must be one of: {valid}"},
        )

    from datetime import datetime
    now = datetime.utcnow()

    claim["status"] = new_status
    claim["timeline"].append({
        "event": f"status_changed_to_{new_status}",
        "timestamp": now.isoformat() + "Z",
    })

    logger.info("Claim %s status updated to %s", claim_id, new_status)
    return {"claim_id": claim_id, "status": new_status, "message": "Status updated"}


# ── Memory System API ──────────────────────────────────────────────────────


@app.post("/api/memory/process", tags=["Learning"])
async def process_cognitive_event(request: Request):
    """Process an event through the full cognitive cycle (CoALA).

    Perceive → Remember → Reason → Act

    Body: {"event": "damage_detected", "content": "...", "metadata": {...}}
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    body = await request.json()
    result = await _memory.cognitive.process(
        event=body.get("event", ""),
        content=body.get("content", ""),
        metadata=body.get("metadata"),
    )
    return result


@app.post("/api/memory/record", tags=["Learning"])
async def record_event(request: Request):
    """Record an event to episodic memory + guest history.

    Body: {"event": "guest_identified", "data": {...}}
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    body = await request.json()
    event = body.get("event", "")
    data = body.get("data", {})

    recorder = _memory.event_recorder

    if event == "guest_identified":
        guest_id = await recorder.record_guest_identified(**data)
        return {"guest_id": guest_id, "event": event}
    elif event == "incident_started":
        incident_id = await recorder.record_incident_started(**data)
        return {"incident_id": incident_id, "event": event}
    elif event == "damage_detected":
        await recorder.record_damage_detected(**data)
        return {"event": event, "recorded": True}
    elif event == "claim_submitted":
        await recorder.record_claim_submitted(**data)
        return {"event": event, "recorded": True}
    elif event == "late_checkout_requested":
        await recorder.record_late_checkout_requested(**data)
        return {"event": event, "recorded": True}
    elif event == "cleaner_assigned":
        await recorder.record_cleaner_assigned(**data)
        return {"event": event, "recorded": True}
    elif event == "incident_resolved":
        await recorder.record_incident_resolved(**data)
        return {"event": event, "recorded": True}
    else:
        await recorder.record_incident_update(event=event, details=data.get("details", ""))
        return {"event": event, "recorded": True}


@app.get("/api/memory/guest/{guest_id}", tags=["Intelligence"])
async def get_guest_memory(guest_id: str):
    """Get full memory context about a guest.

    Combines: guest history + knowledge graph facts/beliefs + episodic events.
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    guest_ctx = await _memory.guest_history.build_guest_context(guest_id)
    kg_ctx = await _memory.knowledge_graph.build_entity_context(guest_id)

    return {
        "guest_id": guest_id,
        "guest_history": guest_ctx,
        "knowledge_graph": kg_ctx,
    }


@app.get(
    "/v2/properties/{property_id}/trust-meter",
    tags=["Autonomy V2"],
)
async def get_property_trust_meter(property_id: str) -> JSONResponse:
    """Return the per-workflow Trust Meter band for a property.

    Response shape mirrors :class:`TrustMeterView`: a top-level
    ``property_id`` + ``generated_at`` plus an ordered ``bands`` list.
    Each band carries the canonical metric snapshot the engine drove
    its last decision on plus a ``progress`` block with the per-metric
    conditions gating the next state.

    Returns 503 when the autonomy stack failed to initialize so the V2
    UI can degrade explicitly instead of rendering a half-empty band.
    """
    if _trust_meter_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Trust Meter service not initialized"},
        )
    view = await _trust_meter_service.for_property(property_id)
    return JSONResponse(content=_trust_meter_to_dict(view))


def _trust_meter_to_dict(view: TrustMeterView) -> dict[str, Any]:
    """Serialize a :class:`TrustMeterView` to a JSON-safe dict.

    Kept module-level (not inlined) so future endpoints — e.g. a
    history fan-out — can reuse the exact same shape without drifting.
    """
    return {
        "property_id": view.property_id,
        "generated_at": view.generated_at.isoformat(),
        "bands": [
            {
                "workflow": band.workflow,
                "state": band.state.value,
                "sample_size": band.sample_size,
                "success_rate": band.success_rate,
                "override_rate": band.override_rate,
                "incidents": band.incidents,
                "mean_latency_seconds": band.mean_latency_seconds,
                "hold_seconds": band.hold_seconds,
                "changed_at": band.changed_at.isoformat(),
                "changed_by": band.changed_by,
                "reason": band.reason,
                "progress": {
                    "target_state": (
                        band.progress.target_state.value
                        if band.progress.target_state is not None
                        else None
                    ),
                    "satisfied_count": band.progress.satisfied_count,
                    "total": band.progress.total,
                    "all_satisfied": band.progress.all_satisfied,
                    "conditions": [
                        {
                            "name": c.name,
                            "current": c.current,
                            "target": c.target,
                            "comparator": c.comparator,
                            "satisfied": c.satisfied,
                        }
                        for c in band.progress.conditions
                    ],
                },
            }
            for band in view.bands
        ],
    }


@app.get("/api/memory/property/{property_id}", tags=["Intelligence"])
async def get_property_memory(property_id: str):
    """Get full memory context about a property."""
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    prop_ctx = await _memory.guest_history.build_property_context(property_id)
    kg_ctx = await _memory.knowledge_graph.build_entity_context(property_id)

    return {
        "property_id": property_id,
        "property_history": prop_ctx,
        "knowledge_graph": kg_ctx,
    }


_NARRATIVE_VALID_FORMATS = frozenset(
    {"json", "text", "voice", "voice_stream"}
)


def _parse_render_style(value: str) -> RenderStyle:
    """Return the :class:`RenderStyle` for ``value`` or raise ``ValueError``."""
    normalized = value.strip().lower()
    for style in RenderStyle:
        if style.value == normalized:
            return style
    raise ValueError(f"unsupported style: {value}")


def _parse_iso_or_none(value: str | None) -> Any:
    """Parse an ISO-8601 timestamp, returning ``None`` when unset."""
    if value is None:
        return None
    from datetime import datetime

    return datetime.fromisoformat(value)


def _narrative_json_payload(narrative: Any) -> dict[str, Any]:
    """Serialise a :class:`Narrative` to a JSON-friendly dict."""
    return {
        "text": narrative.text,
        "event_count": len(narrative.events),
        "range": {
            "since": narrative.range.since.isoformat(),
            "until": narrative.range.until.isoformat(),
        },
        "events": [
            {
                "occurred_at": event.occurred_at.isoformat(),
                "kind": event.kind.value,
                "summary": event.summary,
                "source": event.source,
                "native_id": event.native_id,
                "property_id": event.property_id,
                "property_name": event.property_name,
            }
            for event in narrative.events
        ],
        "meta": dict(narrative.meta),
    }


@app.get(
    "/api/memory/property/{property_id}/timeline",
    tags=["Intelligence"],
)
async def get_property_timeline(
    property_id: str,
    format: str = "text",
    range_days: int = 90,
    since: str | None = None,
    until: str | None = None,
    include_ops: bool = True,
    style: str = "concise",
    use_llm: bool = False,
    customer_id: str | None = None,
    reservation_id: str | None = None,
    guest_id: str | None = None,
    property_label: str = "",
    with_causal: bool = False,
):
    """Return a property's event timeline as JSON, plain text or audio.

    Query parameters:
        format: ``json`` | ``text`` | ``voice`` (default ``text``).
        range_days: Look-back window when ``since`` is omitted.
        since / until: ISO-8601 overrides for the window bounds.
        include_ops: When ``false`` drops ``EventKind.OPS`` rows.
        style: ``concise`` | ``full`` narrative verbosity.
        use_llm: Opt-in LLM rewrite (silently no-ops when unavailable).
        customer_id / reservation_id / guest_id: Optional scoping.
        property_label: Friendly name shown in the opening sentence.
        with_causal: When ``true`` and the causal service is available,
            attach a serialised causal graph derived from the narrative
            events under ``meta.causal_graph``.  Silently skipped for
            ``voice``/``voice_stream`` responses.
    """
    fmt = format.strip().lower()
    if fmt not in _NARRATIVE_VALID_FORMATS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported format: {format}"},
        )
    if _narrative_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Narrative service not initialized"},
        )
    try:
        render_style = _parse_render_style(style)
        since_dt = _parse_iso_or_none(since)
        until_dt = _parse_iso_or_none(until)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    timeline_range = TimelineRange.from_params(
        days=range_days,
        since=since_dt,
        until=until_dt,
    )

    try:
        if fmt == "voice":
            if not _narrative_service.voice_available:
                return JSONResponse(
                    status_code=503,
                    content={"error": "Voice renderer not configured"},
                )
            narrative, audio, content_type = await _narrative_service.build_voice(
                property_id=property_id,
                range=timeline_range,
                customer_id=customer_id,
                reservation_id=reservation_id,
                guest_id=guest_id,
                include_ops=include_ops,
                property_label=property_label,
                style=render_style,
                use_llm=use_llm,
            )
            return Response(
                content=audio,
                media_type=content_type,
                headers={"X-Event-Count": str(len(narrative.events))},
            )

        if fmt == "voice_stream":
            if not _narrative_service.voice_available:
                return JSONResponse(
                    status_code=503,
                    content={"error": "Voice renderer not configured"},
                )
            narrative, audio_iter, content_type = (
                await _narrative_service.stream_voice(
                    property_id=property_id,
                    range=timeline_range,
                    customer_id=customer_id,
                    reservation_id=reservation_id,
                    guest_id=guest_id,
                    include_ops=include_ops,
                    property_label=property_label,
                    style=render_style,
                    use_llm=use_llm,
                )
            )
            return StreamingResponse(
                audio_iter,
                media_type=content_type,
                headers={"X-Event-Count": str(len(narrative.events))},
            )

        narrative = await _narrative_service.build_json(
            property_id=property_id,
            range=timeline_range,
            customer_id=customer_id,
            reservation_id=reservation_id,
            guest_id=guest_id,
            include_ops=include_ops,
            property_label=property_label,
            style=render_style,
            use_llm=use_llm,
        )
    except VoiceSynthesisUnavailable as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    except NarrativeError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})

    payload = _narrative_json_payload(narrative)
    if with_causal and _causal_service is not None:
        try:
            graph = await _causal_service.build_graph(narrative.events)
            payload["meta"]["causal_graph"] = _causal_graph_payload(graph)
        except Exception:
            logger.warning("timeline_causal_attach_failed", exc_info=True)

    if fmt == "text":
        return {
            "property_id": property_id,
            "text": payload["text"],
            "event_count": payload["event_count"],
            "range": payload["range"],
            "meta": payload["meta"],
        }
    return {"property_id": property_id, **payload}


# ---------------------------------------------------------------------------
# GAP L — decision evidence endpoint
# ---------------------------------------------------------------------------


def _evidence_bundle_payload(bundle: EvidenceBundle) -> dict[str, Any]:
    """Serialise an :class:`EvidenceBundle` to a JSON-friendly dict."""
    summary = bundle.summary
    return {
        "bundle_id": bundle.bundle_id,
        "decision_id": bundle.query.decision_id,
        "reference": bundle.query.reference.value,
        "assembled_at": bundle.assembled_at.isoformat(),
        "summary": {
            "rule_count": summary.rule_count,
            "case_count": summary.case_count,
            "prompt_count": summary.prompt_count,
            "blocker_count": summary.blocker_count,
            "supporting_cases": summary.supporting_cases,
            "contradicting_cases": summary.contradicting_cases,
            "has_hard_blocker": summary.has_hard_blocker,
        },
        "rules": [
            {
                "pattern_id": r.pattern_id,
                "scenario": r.scenario,
                "scope": r.scope,
                "scope_id": r.scope_id,
                "confidence": r.confidence,
                "support_count": r.support_count,
                "counterexample_ratio": r.counterexample_ratio,
                "risk_level": r.risk_level,
                "execution_mode": r.execution_mode,
                "weight": r.weight.value,
                "action_type": r.action_type,
            }
            for r in bundle.rules
        ],
        "cases": [
            {
                "case_id": c.case_id,
                "scenario": c.scenario,
                "stage": c.stage,
                "decision_type": c.decision_type,
                "weight": c.weight.value,
                "resolution_type": c.resolution_type,
                "revenue_impact": c.revenue_impact,
                "occurred_at": (
                    c.occurred_at.isoformat()
                    if c.occurred_at is not None
                    else None
                ),
            }
            for c in bundle.cases
        ],
        "prompts": [
            {
                "prompt_id": p.prompt_id,
                "source": p.source,
                "kind": p.kind,
                "text": p.text,
                "relevance": p.relevance,
                "reference_id": p.reference_id,
            }
            for p in bundle.prompts
        ],
        "blockers": [
            {
                "blocker_id": b.blocker_id,
                "blocker_type": b.blocker_type,
                "severity": b.severity,
                "reason": b.reason,
                "introduced_at": (
                    b.introduced_at.isoformat()
                    if b.introduced_at is not None
                    else None
                ),
                "resolves_on": b.resolves_on,
            }
            for b in bundle.blockers
        ],
        "errors": list(bundle.errors),
        "meta": dict(bundle.meta),
    }


@app.get(
    "/api/decisions/{decision_id}/evidence",
    tags=["Intelligence"],
)
async def get_decision_evidence(
    decision_id: str,
    scenario: str | None = None,
    property_id: str | None = None,
    owner_id: str | None = None,
    guest_id: str | None = None,
    limit: int = 10,
):
    """Return the evidence bundle for a decision.

    Fans out across the pattern-rule store, the decision-case store,
    and (in future commits) the memory-prompt + blocker sources to
    build a single JSON payload the UI renders as the "why" screen.

    Query parameters:
        scenario: Optional scenario key (e.g. ``discount_request``)
            narrowing the case / rule search.
        property_id: Optional property scope.
        owner_id: Optional owner scope.
        guest_id: Optional guest scope.
        limit: Max picks per category (default 10, must be > 0).
    """
    if _evidence_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Evidence service not initialized"},
        )
    if limit <= 0:
        return JSONResponse(
            status_code=400,
            content={"error": "limit must be a positive integer"},
        )
    query = EvidenceQuery(
        decision_id=decision_id,
        scenario=scenario,
        property_id=property_id,
        owner_id=owner_id,
        guest_id=guest_id,
        limit=limit,
    )
    try:
        bundle = await _evidence_service.compose(query)
    except EvidenceError as exc:
        logger.warning("evidence.compose_failed error=%s", exc)
        return JSONResponse(
            status_code=503,
            content={"error": str(exc)},
        )
    return _evidence_bundle_payload(bundle)


# ---------------------------------------------------------------------------
# Gap #3 — property causal navigation endpoint
# ---------------------------------------------------------------------------


_CAUSAL_DIRECTIONS: frozenset[str] = frozenset({"ancestors", "descendants"})


def _causal_event_dict(event: Any) -> dict[str, Any]:
    """Serialise a :class:`TimelineEvent` to a JSON-friendly dict."""
    return {
        "event_key": causal_event_key(event),
        "occurred_at": event.occurred_at.isoformat(),
        "kind": event.kind.value,
        "summary": event.summary,
        "source": event.source,
        "native_id": event.native_id,
        "property_id": event.property_id,
        "property_name": event.property_name,
    }


def _causal_edge_dict(edge: CausalEdge) -> dict[str, Any]:
    return {
        "source_key": edge.source_key,
        "target_key": edge.target_key,
        "kind": edge.kind.value,
        "confidence": edge.confidence,
        "reason": edge.reason,
        "inferred_by": edge.inferred_by,
    }


def _causal_graph_payload(graph: CausalGraph) -> dict[str, Any]:
    return {
        "events": [_causal_event_dict(e) for e in graph.events],
        "edges": [_causal_edge_dict(e) for e in graph.edges],
        "meta": dict(graph.meta),
    }


def _causal_chain_payload(chain: CausalChain) -> dict[str, Any]:
    return {
        "anchor_key": chain.anchor_key,
        "direction": chain.direction,
        "depth": chain.depth,
        "leaf_key": chain.leaf_key,
        "edges": [_causal_edge_dict(e) for e in chain.edges],
    }


_CAUSAL_CSV_EVENT_HEADER: tuple[str, ...] = (
    "row_type",
    "event_key",
    "occurred_at",
    "kind",
    "source",
    "native_id",
    "property_id",
    "summary",
)

_CAUSAL_CSV_EDGE_HEADER: tuple[str, ...] = (
    "row_type",
    "source_key",
    "target_key",
    "kind",
    "confidence",
    "inferred_by",
    "reason",
)


def _causal_graph_to_csv(graph: CausalGraph) -> str:
    """Render a causal graph as a single combined CSV document.

    The CSV carries two logical sections — events and edges — stitched
    into one stream by a leading ``row_type`` discriminator column.
    Analytics tools ingest the file in one pass and filter on the
    discriminator to split the two relations.
    """
    import csv
    from io import StringIO

    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    writer.writerow(_CAUSAL_CSV_EVENT_HEADER)
    for event in graph.events:
        writer.writerow(
            (
                "event",
                causal_event_key(event),
                event.occurred_at.isoformat(),
                event.kind.value,
                event.source,
                event.native_id,
                event.property_id,
                event.summary,
            )
        )

    writer.writerow(())
    writer.writerow(_CAUSAL_CSV_EDGE_HEADER)
    for edge in graph.edges:
        writer.writerow(
            (
                "edge",
                edge.source_key,
                edge.target_key,
                edge.kind.value,
                f"{edge.confidence:.4f}",
                edge.inferred_by,
                edge.reason,
            )
        )

    return buffer.getvalue()


_CAUSAL_VALID_FORMATS: frozenset[str] = frozenset({"json", "csv"})


@app.get(
    "/api/memory/property/{property_id}/causal",
    tags=["Intelligence"],
)
async def get_property_causal(
    property_id: str,
    event_id: str | None = None,
    direction: str = "descendants",
    depth: int | None = None,
    range_days: int = 90,
    since: str | None = None,
    until: str | None = None,
    include_ops: bool = True,
    customer_id: str | None = None,
    reservation_id: str | None = None,
    guest_id: str | None = None,
    format: str = "json",
):
    """Return a property's causal graph and optional anchor-walked chains.

    When ``event_id`` is omitted the response carries only the graph
    payload.  When provided, the endpoint walks the graph from that
    anchor in ``direction`` (``ancestors`` | ``descendants``) up to
    ``depth`` (defaults to the service max) and returns the resulting
    chains.

    ``format=csv`` returns the full graph as a two-section CSV
    document (events, then edges, separated by a blank line) for
    analytics ingestion; chains are JSON-only.

    Error mapping:
        400 — bad ``direction`` / ``format`` or unparsable
            ``since`` / ``until``.
        404 — ``event_id`` not present in the graph.
        503 — causal service not initialised.
    """
    if _causal_service is None or _narrative_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Causal service not initialized"},
        )
    fmt = format.strip().lower()
    if fmt not in _CAUSAL_VALID_FORMATS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported format: {format}"},
        )
    if direction not in _CAUSAL_DIRECTIONS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported direction: {direction}"},
        )
    try:
        since_dt = _parse_iso_or_none(since)
        until_dt = _parse_iso_or_none(until)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    timeline_range = TimelineRange.from_params(
        days=range_days,
        since=since_dt,
        until=until_dt,
    )

    events = await _narrative_service.collect_events(
        property_id=property_id,
        range=timeline_range,
        customer_id=customer_id,
        reservation_id=reservation_id,
        guest_id=guest_id,
        include_ops=include_ops,
    )
    graph = await _causal_service.build_graph(events)

    if fmt == "csv":
        return PlainTextResponse(
            content=_causal_graph_to_csv(graph),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="causal-{property_id}.csv"'
                ),
                "X-Event-Count": str(len(graph.events)),
                "X-Edge-Count": str(len(graph.edges)),
            },
        )

    payload: dict[str, Any] = {
        "property_id": property_id,
        "range": {
            "since": timeline_range.since.isoformat(),
            "until": timeline_range.until.isoformat(),
        },
        "graph": _causal_graph_payload(graph),
    }

    if event_id is not None:
        try:
            chains = _causal_service.walk(
                graph,
                anchor_key=event_id,
                direction=direction,
                depth=depth,
            )
        except CausalNavigationError as exc:
            status = 404 if "not in the graph" in str(exc) else 400
            return JSONResponse(
                status_code=status,
                content={"error": str(exc)},
            )
        payload["anchor"] = {"event_key": event_id, "direction": direction}
        payload["chains"] = [_causal_chain_payload(c) for c in chains]

    return payload


@app.get(
    "/api/memory/property/{property_id}/causal/walk",
    tags=["Intelligence"],
)
async def walk_property_causal(
    property_id: str,
    event_id: str,
    direction: str = "descendants",
    depth: int | None = None,
    range_days: int = 90,
    since: str | None = None,
    until: str | None = None,
    include_ops: bool = True,
    customer_id: str | None = None,
    reservation_id: str | None = None,
    guest_id: str | None = None,
):
    """Return chains only for an anchor event — no embedded graph.

    Thin companion to ``/causal`` for UI follow-up navigation: the
    client already has the graph from the first call and only needs the
    chains rooted at a new anchor.  The response omits the ``events``
    and ``edges`` arrays so round-trips stay small.

    Error mapping:
        400 — bad ``direction`` or unparsable ``since`` / ``until``.
        404 — ``event_id`` not present in the graph.
        503 — causal service not initialised.
    """
    if _causal_service is None or _narrative_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Causal service not initialized"},
        )
    if direction not in _CAUSAL_DIRECTIONS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported direction: {direction}"},
        )
    try:
        since_dt = _parse_iso_or_none(since)
        until_dt = _parse_iso_or_none(until)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    timeline_range = TimelineRange.from_params(
        days=range_days,
        since=since_dt,
        until=until_dt,
    )

    events = await _narrative_service.collect_events(
        property_id=property_id,
        range=timeline_range,
        customer_id=customer_id,
        reservation_id=reservation_id,
        guest_id=guest_id,
        include_ops=include_ops,
    )
    graph = await _causal_service.build_graph(events)

    try:
        chains = _causal_service.walk(
            graph,
            anchor_key=event_id,
            direction=direction,
            depth=depth,
        )
    except CausalNavigationError as exc:
        status = 404 if "not in the graph" in str(exc) else 400
        return JSONResponse(
            status_code=status,
            content={"error": str(exc)},
        )

    return {
        "property_id": property_id,
        "range": {
            "since": timeline_range.since.isoformat(),
            "until": timeline_range.until.isoformat(),
        },
        "anchor": {"event_key": event_id, "direction": direction},
        "chains": [_causal_chain_payload(c) for c in chains],
    }


@app.post(
    "/api/admin/onboarding/bootstrap",
    tags=["Intelligence"],
)
async def bootstrap_onboarding(request: Request):
    """Replay archived conversations into the DecisionCase store.

    Body::

        {
            "property_ids": ["p-1", "p-2"],
            "days": 180,
            "limit_per_property": 500,
            "dry_run": false
        }

    Dry runs build cases in memory but skip persistence, letting the
    caller preview bootstrap volume before committing.
    """
    if _onboarding_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Onboarding service not initialized"},
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON body"},
        )

    property_ids = body.get("property_ids") or []
    if not isinstance(property_ids, list) or not property_ids:
        return JSONResponse(
            status_code=400,
            content={"error": "property_ids must be a non-empty list"},
        )
    ids = tuple(str(p) for p in property_ids if str(p).strip())
    if not ids:
        return JSONResponse(
            status_code=400,
            content={"error": "property_ids must contain non-empty strings"},
        )

    try:
        onboarding_request = OnboardingRequest(
            property_ids=ids,
            days=int(body.get("days", 180)),
            limit_per_property=int(body.get("limit_per_property", 500)),
            dry_run=bool(body.get("dry_run", False)),
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid numeric field: {exc}"},
        )

    try:
        report = await _onboarding_service.bootstrap(onboarding_request)
    except OnboardingError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})

    return report.as_dict()


@app.post("/api/memory/knowledge", tags=["Knowledge"])
async def add_knowledge(request: Request):
    """Add knowledge to the temporal knowledge graph.

    Body: {
        "content": "Guest John tends to request late checkouts",
        "knowledge_type": "belief",
        "entity_id": "guest_123",
        "confidence": 0.8,
        "keywords": ["late_checkout", "guest_behavior"]
    }
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    body = await request.json()
    node = await _memory.knowledge_graph.add_knowledge(
        content=body.get("content", ""),
        knowledge_type=body.get("knowledge_type", "fact"),
        entity_type=body.get("entity_type", ""),
        entity_id=body.get("entity_id", ""),
        confidence=body.get("confidence", 1.0),
        keywords=body.get("keywords", []),
        tags=body.get("tags", []),
        source=body.get("source", "api"),
    )
    return {"node_id": node.node_id, "content": node.content, "type": node.knowledge_type}


@app.post("/api/memory/surprise", tags=["Learning"])
async def analyze_surprise(request: Request):
    """Analyze an event for surprise level (Titans-inspired).

    Body: {"event": "damage_detected", "context": {"severity": 4, "guest_id": "..."}}
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    body = await request.json()
    score = await _memory.surprise_detector.analyze_event(
        event=body.get("event", ""),
        context=body.get("context"),
    )
    return {
        "event": score.event,
        "surprise_score": score.raw_score,
        "category": score.category,
        "factors": score.factors,
        "should_memorize": score.should_memorize,
        "memory_strength": score.memory_strength,
    }


@app.get("/api/memory/procedures", tags=["Learning"])
async def list_procedures():
    """List all learned behavioral procedures."""
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    procedures = await _memory.procedural.get_all_procedures()
    return {
        "procedures": [p.to_dict() for p in procedures],
        "total": len(procedures),
    }


@app.post("/api/memory/consolidate", tags=["Learning"])
async def trigger_consolidation():
    """Trigger manual memory consolidation (tier migration)."""
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    stats = await _memory.consolidator.consolidate(force=True)
    return {"consolidation": stats}


@app.post("/api/memory/context", tags=["Intelligence"])
async def build_full_context(request: Request):
    """Build comprehensive LLM context from all memory tiers.

    Body: {"query": "damage history", "entity_ids": ["guest_123", "prop_456"]}
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    body = await request.json()
    context = await _memory.cognitive.build_full_context(
        query=body.get("query", ""),
        entity_ids=body.get("entity_ids"),
    )
    return {"context": context}


@app.get("/api/memory/introspection", tags=["Intelligence"])
async def metacognition_introspection():
    """Metacognitive introspection report.

    Returns self-assessment: performance metrics, epistemic state,
    recent reasoning traces, and overall health.
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    report = _memory.cognitive.metacognition.introspection_report()
    return report


@app.post("/api/memory/outcome", tags=["Learning"])
async def record_decision_outcome(request: Request):
    """Record the outcome of a previous decision for metacognitive learning.

    Body: {"trace_id": "trace-0003", "outcome": "success"}
    """
    if not _memory:
        return JSONResponse(status_code=503, content={"error": "Memory system not initialized"})

    body = await request.json()
    trace_id = body.get("trace_id", "")
    outcome = body.get("outcome", "")
    if outcome not in ("success", "failure"):
        return JSONResponse(status_code=400, content={"error": "outcome must be 'success' or 'failure'"})

    _memory.cognitive.metacognition.record_outcome(trace_id, outcome)
    return {"status": "recorded", "trace_id": trace_id, "outcome": outcome}


# ── Nuki Smart Lock Webhook ──────────────────────────────────────────── #

# Global entry detector reference (set by scenario orchestrator at runtime)
_entry_detector = None


def set_entry_detector(detector: Any) -> None:
    """Register the active NukiEntryDetector for webhook routing."""
    global _entry_detector
    _entry_detector = detector


@app.post("/api/nuki/webhook", tags=["IoT"])
async def nuki_webhook(request: Request):
    """Receive Nuki smart lock state change notifications.

    Nuki sends webhooks when the lock state changes (unlock, lock, etc.).
    This endpoint routes the payload to the active NukiEntryDetector.

    Body: Raw Nuki webhook JSON payload.
    """
    body = await request.json()

    # Optional secret verification
    webhook_secret = request.headers.get("X-Nuki-Secret", "")
    if _settings and _settings.nuki_webhook_secret:
        if webhook_secret != _settings.nuki_webhook_secret:
            return JSONResponse(status_code=403, content={"error": "Invalid webhook secret"})

    if _entry_detector is None:
        logger.warning("Nuki webhook received but no entry detector is active")
        return {"status": "ignored", "reason": "no_active_detector"}

    entry = _entry_detector.handle_webhook(body)
    if entry:
        logger.info("Nuki webhook: entry detected — %s via %s", entry.action, entry.trigger)
        return {
            "status": "entry_detected",
            "action": entry.action,
            "trigger": entry.trigger,
            "timestamp": entry.timestamp.isoformat(),
        }

    return {"status": "processed", "entry_detected": False}


# ── Scenario Flow Endpoint ───────────────────────────────────────────── #

def _load_scenario_defaults() -> dict[str, Any]:
    """Load all scenario defaults from config/guests.json and config/cleaners.json.

    Returns a dict of slot values ready to use — no manual input needed.
    """
    import json as _json

    config_dir = Path(__file__).resolve().parents[1] / "config"
    defaults: dict[str, Any] = {
        "property_name": "Seaside Apartment",
        "property_address": "123 Marina Boulevard, Apt 4B",
        "standard_checkout_time": "11:00 AM",
        "standard_checkin_time": "3:00 PM",
        "cleaning_time": "7:00 PM",
        "cleaning_date": "today",
        "delivery_channel": "Telegram",
        "photo_upload_link": "Telegram bot @cendra_cleaner_bot",
    }

    # Load guests (incoming + departing)
    try:
        with open(config_dir / "guests.json") as f:
            guests = _json.load(f)
        if isinstance(guests, list):
            for guest in guests:
                role = guest.get("role", "")
                if role == "incoming":
                    defaults["incoming_guest_name"] = guest.get("name", "")
                    defaults["incoming_guest_phone"] = guest.get("phone", "")
                    defaults["property_id"] = guest.get("property_id", "")
                    defaults["checkin_date"] = guest.get("checkin", "")
                elif role == "departing":
                    defaults["departing_guest_name"] = guest.get("name", "")
                    defaults["departing_guest_phone"] = guest.get("phone", "")
                    defaults["checkout_date"] = guest.get("checkout", "")
    except (FileNotFoundError, _json.JSONDecodeError):
        pass

    # Load cleaners (primary + backup)
    try:
        with open(config_dir / "cleaners.json") as f:
            cleaners = _json.load(f)
        if isinstance(cleaners, list):
            if len(cleaners) >= 1:
                defaults["cleaner_name"] = cleaners[0].get("name", "")
                defaults["cleaner_phone"] = cleaners[0].get("phone", "")
            if len(cleaners) >= 2:
                defaults["backup_cleaner_name"] = cleaners[1].get("name", "")
                defaults["backup_cleaner_phone"] = cleaners[1].get("phone", "")
    except (FileNotFoundError, _json.JSONDecodeError):
        pass

    return defaults


# ── Guardrail Validation API ─────────────────────────────────────────── #


@app.post("/api/validate/action", tags=["System"])
async def validate_action(request: Request):
    """Validate an action against symbolic rules and contradiction checks.

    Body: {"action": "call_guest", "context": {"departing_guest_phone": "+1..."}}
    """
    from brain_engine.guardrails.pipeline import GuardrailPipeline

    body = await request.json()
    pipeline = GuardrailPipeline()
    result = pipeline.validate_action(
        action=body.get("action", ""),
        context=body.get("context", {}),
    )
    return {
        "passed": result.passed,
        "failures": result.failures,
        "warnings": result.warnings,
        "correction_prompt": result.correction_prompt,
    }


@app.post("/api/validate/slots", tags=["System"])
async def validate_slots(request: Request):
    """Check for contradictions in current slot values.

    Body: {"slots": {"checkin_date": "2026-03-10", "checkout_date": "2026-03-08"}}
    """
    from brain_engine.guardrails.contradiction_checker import (
        ContradictionChecker,
    )

    body = await request.json()
    checker = ContradictionChecker()
    contradictions = checker.check_slots(body.get("slots", {}))
    return {
        "contradictions": [
            {
                "type": c.conflict_type,
                "severity": c.severity,
                "message": c.message,
                "action": c.action,
                "slots": {c.slot_a: c.value_a, c.slot_b: c.value_b},
            }
            for c in contradictions
        ],
        "has_conflicts": len(contradictions) > 0,
    }


# Scenario/voice endpoints temporarily disabled — working in text API mode only


# ══════════════════════════════════════════════════════════════════════════
# PHASE 1: Human-in-the-Loop — Approval Gateway API
# ══════════════════════════════════════════════════════════════════════════


@app.post("/api/approval/request", tags=["Approval"])
async def request_approval(request: Request):
    """Request owner approval for an AI-proposed action.

    Body: {
        "action_type": "late_checkout",
        "owner_id": "owner_123",
        "property_id": "PROP001",
        "description": "Guest John requests late checkout at 3 PM ($50 fee)",
        "proposed_action": {"checkout_time": "15:00", "fee": 50},
        "context": {"guest_name": "John", "guest_rating": 4.8},
        "urgency": 3
    }
    """
    if not _approval_gateway:
        return JSONResponse(status_code=503, content={"error": "Approval gateway not initialized"})

    body = await request.json()
    try:
        action_type = ActionType(body.get("action_type", ""))
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid action_type. Valid: {[a.value for a in ActionType]}"},
        )

    result = await _approval_gateway.request_approval(
        action_type=action_type,
        owner_id=body.get("owner_id", ""),
        property_id=body.get("property_id", ""),
        description=body.get("description", ""),
        proposed_action=body.get("proposed_action"),
        context=body.get("context"),
        urgency=body.get("urgency", 3),
    )

    return {
        "request_id": result.request_id,
        "status": result.status.value,
        "message": result.message,
    }


@app.post("/api/approval/respond", tags=["Approval"])
async def respond_to_approval(request: Request):
    """Submit owner response to an approval request.

    Body: {
        "request_id": "APR-12345678",
        "approved": true,
        "owner_id": "owner_123",
        "message": "OK, approve it",
        "apply_rule": true,
        "rule_scope": "always"
    }
    """
    if not _approval_gateway:
        return JSONResponse(status_code=503, content={"error": "Approval gateway not initialized"})

    body = await request.json()
    try:
        result = await _approval_gateway.submit_response(
            request_id=body.get("request_id", ""),
            approved=body.get("approved", False),
            owner_id=body.get("owner_id", ""),
            message=body.get("message", ""),
            apply_rule=body.get("apply_rule", False),
            rule_scope=body.get("rule_scope", "this_time"),
        )
    except ApprovalNotFoundError as exc:
        return JSONResponse(status_code=exc.code, content={"error": str(exc)})

    # Trigger preference learning if approved/denied
    if _preference_learner and _approval_gateway:
        req = _approval_gateway.get_request(body.get("request_id", ""))
        if req:
            questions = _preference_learner.generate_questions(
                request=req,
                approved=body.get("approved", False),
            )
            if questions:
                await _preference_learner.send_questions(
                    questions=questions,
                    owner_id=req.owner_id,
                )

    return {
        "request_id": result.request_id,
        "status": result.status.value,
        "message": result.message,
    }


@app.post("/api/approval/register-owner", tags=["Approval"])
async def register_owner_telegram(request: Request):
    """Register an owner's Telegram chat_id for approval notifications.

    Body: {
        "owner_id": "owner_123",
        "chat_id": 123456789
    }

    The owner must first /start the bot in Telegram,
    then use this endpoint to link their owner_id.
    """
    if not _approval_notifier:
        return JSONResponse(status_code=503, content={"error": "Approval notifier not initialized"})

    body = await request.json()
    owner_id = body.get("owner_id", "")
    chat_id = body.get("chat_id", "")

    if not owner_id or not chat_id:
        return JSONResponse(status_code=400, content={"error": "owner_id and chat_id required"})

    _approval_notifier.register_owner(owner_id, chat_id)
    return {
        "owner_id": owner_id,
        "chat_id": chat_id,
        "message": f"Owner {owner_id} registered for Telegram notifications",
    }


@app.get("/api/approval/pending", tags=["Approval"])
async def list_pending_approvals():
    """List all pending approval requests."""
    if not _approval_gateway:
        return JSONResponse(status_code=503, content={"error": "Approval gateway not initialized"})

    pending = _approval_gateway.pending_requests
    return {
        "pending": [
            {
                "request_id": r.request_id,
                "action_type": r.action_type.value,
                "owner_id": r.owner_id,
                "property_id": r.property_id,
                "description": r.description,
                "urgency": r.urgency,
                "created_at": r.created_at,
                "timeout_seconds": r.timeout_seconds,
            }
            for r in pending
        ],
        "total": len(pending),
    }


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2: Adaptive Preferences Engine API
# ══════════════════════════════════════════════════════════════════════════


@app.post("/api/preferences/rule", tags=["Approval"])
async def create_preference_rule(request: Request):
    """Manually create a preference rule for an owner.

    Body: {
        "owner_id": "owner_123",
        "property_id": "PROP001",
        "action_type": "late_checkout",
        "auto_approve": true,
        "scope": "this_property",
        "conditions": {"guest_rating_min": 4.5}
    }
    """
    if not _preference_store:
        return JSONResponse(status_code=503, content={"error": "Preference store not initialized"})

    body = await request.json()
    rule = await _preference_store.save_rule(
        owner_id=body.get("owner_id", ""),
        property_id=body.get("property_id", ""),
        action_type=body.get("action_type", ""),
        auto_approve=body.get("auto_approve", True),
        scope=body.get("scope", "this_property"),
        conditions=body.get("conditions"),
    )
    return {
        "rule_id": rule.rule_id,
        "owner_id": rule.owner_id,
        "action_type": rule.action_type,
        "auto_approve": rule.auto_approve,
        "scope": rule.scope.value,
    }


@app.get("/api/preferences/{owner_id}", tags=["Approval"])
async def get_owner_preferences(owner_id: str):
    """Get all preference rules for an owner."""
    if not _preference_store:
        return JSONResponse(status_code=503, content={"error": "Preference store not initialized"})

    rules = await _preference_store.get_rules_for_owner(owner_id)
    return {
        "owner_id": owner_id,
        "rules": [
            {
                "rule_id": r.rule_id,
                "property_id": r.property_id or "all",
                "action_type": r.action_type,
                "auto_approve": r.auto_approve,
                "scope": r.scope.value,
                "conditions": r.conditions,
                "usage_count": r.usage_count,
                "created_at": r.created_at,
            }
            for r in rules
        ],
        "total": len(rules),
    }


@app.post("/api/preferences/check", tags=["Approval"])
async def check_policy(request: Request):
    """Check if an action has a matching preference rule.

    Body: {
        "owner_id": "owner_123",
        "property_id": "PROP001",
        "action_type": "late_checkout",
        "context": {"guest_rating": 4.8}
    }
    """
    if not _policy_enforcer:
        return JSONResponse(status_code=503, content={"error": "Policy enforcer not initialized"})

    body = await request.json()
    result = await _policy_enforcer.check_policy(
        owner_id=body.get("owner_id", ""),
        property_id=body.get("property_id", ""),
        action_type=body.get("action_type", ""),
        context=body.get("context"),
    )
    return {
        "decision": result.decision.value,
        "rule_id": result.rule_id,
        "reason": result.reason,
    }


@app.post("/api/preferences/learn", tags=["Approval"])
async def process_learning_answer(request: Request):
    """Process owner's answer to a learning question.

    Body: {
        "question_id": "Q-12345678",
        "answer": "Always for this property",
        "approved": true
    }
    """
    if not _preference_learner:
        return JSONResponse(status_code=503, content={"error": "Preference learner not initialized"})

    body = await request.json()
    result = await _preference_learner.process_answer(
        question_id=body.get("question_id", ""),
        answer=body.get("answer", ""),
        approved=body.get("approved", True),
    )
    return result


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3: Fallback & Gap Detection API
# ══════════════════════════════════════════════════════════════════════════


@app.post("/api/validate/config", tags=["System"])
async def validate_flow_config(request: Request):
    """Validate configuration completeness before starting a flow.

    Body: {
        "flow_type": "cleaner_coordination",
        "slots": {"cleaner_name": "Maria", "cleaner_phone": "+1234567890"}
    }
    """
    if not _config_validator:
        return JSONResponse(status_code=503, content={"error": "Config validator not initialized"})

    body = await request.json()
    result = _config_validator.validate_flow(
        flow_type=body.get("flow_type", ""),
        slots=body.get("slots", {}),
    )
    return result.to_dict()


@app.post("/api/fallback/resolve", tags=["Ops Agent"])
async def resolve_gap(request: Request):
    """Create and execute a resolution plan for a data gap.

    Body: {
        "gap_type": "all_cleaners_busy",
        "context": {
            "property_address": "123 Marina Blvd",
            "manager_phone": "+1234567890",
            "backup_cleaners": [{"name": "Eve", "phone": "+9876543210"}]
        }
    }
    """
    if not _gap_resolver:
        return JSONResponse(status_code=503, content={"error": "Gap resolver not initialized"})

    body = await request.json()
    try:
        gap_type = GapType(body.get("gap_type", ""))
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid gap_type. Valid: {[g.value for g in GapType]}"},
        )

    plan = _gap_resolver.create_resolution_plan(
        gap_type=gap_type,
        context=body.get("context"),
    )
    result = await _gap_resolver.execute_plan(plan, context=body.get("context"))

    return {
        "gap_type": result.gap_type.value,
        "resolved": result.resolved,
        "steps_executed": [
            {
                "action": s.action,
                "target": s.target,
                "completed": s.completed,
                "success": s.success,
            }
            for s in result.steps
        ],
        "resolution_data": result.resolution_data,
    }


@app.post("/api/fallback/cleaner-chain", tags=["Ops Agent"])
async def run_cleaner_fallback(request: Request):
    """Run the cleaner fallback chain when all cleaners are busy.

    Tries each cleaner by rating, then escalates to manager, then owner.

    Body: {
        "cleaners": [
            {"name": "Maria", "phone": "+1...", "rating": 4.9, "available": true},
            {"name": "Carlos", "phone": "+1...", "rating": 4.7, "available": true}
        ],
        "manager_phone": "+1234567890",
        "owner_phone": "+0987654321",
        "property_address": "123 Marina Blvd"
    }
    """
    body = await request.json()
    cleaners = body.get("cleaners", [])
    manager_phone = body.get("manager_phone", "")
    owner_phone = body.get("owner_phone", "")

    chain = build_cleaner_fallback_chain(
        cleaners=cleaners,
        voice_client=_elevenlabs_client,
        notifier=_telegram_bot,
        manager_phone=manager_phone,
        owner_phone=owner_phone,
    )

    result = await chain.execute(context=body)

    # Emit an ops DecisionCase when the caller supplied property +
    # owner context.  Learning is scope-keyed on both ids, so without
    # either we skip rather than persist a useless case.
    property_id = str(body.get("property_id", "") or "")
    owner_id = str(body.get("owner_id", "") or "")
    reservation_id = body.get("reservation_id") or None
    if _ops_logger is not None and property_id and owner_id:
        await _ops_logger.log_cleaner_dispatch(
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            fallback_result=result,
        )

    return result.to_dict()


# ══════════════════════════════════════════════════════════════════════════
# Negotiation API (Gap #4 part 4)
# ══════════════════════════════════════════════════════════════════════════
#
# Three endpoints exposed under /api/ops/negotiate back the in-flight
# session manager.  The design is intentionally minimal — no persistence,
# no per-session auth, no transport dispatch wiring.  A session launched
# here records outbound text but does not push it to WhatsApp / voice; a
# follow-up PR will inject a real sender through the manager once the
# per-vendor channel registry lands.


def _parse_offer(data: dict[str, Any] | None) -> NegotiationOffer:
    """Build a :class:`NegotiationOffer` from a raw JSON body fragment."""
    if not data:
        return NegotiationOffer()
    price = data.get("price")
    return NegotiationOffer(
        time=str(data.get("time", "") or ""),
        price=float(price) if price is not None else None,
        notes=str(data.get("notes", "") or ""),
    )


def _parse_target(data: dict[str, Any] | None) -> NegotiationTarget:
    """Build a :class:`NegotiationTarget` from a raw JSON body fragment."""
    data = data or {}
    max_price = data.get("max_price")
    return NegotiationTarget(
        target_time=str(data.get("target_time", "") or ""),
        max_price=float(max_price) if max_price is not None else None,
        max_rounds=int(data.get("max_rounds", 3)),
    )


@app.post("/api/ops/negotiate", tags=["Ops Agent"])
async def start_negotiation(request: Request):
    """Launch a new negotiation session in the background.

    Body::

        {
            "vendor_name": "Acme Plumbing",
            "property_id": "prop-1",
            "owner_id": "owner-1",
            "reservation_id": "res-1",            // optional
            "initial_ask": {
                "time": "2026-05-03T10:00",
                "price": 450.0,
                "notes": "opening"
            },
            "target": {
                "target_time": "2026-05-03T10:00",
                "max_price": 500.0,
                "max_rounds": 3
            }
        }

    Returns the new ``session_id`` plus an initial status snapshot so
    the caller immediately sees the opening outbound text.
    """
    if _negotiation_manager is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Negotiation manager not initialized"},
        )
    body = await request.json()
    vendor_name = str(body.get("vendor_name", "") or "")
    property_id = str(body.get("property_id", "") or "")
    owner_id = str(body.get("owner_id", "") or "")
    if not (vendor_name and property_id and owner_id):
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    "vendor_name, property_id and owner_id are required"
                ),
            },
        )
    reservation_id = body.get("reservation_id") or None
    try:
        initial_ask = _parse_offer(body.get("initial_ask"))
        target = _parse_target(body.get("target"))
    except (TypeError, ValueError) as exc:
        return JSONResponse(
            status_code=400,
            content={"error": f"invalid offer/target payload: {exc}"},
        )

    session_id = await _negotiation_manager.start(
        vendor_name=vendor_name,
        property_id=property_id,
        owner_id=owner_id,
        initial_ask=initial_ask,
        target=target,
        reservation_id=reservation_id,
    )
    return _negotiation_manager.status(session_id)


@app.post(
    "/api/ops/negotiate/{session_id}/reply",
    tags=["Ops Agent"],
)
async def feed_negotiation_reply(session_id: str, request: Request):
    """Feed a raw counterparty reply into an active session.

    Body::

        {"reply_text": "sure, 2026-05-03T10:00 for $480"}
    """
    if _negotiation_manager is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Negotiation manager not initialized"},
        )
    body = await request.json()
    reply_text = str(body.get("reply_text", "") or "")
    if not reply_text:
        return JSONResponse(
            status_code=400,
            content={"error": "reply_text is required"},
        )
    try:
        parsed = await _negotiation_manager.feed_reply(
            session_id, reply_text,
        )
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"error": f"unknown session: {session_id}"},
        )
    except RuntimeError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": str(exc)},
        )
    return {
        "parsed": {
            "time": parsed.time,
            "price": parsed.price,
            "notes": parsed.notes,
        },
        "status": _negotiation_manager.status(session_id),
    }


@app.get(
    "/api/ops/negotiate/{session_id}",
    tags=["Ops Agent"],
)
async def get_negotiation_status(session_id: str):
    """Return a snapshot of a negotiation session."""
    if _negotiation_manager is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Negotiation manager not initialized"},
        )
    try:
        return _negotiation_manager.status(session_id)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"error": f"unknown session: {session_id}"},
        )


@app.post("/api/ops/vendor-channel", tags=["Ops Agent"])
async def register_vendor_channel(request: Request):
    """Register or update a vendor's negotiation channel.

    Body::

        {
            "vendor_name": "Acme Plumbing",
            "channel": "telegram" | "whatsapp" | "log",
            "address": "123456789"   // chat_id or phone; optional for log
        }

    Until a vendor has a registered channel, negotiations started for
    that vendor run in record-only mode (outbound text captured in
    ``sent_messages`` but not delivered).
    """
    if _vendor_channels is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Vendor channel registry not initialized"},
        )
    body = await request.json()
    vendor_name = str(body.get("vendor_name", "") or "")
    channel = str(body.get("channel", "") or "")
    address = str(body.get("address", "") or "")
    try:
        spec = _vendor_channels.register(
            vendor_name, channel=channel, address=address,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc)},
        )
    return {
        "vendor_name": vendor_name,
        "channel": spec.channel,
        "address": spec.address,
    }


@app.delete(
    "/api/ops/vendor-channel/{vendor_name}",
    tags=["Ops Agent"],
)
async def deregister_vendor_channel(vendor_name: str):
    """Drop a vendor from the channel registry."""
    if _vendor_channels is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Vendor channel registry not initialized"},
        )
    removed = _vendor_channels.unregister(vendor_name)
    if not removed:
        return JSONResponse(
            status_code=404,
            content={"error": f"unknown vendor: {vendor_name}"},
        )
    return {"vendor_name": vendor_name, "removed": True}


@app.get("/api/ops/vendor-channel", tags=["Ops Agent"])
async def list_vendor_channels():
    """Return the set of registered vendor channel names."""
    if _vendor_channels is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Vendor channel registry not initialized"},
        )
    return {"vendors": _vendor_channels.known_vendors()}


# ══════════════════════════════════════════════════════════════════════════
# PHASE 4: Guest Intelligence API
# ══════════════════════════════════════════════════════════════════════════


@app.get("/api/guest/{guest_id}/profile", tags=["Intelligence"])
async def get_guest_profile(guest_id: str):
    """Get comprehensive guest profile with all aggregated data."""
    if not _guest_profile_builder:
        return JSONResponse(status_code=503, content={"error": "Guest profile builder not initialized"})

    profile = await _guest_profile_builder.build_profile(guest_id)
    return profile.to_dict()


@app.get("/api/guest/{guest_id}/loyalty", tags=["Intelligence"])
async def get_guest_loyalty(guest_id: str):
    """Get guest loyalty score and tier."""
    if not _guest_profile_builder or not _loyalty_scorer:
        return JSONResponse(status_code=503, content={"error": "Guest intelligence not initialized"})

    profile = await _guest_profile_builder.build_profile(guest_id)
    score = _loyalty_scorer.score(profile)
    return score.to_dict()


@app.get("/api/guest/{guest_id}/benefits", tags=["Intelligence"])
async def get_guest_benefits(guest_id: str):
    """Get recommended benefits for a guest based on loyalty."""
    if not _guest_profile_builder or not _loyalty_scorer or not _benefit_recommender:
        return JSONResponse(status_code=503, content={"error": "Guest intelligence not initialized"})

    profile = await _guest_profile_builder.build_profile(guest_id)
    score = _loyalty_scorer.score(profile)
    recommendation = _benefit_recommender.recommend(score)
    return recommendation.to_dict()


@app.get("/api/guest/{guest_id}/risk", tags=["Intelligence"])
async def get_guest_risk(guest_id: str):
    """Get risk assessment for a guest."""
    if not _guest_profile_builder or not _risk_flag_system:
        return JSONResponse(status_code=503, content={"error": "Guest intelligence not initialized"})

    profile = await _guest_profile_builder.build_profile(guest_id)
    assessment = _risk_flag_system.assess(profile)
    return assessment.to_dict()


@app.post("/api/guest/assess", tags=["Intelligence"])
async def assess_guest_inline(request: Request):
    """Inline guest assessment — provide data directly without stored profile.

    Body: {
        "guest_name": "John",
        "total_stays": 5,
        "damage_incidents": 1,
        "complaints": 0,
        "positive_reviews": 3,
        "negative_reviews": 0,
        "late_checkout_requests": 2,
        "properties_stayed": ["PROP001", "PROP002"]
    }
    """
    from brain_engine.guest_intelligence.profile_builder import GuestProfile

    body = await request.json()
    profile = GuestProfile(
        guest_id=body.get("guest_id", "inline"),
        guest_name=body.get("guest_name", ""),
        total_stays=body.get("total_stays", 0),
        damage_incidents=body.get("damage_incidents", 0),
        complaints=body.get("complaints", 0),
        positive_reviews=body.get("positive_reviews", 0),
        negative_reviews=body.get("negative_reviews", 0),
        late_checkout_requests=body.get("late_checkout_requests", 0),
        properties_stayed=body.get("properties_stayed", []),
    )

    result: dict[str, Any] = {"profile": profile.to_dict()}

    if _loyalty_scorer:
        score = _loyalty_scorer.score(profile)
        profile.loyalty_score = score.total_score
        result["loyalty"] = score.to_dict()

    if _benefit_recommender and _loyalty_scorer:
        score = _loyalty_scorer.score(profile)
        recommendation = _benefit_recommender.recommend(score)
        result["benefits"] = recommendation.to_dict()

    if _risk_flag_system:
        assessment = _risk_flag_system.assess(profile)
        profile.risk_level = assessment.risk_level.value
        result["risk"] = assessment.to_dict()

    return result


# ══════════════════════════════════════════════════════════════════════════
# Neuro-Symbolic Cascade & Temporal Memory API
# ══════════════════════════════════════════════════════════════════════════


@app.get("/api/demo/guests", tags=["Intelligence"])
async def demo_guest_intelligence():
    """Demo endpoint: shows loyalty scoring for all sample guests.

    Loads fake guests from config/demo_guests.json and runs the full
    Guest Intelligence pipeline: profile → loyalty score → benefits → risk.

    No parameters needed — just GET /api/demo/guests.
    """
    from brain_engine.guest_intelligence.benefit_recommender import (
        BenefitRecommender,
    )
    from brain_engine.guest_intelligence.loyalty_scorer import LoyaltyScorer
    from brain_engine.guest_intelligence.profile_builder import GuestProfile
    from brain_engine.guest_intelligence.risk_flag import RiskFlagSystem

    scorer = LoyaltyScorer()
    recommender = BenefitRecommender()
    risk_system = RiskFlagSystem()

    # Load demo guests
    config_dir = Path(__file__).resolve().parents[1] / "config"
    try:
        with open(config_dir / "demo_guests.json") as f:
            guests_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return JSONResponse(status_code=404, content={"error": "demo_guests.json not found"})

    results = []
    for g in guests_data:
        profile = GuestProfile(
            guest_id=g.get("guest_id", ""),
            guest_name=g.get("guest_name", ""),
            phone=g.get("phone", ""),
            email=g.get("email", ""),
            total_stays=g.get("total_stays", 0),
            damage_incidents=g.get("damage_incidents", 0),
            complaints=g.get("complaints", 0),
            positive_reviews=g.get("positive_reviews", 0),
            negative_reviews=g.get("negative_reviews", 0),
            late_checkout_requests=g.get("late_checkout_requests", 0),
            average_review_rating=g.get("average_review_rating", 0),
            properties_stayed=g.get("properties_stayed", []),
            first_stay_date=g.get("first_stay_date", ""),
            last_stay_date=g.get("last_stay_date", ""),
            notes=g.get("notes", ""),
        )

        score = scorer.score(profile)
        profile.loyalty_score = score.total_score
        benefits = recommender.recommend(score)
        risk = risk_system.assess(profile)
        profile.risk_level = risk.risk_level.value

        results.append({
            "guest": {
                "id": profile.guest_id,
                "name": profile.guest_name,
                "total_stays": profile.total_stays,
                "damage_incidents": profile.damage_incidents,
                "complaints": profile.complaints,
                "notes": profile.notes,
            },
            "loyalty": {
                "score": score.total_score,
                "tier": score.tier,
                "factors": score.factors,
            },
            "benefits": [
                {"type": b.benefit_type, "description": b.description, "auto": b.auto_applicable}
                for b in benefits.benefits
            ],
            "risk": {
                "level": risk.risk_level.value,
                "flags": [
                    {"type": f.flag_type, "severity": f.severity.value, "description": f.description}
                    for f in risk.flags
                ],
                "recommendation": risk.recommendation,
                "allow_booking": risk.allow_booking,
            },
        })

    return {
        "demo": True,
        "total_guests": len(results),
        "guests": results,
    }


@app.post("/api/maintenance/report", tags=["Ops Agent"])
async def report_maintenance_issue(request: Request):
    """Report a maintenance issue and start the resolution flow.

    The system will: diagnose → try remote fix → find vendor →
    dispatch → notify guest — all before next guest checks in.

    Body: {
        "issue_type": "ac_not_working",
        "issue_description": "AC making loud noise and not cooling",
        "property_id": "PROP001",
        "guest_name": "George",
        "owner_id": "owner_123"
    }

    issue_type options: ac_not_working, water_leak, broken_lock,
    electrical, no_hot_water, wifi_down, appliance_broken, other
    """
    from brain_engine.flows.maintenance import MaintenanceFlow
    from brain_engine.problem_solver import ProblemSolver
    from brain_engine.state_manager.slot_manager import SlotManager
    from brain_engine.streaming.ag_ui_emitter import AGUIEmitter

    body = await request.json()

    slot_manager = SlotManager()
    for key, value in body.items():
        slot_manager.set_slot(key, value)

    # Create ProblemSolver — Azure OpenAI is the sole LLM backend.
    from brain_engine.models.azure_routing import load_azure_openai_config
    solver = (
        ProblemSolver()
        if load_azure_openai_config().is_complete()
        else None
    )

    emitter = AGUIEmitter()
    flow = MaintenanceFlow(
        slot_manager=slot_manager,
        emitter=emitter,
        session_id=str(uuid.uuid4())[:8],
        voice_client=_elevenlabs_client,
        approval_gateway=_approval_gateway,
        event_recorder=_memory.event_recorder if _memory else None,
        episodic=_memory.episodic if _memory else None,
        owner_id=body.get("owner_id", "default_owner"),
        property_id=body.get("property_id", "PROP001"),
        problem_solver=solver,
    )

    async def _maintenance_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in flow.run():
                event_dict = event.to_dict() if hasattr(event, "to_dict") else {"data": str(event)}
                event_type = event_dict.get("type", "message")
                yield _sse_event(event_type, event_dict)
        except Exception as exc:
            logger.exception("Maintenance flow error")
            yield _sse_event("error", {"error": str(exc)})

    return StreamingResponse(
        _maintenance_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# Smart Engine — Self-Learning Autonomous Property Manager
# ══════════════════════════════════════════════════════════════════════════


@app.post("/api/smart/booking", tags=["Booking"])
async def handle_new_booking(request: Request):
    """Handle a new booking — check guest score, schedule tasks.

    Body: {
        "id": "BK-001",
        "property_id": "PROP001",
        "guest_id": "GUEST-003",
        "city": "Istanbul",
        "checkin_date": "2026-03-25",
        "checkout_date": "2026-03-28",
        "owner_id": "owner_123"
    }
    """
    from brain_engine.smart_engine.orchestrator import APMOrchestrator

    body = await request.json()
    scoring = _scoring_engine
    city_kg = _city_knowledge
    orchestrator = APMOrchestrator(
        scoring_engine=scoring,
        city_knowledge=city_kg,
        voice_client=_elevenlabs_client,
        notifier=_approval_notifier,
        pms_client=None,
        approval_gateway=_approval_gateway,
    )

    status = await orchestrator.on_new_booking(body)
    return status.to_dict()


@app.post("/api/smart/precheck", tags=["Booking"])
async def run_precheck(request: Request):
    """Run pre-check for a booking: vendor checks + book cleaner.

    Body: {
        "booking_id": "BK-001",
        "cleaners": [{"name": "Maria", "phone": "+1...", "id": "CLN-001"}],
        "manager_phone": "+1234567890",
        "owner_phone": "+0987654321"
    }
    """
    from brain_engine.smart_engine.orchestrator import APMOrchestrator

    body = await request.json()
    scoring = _scoring_engine
    city_kg = _city_knowledge
    orchestrator = APMOrchestrator(
        scoring_engine=scoring,
        city_knowledge=city_kg,
        voice_client=_elevenlabs_client,
        notifier=_approval_notifier,
        dry_run=True,
    )

    # Need to first register the booking
    booking_id = body.get("booking_id", "")
    if not orchestrator.get_status(booking_id):
        await orchestrator.on_new_booking({
            "id": booking_id,
            "property_id": body.get("property_id", "PROP001"),
            "guest_id": body.get("guest_id", ""),
            "city": body.get("city", "Istanbul"),
        })

    status = await orchestrator.run_precheck(
        booking_id=booking_id,
        cleaners=body.get("cleaners", []),
        manager_phone=body.get("manager_phone", ""),
        owner_phone=body.get("owner_phone", ""),
    )
    return status.to_dict()


@app.post("/api/smart/score", tags=["Intelligence"])
async def update_score(request: Request):
    """Record a scoring event for a cleaner, vendor, or guest.

    Body: {
        "entity_id": "CLN-001",
        "entity_type": "cleaner",
        "event_type": "accepted_fast",
        "property_id": "PROP001",
        "city": "Istanbul",
        "response_time": 120
    }
    """

    body = await request.json()
    scoring = _scoring_engine
    score = await scoring.record_event(
        entity_id=body.get("entity_id", ""),
        entity_type=body.get("entity_type", ""),
        event_type=body.get("event_type", ""),
        property_id=body.get("property_id", ""),
        city=body.get("city", ""),
        response_time=body.get("response_time"),
    )
    return score.to_dict()


@app.get("/api/smart/ranked/{entity_type}", tags=["Intelligence"])
async def get_ranked_entities(
    entity_type: str,
    property_id: str = "",
    city: str = "",
    limit: int = 10,
):
    """Get entities ranked by composite score.

    entity_type: "cleaner" or "vendor"
    """

    scoring = _scoring_engine
    ranked = await scoring.get_ranked(
        entity_type=entity_type,
        property_id=property_id,
        city=city,
        limit=limit,
    )
    return {"entity_type": entity_type, "ranked": ranked, "total": len(ranked)}


@app.get("/api/smart/city/{city}", tags=["Intelligence"])
async def get_city_profile(city: str):
    """Get city knowledge profile."""

    scoring = _scoring_engine
    city_kg = _city_knowledge
    profile = await city_kg.get_city_profile(city)
    return profile.to_dict()


@app.post("/api/smart/vendor-precheck", tags=["Booking"])
async def run_vendor_precheck(request: Request):
    """Run equipment pre-check for a property.

    Body: {
        "property_id": "PROP001",
        "city": "Istanbul",
        "known_issues": ["WiFi intermittent", "AC noisy"]
    }
    """
    from brain_engine.smart_engine.vendor_precheck import VendorPreCheck

    body = await request.json()
    scoring = _scoring_engine
    checker = VendorPreCheck(
        scoring_engine=scoring,
        notifier=_approval_notifier,
        voice_client=_elevenlabs_client,
        property_id=body.get("property_id", ""),
        city=body.get("city", ""),
    )

    report = await checker.run_full_check(
        known_issues=body.get("known_issues"),
    )
    return report.to_dict()


@app.post("/api/maintenance/analyze", tags=["Ops Agent"])
async def analyze_problem(request: Request):
    """Analyze ANY property problem with AI and get an action plan.

    Send any free-text description and the AI will figure out:
    - What type of problem it is
    - How urgent (1-5)
    - Can it be fixed remotely?
    - What vendor is needed
    - What to tell the guest NOW
    - Step-by-step action plan

    Body: {
        "description": "The toilet is overflowing and water is on the floor",
        "property_name": "Seaside Apartment",
        "reported_by": "guest",
        "next_checkin": "7:00 PM today"
    }
    """
    from brain_engine.problem_solver import ProblemSolver

    body = await request.json()

    solver = ProblemSolver()
    analysis = await solver.analyze(
        problem_description=body.get("description", ""),
        property_context=f"Property: {body.get('property_name', 'Rental apartment')}",
        reported_by=body.get("reported_by", "guest"),
        next_checkin=body.get("next_checkin", ""),
    )
    return analysis.to_dict()


@app.post("/api/validate/cascade", tags=["System"])
async def run_neuro_symbolic_cascade(request: Request):
    """Run the full 4-layer neuro-symbolic contradiction cascade.

    Layers: Keywords → ConceptNet → NLI (DeBERTa/GPT-4o Mini) → GPT-4o

    Body: {
        "premise": "The guest is celebrating a birthday",
        "hypothesis": "Delivery to funeral home",
        "context": {"guest_name": "John"},
        "slots": {"occasion": "birthday"}
    }
    """
    from brain_engine.guardrails.neuro_symbolic_cascade import (
        NeuroSymbolicCascade,
    )

    body = await request.json()

    cascade = NeuroSymbolicCascade()
    result = await cascade.validate(
        premise=body.get("premise", ""),
        hypothesis=body.get("hypothesis", ""),
        context=body.get("context"),
        slots=body.get("slots"),
    )
    return result.to_dict()


@app.post("/api/validate/response", tags=["System"])
async def validate_agent_response(request: Request):
    """Run the full guardrail pipeline on an agent response.

    Checks: empty, repeat question, repeat response, hallucination,
    lexical quality, format.

    Body: {
        "response": "The guest checkout time is...",
        "filled_slots": {"john_checkout_time": "3:00 PM", "cleaner_name": "Maria"},
        "knowledge_base": "Property policy document text...",
        "audience": "guest"
    }
    """
    from brain_engine.guardrails.pipeline import GuardrailPipeline as _GP

    body = await request.json()
    audience = body.get("audience", "guest")

    pipeline = _GP(audience=audience)
    result = pipeline.validate_response(
        response=body.get("response", ""),
        context=body.get("context", {}),
        filled_slots=body.get("filled_slots"),
        knowledge_base=body.get("knowledge_base", ""),
    )
    return {
        "passed": result.passed,
        "failures": result.failures,
        "warnings": result.warnings,
        "cleaned_response": result.cleaned_response,
        "correction_prompt": result.correction_prompt,
    }


@app.post("/api/temporal/event", tags=["Learning"])
async def add_temporal_event(request: Request):
    """Record an event in temporal memory.

    Body: {
        "event_type": "damage_detected",
        "content": "Cracked TV screen found in living room",
        "importance": 0.9,
        "entity_ids": ["guest_123", "PROP001"],
        "tags": ["damage", "tv"]
    }
    """
    from brain_engine.memory.temporal_memory import TemporalMemory

    if not hasattr(app.state, "temporal_memory"):
        app.state.temporal_memory = TemporalMemory()

    body = await request.json()
    event = await app.state.temporal_memory.add_event(
        event_type=body.get("event_type", ""),
        content=body.get("content", ""),
        metadata=body.get("metadata"),
        importance=body.get("importance", 0.5),
        initial_strength=body.get("initial_strength", 1.0),
        entity_ids=body.get("entity_ids"),
        tags=body.get("tags"),
    )
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "initial_strength": event.initial_strength,
    }


@app.get("/api/temporal/query", tags=["Learning"])
async def query_temporal_events(
    hours_back: int = 24,
    entity_id: str = "",
    event_type: str = "",
    limit: int = 50,
):
    """Query temporal memory for recent events.

    Params:
        hours_back: How many hours back to look (default 24).
        entity_id: Filter by entity (guest, property).
        event_type: Filter by event type.
        limit: Max events to return.
    """
    from brain_engine.memory.temporal_memory import TemporalMemory

    if not hasattr(app.state, "temporal_memory"):
        app.state.temporal_memory = TemporalMemory()

    tm: TemporalMemory = app.state.temporal_memory

    if entity_id:
        events = await tm.query_by_entity(entity_id, limit=limit)
    elif event_type:
        events = await tm.query_by_type(event_type, limit=limit, hours_back=hours_back)
    else:
        events = await tm.query_by_time(hours_back=hours_back)

    return {
        "events": [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "content": e.content,
                "timestamp": e.timestamp,
                "strength": round(e.current_strength(), 3),
                "access_count": e.access_count,
                "importance": e.importance,
                "entity_ids": e.entity_ids,
                "tags": e.tags,
            }
            for e in events[:limit]
        ],
        "total": len(events),
    }


@app.get("/api/temporal/context", tags=["Learning"])
async def get_temporal_context(
    entity_id: str = "",
    hours_back: int = 24,
):
    """Get formatted temporal context for LLM prompt injection."""
    from brain_engine.memory.temporal_memory import TemporalMemory

    if not hasattr(app.state, "temporal_memory"):
        app.state.temporal_memory = TemporalMemory()

    context = await app.state.temporal_memory.get_temporal_context(
        entity_id=entity_id or None,
        hours_back=hours_back,
    )
    return {"context": context}


@app.get("/api/temporal/health", tags=["System"])
async def get_temporal_health():
    """Get temporal memory health statistics."""
    from brain_engine.memory.temporal_memory import TemporalMemory

    if not hasattr(app.state, "temporal_memory"):
        app.state.temporal_memory = TemporalMemory()

    health = await app.state.temporal_memory.get_memory_health()
    return health


@app.post("/api/validate/nli", tags=["System"])
async def check_nli(request: Request):
    """Run NLI contradiction check on a premise/hypothesis pair.

    Body: {
        "premise": "The cleaner is available at 3 PM",
        "hypothesis": "The cleaner is busy and cannot come today"
    }
    """
    from brain_engine.guardrails.nli_checker import NLIChecker

    body = await request.json()

    checker = NLIChecker()
    result = await checker.check_contradiction(
        premise=body.get("premise", ""),
        hypothesis=body.get("hypothesis", ""),
    )
    return {
        "label": result.label.value,
        "confidence": result.confidence,
        "method": result.method,
    }


@app.post("/api/validate/commonsense", tags=["System"])
async def check_commonsense(request: Request):
    """Run ConceptNet commonsense check between two concepts.

    Body: {
        "concept_a": "birthday celebration",
        "concept_b": "funeral home"
    }
    """
    from brain_engine.guardrails.conceptnet import ConceptNetClient

    body = await request.json()
    client = ConceptNetClient(use_api=False)  # Offline mode by default
    result = await client.check_commonsense(
        concept_a=body.get("concept_a", ""),
        concept_b=body.get("concept_b", ""),
    )
    return {
        "concept_a": result.concept_a,
        "concept_b": result.concept_b,
        "is_conflict": result.is_conflict,
        "confidence": result.confidence,
        "explanation": result.explanation,
    }


@app.post("/api/validate/lexical", tags=["System"])
async def check_lexical(request: Request):
    """Run lexical quality check on agent response text.

    Body: {
        "text": "Please be advised that we would like to inform you...",
        "audience": "guest"
    }
    """
    from brain_engine.guardrails.lexical_check import LexicalCheck as _LC

    body = await request.json()
    checker = _LC(audience=body.get("audience", "guest"))
    result = checker.check(body.get("text", ""))
    return {
        "has_issues": result.has_issues,
        "tone_score": result.tone_score,
        "issues": [
            {
                "type": i.issue_type,
                "severity": i.severity,
                "original": i.original,
                "suggestion": i.suggestion,
            }
            for i in result.issues
        ],
        "cleaned_text": result.cleaned_text,
    }
