"""Conversation Service — главный оркестратор конвейера гостевых сообщений.

Обрабатывает сообщение гостя через полный конвейер:
1. Предобработка (очистка HTML, фильтрация системных сообщений)
2. Классификация (16 бизнес-флагов)
3. Intent-классификация (определение намерения + фильтрация инструментов)
4. Выбор гарантий (ALWAYS + CONTEXTUAL)
5. Сборка system prompt (гарантии + тон + пользовательские инструкции)
6. Запуск ReAct-агента (LLM с инструментами)
7. Валидация ответа через guardrails
8. Постобработка (теги, тональность, создание задач)
9. Сборка финального ответа

Единая точка входа для POST /api/v1/conversations.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from brain_engine.conversation.reservation_prefetcher import (
        ReservationPrefetcher,
    )

import litellm

from brain_engine.analysis.models import (
    AnalysisEvent,
    AnalysisEventType,
)
from brain_engine.blockers.engine import BlockerEngine
from brain_engine.context.assembler import ContextAssembler
from brain_engine.conversation.memory_recall import (
    recall_property_scoped,
)
from brain_engine.conversation.missing_info_extractor import (
    MissingInfoRequest,
    extract_missing_information,
)
from brain_engine.conversation.models import (
    BusinessFlags,
    ConversationRequest,
    ConversationResponse,
    PipelineState,
    ReservationContext,
)
from brain_engine.conversation.pm_facts import (
    PmFactStore,
    log_pm_fact_relevance,
)
from brain_engine.conversation.postprocessing import run_postprocessing
from brain_engine.conversation.prompt_formatters import (
    _CALENDAR_NO_DATA_BLOCK,
    _RESERVATION_NO_DATA_BLOCK,
    _format_availability_calendar,
    _format_capacity_sanity_block,
    _format_current_stage_block,
    _format_expired_status_block,
    _format_reservation_context,
    _format_stale_reservation_block,
)
from brain_engine.conversation.prompt_redaction import (
    redact_sensitive_for_status,
)
from brain_engine.conversation.preprocessing import (
    clean_message,
    is_empty_or_media_only,
    is_system_message,
)
from brain_engine.conversation.temporal_pm_hook import (
    maybe_emit_temporal_analysis,
)
from brain_engine.conversation_tools.domain_map import get_tools_for_intent
from brain_engine.customer.models import CustomerSettings
from brain_engine.customer.settings_service import CustomerSettingsService
from brain_engine.customer.tone_system import get_tone_prompt
from brain_engine.guardrails.customer_guardrails import (
    format_guardrails_for_prompt,
    select_guardrails,
)
from brain_engine.guardrails.operational_policies import (
    format_policies_for_prompt,
    policies_for_status,
)
from brain_engine.intent_controller.classifier import (
    IntentClassifier,
    IntentResult,
)
from brain_engine.intent_controller.intents import Intent
from brain_engine.memory.customer_memory import CustomerMemory
from brain_engine.orchestrator.decision import Decision, DecisionContext
from brain_engine.orchestrator.priority_chain import ExecutionOrchestrator
from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.classifier import DecisionClassifier
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.patterns.models import CaseOutcome, PatternRule
from brain_engine.patterns.pms_fetcher import PmsFetcher
from brain_engine.patterns.router import PatternRuleRouter
from brain_engine.patterns.store import DecisionCaseStore
from brain_engine.profiles.models import PropertyProfile
from brain_engine.profiles.store import PropertyProfileStore
from brain_engine.reasoning.business_classifier import (
    BusinessFlagClassifier,
)
from brain_engine.streaming.emit_helpers import (
    emit_intent_classified,
    emit_learning_decision,
    emit_missing_info_detected,
    emit_stage_mismatch_detected,
)

logger = logging.getLogger(__name__)

_AGENT_MODEL: Final[str] = "gpt-4o"
_AGENT_TEMPERATURE: Final[float] = 0.2
_AGENT_MAX_TOKENS: Final[int] = 2000

# Per-request ceiling for every agent ``litellm.acompletion`` call.  Without
# it a stalled provider turn (connection-pool / concurrency saturation) hangs
# the await forever: no exception is raised, the broad ``except`` below never
# fires, the SSE stream goes silent, and the nginx ingress resets the idle
# HTTP/2 stream at its 60s default — surfacing in the browser as
# ``ERR_HTTP2_PROTOCOL_ERROR``.  30s keeps a clean margin under that 60s so a
# stuck turn raises ``litellm.Timeout`` → the ``except`` runs → the pipeline
# emits a fallback reply and ``RUN_FINISHED``, closing the stream cleanly.
_AGENT_REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0

# Минимальная уверенность intent для активации фильтрации инструментов.
# При confidence ниже порога — все инструменты доступны.
_INTENT_CONFIDENCE_THRESHOLD: Final[float] = 0.5

# Task 4 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md
# for the baseline).  ``_load_memory_context`` consults
# ``MemorySystem.semantic`` and ``MemorySystem.episodic`` when the
# operator opts in via ``BRAIN_MEMORY_RETRIEVAL_ENABLED`` *and* a
# ``memory_system`` is injected (Tasks 2 + 3).  Default off keeps
# the pre-Task-4 path bit-for-bit identical: the new pipeline stage
# short-circuits and ``state.memory_facts`` stays at its empty
# default declared in Task 1.
_MEMORY_RETRIEVAL_ENV: Final[str] = "BRAIN_MEMORY_RETRIEVAL_ENABLED"

# Opt-in for the property-scoped unified recall (knowledge graph +
# scoped semantic) added in :mod:`brain_engine.conversation.memory_recall`.
# When off, ``_load_memory_context`` keeps the legacy single semantic
# search so the change is a safe, reversible toggle.
_UNIFIED_RECALL_ENV: Final[str] = "BRAIN_UNIFIED_RECALL_ENABLED"

# Bi-encoder retrieval depth, deliberately wider than the final
# slot count so the optional Sprint A reranker has headroom to
# surface the right answer if the dense ordering is off.
_MEMORY_TOP_N_BI_ENCODER: Final[int] = 20

# Number of facts that actually reach ``state.memory_facts``.  When
# the reranker is enabled, the top-N from the bi-encoder are
# rescored and the best K are kept; when disabled the bi-encoder
# top-K are used directly.
_MEMORY_TOP_K_FINAL: Final[int] = 8

# Conversation summary is only worth assembling once the dialogue
# has accumulated enough turns for compression to add signal.
# Below this threshold the summary block stays empty and recent
# messages alone provide context.
_MEMORY_SUMMARY_MIN_MESSAGES: Final[int] = 5

# Episodes pulled from episodic memory for the summary builder.
# Larger values inflate context-window usage without proportional
# quality gain on the Botel decision-case sample.
_MEMORY_SUMMARY_EPISODES: Final[int] = 10


# Sprint 6 W3 — env flag toggling the FL-05 foundation guardrail
# inside :class:`ConversationService`.  Default off so the live
# hot path stays bit-for-bit identical until the operator opts
# in.  Independent from ``BRAIN_FOUNDATION_LEARN_GATE_ENABLED``
# (which gates the miner) because the guardrail and the learn
# gate target different stages of the pipeline.
_FOUNDATION_GUARDRAIL_ENV: Final[str] = "BRAIN_FOUNDATION_GUARDRAIL_ENABLED"
_GUARDRAIL_FALSY: Final[frozenset[str]] = frozenset(
    {"", "0", "false", "no", "off"},
)


def _foundation_guardrail_enabled() -> bool:
    """Whether :meth:`ConversationService._apply_foundation_guardrail` runs.

    Read on every turn so a deploy can flip
    ``BRAIN_FOUNDATION_GUARDRAIL_ENABLED`` without restarting the
    pod.  Default off — the guardrail step short-circuits and the
    conversation pipeline behaves exactly as it did pre-W3.
    """
    raw = os.environ.get(_FOUNDATION_GUARDRAIL_ENV, "").strip().lower()
    return raw not in _GUARDRAIL_FALSY


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable, otherwise return as-is.

    Lets :meth:`ConversationService._apply_foundation_guardrail`
    accept both synchronous predicates (cheap closures used by
    tests) and asynchronous resolvers (production catalog lookups)
    without forking the call site.
    """
    if inspect.isawaitable(value):
        return await value
    return value


# Sprint 6 W1 — env flag toggling the FL-16 Foundation Analysis
# Orchestrator inside :class:`ConversationService`.  Default off so
# the live hot path stays bit-for-bit identical until the operator
# opts in.  Independent from
# ``BRAIN_FOUNDATION_GUARDRAIL_ENABLED`` (W3) because the
# orchestrator step is the *producer* — it computes the
# :class:`AnalysisResult` — while the guardrail step is one of
# several downstream *consumers*.  Both flags can be flipped
# independently so an operator can enable the orchestrator first
# (observation only — populates ``state.foundation_analysis``) and
# then enable the guardrail once the match quality is validated.
_FOUNDATION_ORCHESTRATOR_ENV: Final[str] = (
    "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED"
)


def _foundation_orchestrator_enabled() -> bool:
    """Whether :meth:`ConversationService._run_foundation_analysis` runs.

    Read on every turn so a deploy can flip
    ``BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED`` without restarting the
    pod.  Default off — the orchestrator step short-circuits and
    ``state.foundation_analysis`` stays ``None``, matching the
    pre-W1 pipeline behaviour exactly.
    """
    raw = (
        os.environ.get(
            _FOUNDATION_ORCHESTRATOR_ENV,
            "",
        )
        .strip()
        .lower()
    )
    return raw not in _GUARDRAIL_FALSY


# R3 — env flag toggling the GuardrailPipeline response validation
# step inside :class:`ConversationService`.  The Cendra adapter path
# already calls ``GuardrailPipeline.validate_response`` (see
# ``cendra_adapter.py:_validate_guest_response``); the AG-UI path
# did not.  The asymmetry meant Sandbox UI replies skipped the
# Tier-1 / Tier-2 / Tier-3 checks (Format, Lexical, Repeat,
# RepeatQuestion, Contradiction, Hallucination) — letting through
# leaks like the WiFi-password / fake-maintenance-dispatch replies
# captured on 2026-05-18.
#
# Default off so a deploy without the pipeline injected stays
# bit-for-bit identical; flip the flag (and pass
# ``guardrail_pipeline=…`` to the service) to enable.
_RESPONSE_VALIDATION_ENV: Final[str] = "BRAIN_RESPONSE_VALIDATION_ENABLED"


def _response_validation_enabled() -> bool:
    """Whether :meth:`ConversationService._validate_agent_response` runs.

    Read on every turn so a deploy can flip
    ``BRAIN_RESPONSE_VALIDATION_ENABLED`` without restarting the pod.
    Default off; truthy values that disable the gate live in
    :data:`_GUARDRAIL_FALSY`.
    """
    raw = (
        os.environ.get(
            _RESPONSE_VALIDATION_ENV,
            "",
        )
        .strip()
        .lower()
    )
    return raw not in _GUARDRAIL_FALSY


class ConversationService:
    """Оркестратор полного конвейера гостевых разговоров.

    Stateless-сервис — всё состояние передаётся через PipelineState.
    Зависимости инжектируются через конструктор (DIP).

    Args:
        settings_service: Загрузчик настроек клиента.
        classifier: Классификатор бизнес-флагов.
        intent_classifier: Классификатор намерений для фильтрации инструментов.
        redis_client: Redis для кеша настроек.
    """

    def __init__(
        self,
        settings_service: CustomerSettingsService | None = None,
        classifier: BusinessFlagClassifier | None = None,
        intent_classifier: IntentClassifier | None = None,
        context_assembler: ContextAssembler | None = None,
        redis_client: Any = None,
        case_builder: CaseBuilder | None = None,
        case_store: DecisionCaseStore | None = None,
        blocker_engine: BlockerEngine | None = None,
        customer_memory: CustomerMemory | None = None,
        decision_classifier: DecisionClassifier | None = None,
        rule_router: PatternRuleRouter | None = None,
        pms_fetcher: PmsFetcher | None = None,
        feature_builder: FeatureBuilder | None = None,
        profile_store: PropertyProfileStore | None = None,
        owner_profile_store: Any = None,
        pm_fact_store: PmFactStore | None = None,
        orchestrator: ExecutionOrchestrator | None = None,
        reservation_prefetcher: ReservationPrefetcher | None = None,
        memory_system: Any = None,
        memory_fanout: Any = None,
        foundation_guardrail_resolver: Any = None,
        foundation_orchestrator: Any = None,
        guardrail_pipeline: Any = None,
    ) -> None:
        self._settings = settings_service or CustomerSettingsService(
            redis_client
        )
        self._classifier = classifier or BusinessFlagClassifier()
        self._intent_classifier = intent_classifier or IntentClassifier()
        self._context_assembler = context_assembler or ContextAssembler()
        self._case_builder = case_builder or CaseBuilder(
            feature_builder=FeatureBuilder(),
        )
        self._case_store = case_store
        # Mümin 2026-05-13 (PR #F): live conversation persistence
        # fans out to Episodic + Semantic + KG via the shared
        # service, mirroring what the bootstrap path does.  When
        # unwired the fan-out is a silent no-op so the rest of
        # the live flow is unaffected.
        from brain_engine.memory.fanout import NullMemoryFanOut

        self._memory_fanout = memory_fanout or NullMemoryFanOut()
        self._blocker_engine = blocker_engine
        self._customer_memory = customer_memory
        self._decision_classifier = decision_classifier or DecisionClassifier()
        # Router + PMS fetcher + feature builder together form the
        # runtime path for learned PatternRules.  All three must be
        # present for rule consultation to occur; a ``None`` in any
        # slot keeps the legacy LLM-only behaviour intact.
        self._rule_router = rule_router
        self._pms_fetcher = pms_fetcher
        self._feature_builder = feature_builder or FeatureBuilder()
        # Cached property knowledge written by the onboarding bootstrap.
        # Preferred over the live PMS REST call because it is keyed by
        # ``propertyChannelId`` (what the chat carries) and contains the
        # full unified static payload (WiFi, parking, amenities, …).
        self._profile_store = profile_store
        # R2 — optional :class:`OwnerProfileStore` providing the
        # per-(owner, property) :class:`OwnerFlexibilityProfile`
        # snapshot.  Surface for the conversation pipeline so amenity
        # carve-outs ("baby crib available for reservations over
        # $2000 at $50"), fee rules, stay rules and check-in policies
        # can land in the LLM's system prompt.  ``None`` keeps the
        # pre-R2 path bit-for-bit identical: the owner block stays
        # empty and the agent answers from
        # ``PropertyProfile.static_payload`` alone — the same shape
        # that caused the baby-crib denial captured on 2026-05-18.
        self._owner_profile_store = owner_profile_store
        # PM-confirmed knowledge captured by the regenerate-pm-knowledge
        # endpoint.  Folded into the system prompt right after the
        # profile cache so the AI answers from manager corrections
        # instead of re-flagging the same gap on every guest turn.
        self._pm_fact_store = pm_fact_store
        # §10 priority-chain orchestrator (Branch 3).  When supplied,
        # the pipeline calls ``decide`` after classification and
        # attaches the verdict to ``state.orchestrator_decision`` so
        # downstream stages (DecisionCase logging, action runner) can
        # see which tier fired.  ``None`` keeps the legacy LLM-only
        # path identical for environments that have not yet wired the
        # owner-flexibility store.
        self._orchestrator = orchestrator
        # Sprint 9 forward-path — optional GraphQL pre-fetcher that
        # enriches ``case_builder.build(pms_data=...)`` with the PMS
        # ``createdAt`` so ``lead_time_hours`` lands on every new
        # ``DecisionCase``.  ``None`` keeps the pre-Sprint-9 path
        # identical (call-site early-returns when the prefetcher is
        # missing).  Bootstrap only constructs the prefetcher when
        # ``BRAIN_LEAD_TIME_FETCH_ENABLED`` is truthy, so the field
        # acts as a feature-flag gate without polluting the service
        # with env-var reads.
        self._reservation_prefetcher = reservation_prefetcher
        # Task 2 of CLAUDE_CODE_WIRING_FIX_PLAN.md — optional handle on
        # the cognitive ``MemorySystem`` (see
        # ``brain_engine.memory.factory.MemorySystem``).  Declared as
        # ``Any`` to avoid a circular import — the same pattern used
        # for ``orchestrator: ExecutionOrchestrator | None`` above.
        # ``None`` keeps the pre-Task-4 path bit-for-bit identical:
        # ``_load_memory_context`` short-circuits when this slot is
        # empty, so existing deployments without the wiring see no
        # behavioural change.
        self._memory_system = memory_system
        # Sprint 6 W3 — optional resolver that consults the FL-01
        # foundation catalog for a ``Should AI Auto-Reply: No``
        # decision.  Signature:
        # ``(message_text: str, property_id: str) -> bool`` — return
        # ``True`` to force the reply into the existing approval mode
        # (PM signs off before the message lands at the guest).
        # When ``None`` *or* the env flag
        # ``BRAIN_FOUNDATION_GUARDRAIL_ENABLED`` is off, the guardrail
        # step is a no-op and the conversation pipeline behaves
        # bit-for-bit identically to pre-W3.  The callable is
        # declared as ``Any`` to avoid pulling
        # :class:`FoundationCatalogStore` into the conversation
        # module's import graph; the production app factory passes a
        # closure that wraps the matcher + catalog lookup.
        self._foundation_guardrail_resolver = foundation_guardrail_resolver
        # Sprint 6 W1 — optional FL-16 Foundation Analysis
        # Orchestrator.  When wired *and* the env flag
        # ``BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED`` is truthy, every
        # guest message produces an
        # :class:`brain_engine.analysis.AnalysisResult` that
        # downstream stages can read off ``state.foundation_analysis``
        # (foundation match, memory routes, origin trail, guardrail
        # block).  Declared as ``Any`` to avoid pulling the analysis
        # package into the conversation module's import graph — the
        # production app factory instantiates the orchestrator and
        # passes it through.  ``None`` keeps the conversation pipeline
        # bit-for-bit identical to pre-W1: ``_run_foundation_analysis``
        # short-circuits and ``state.foundation_analysis`` stays
        # ``None``.
        self._foundation_orchestrator = foundation_orchestrator
        # R3 — optional :class:`brain_engine.guardrails.pipeline.GuardrailPipeline`.
        # The Cendra adapter validates every LLM reply through this
        # pipeline (Format, Lexical, Repeat, Contradiction,
        # Hallucination tiers).  The AG-UI path historically skipped
        # the validation, which let through the WiFi-password leak /
        # fake-maintenance-dispatch replies captured in Sandbox UI.
        # Wired here so ``_run_pipeline`` can invoke the same checks
        # before ``state.agent_response`` reaches ``_build_response``.
        # ``None`` (or the env flag off) keeps the pre-R3 behaviour
        # bit-for-bit identical.
        self._guardrail_pipeline = guardrail_pipeline

    async def process(
        self,
        request: ConversationRequest,
    ) -> ConversationResponse:
        """Process a guest message through the full pipeline.

        Args:
            request: Incoming conversation request.

        Returns:
            ConversationResponse with AI message and metadata.
        """
        state = PipelineState(
            request=request,
            started_at=time.monotonic(),
        )

        try:
            state = await self._run_pipeline(state)
            await self._log_decision_case(state)
            return self._build_response(state)
        except Exception as exc:
            logger.error("Pipeline failed: %s", exc, exc_info=True)
            return ConversationResponse(
                status=False,
                error=str(exc),
                message="I'm sorry, something went wrong. Please try again.",
            )

    async def _run_pipeline(self, state: PipelineState) -> PipelineState:
        """Последовательное выполнение всех стадий конвейера.

        Args:
            state: Начальное состояние конвейера.

        Returns:
            Полностью обработанное состояние.
        """
        state = self._preprocess(state)
        if is_empty_or_media_only(state.cleaned_message):
            state.agent_response = ""
            return state

        # Load customer-level context (cross-property history)
        await self._load_customer_context(state)

        # Load property knowledge (mockup data or PMS → system prompt)
        await self._load_property_knowledge(state)

        # Manager-confirmed facts must be merged regardless of whether
        # ``_load_property_knowledge`` produced any base knowledge — when
        # the request carries no ``property_id`` (some Sandbox v2 / new
        # conversation flows omit it) the live-chat path still needs the
        # customer-wide PM corrections, and when ``property_id`` is set
        # but the mockup / profile / PMS lookups all came back empty the
        # PM facts are the only knowledge we have.  Calling this stage
        # outside ``_load_property_knowledge`` decouples the two concerns
        # and was the root cause of "PM corrections forgotten in the
        # next conversation" reported on 2026-04-28.
        await self._append_pm_facts(state, state.request.property_id or "")

        # Task 4 — populate ``state.memory_facts`` (and optionally
        # ``state.conversation_summary``) from the cognitive memory
        # before classification.  No-op when ``memory_system`` is
        # not injected (Tasks 2 + 3) or
        # ``BRAIN_MEMORY_RETRIEVAL_ENABLED`` is off.  Any failure is
        # logged at WARN and the pipeline continues with empty facts.
        await self._load_memory_context(state)

        state = await self._classify(state)
        intent_result = await self._classify_intent(state)
        settings = await self._settings.get_settings(
            state.request.customer_id,
        )
        # Consult learned PatternRules before the LLM call so the
        # assembled prompt can surface the matched rule's action.
        await self._consult_pattern_rules(state)
        # §10 priority-chain consult — runs after the learned-rule
        # hint so the orchestrator's tier 4 sees the same scenario
        # while still gating on tiers 1-3 (manual / blocker / safety)
        # before falling through to tier 5 (preference) or tier 6
        # (ask).  No-ops when the orchestrator was not injected.
        await self._consult_orchestrator(state)
        # Sprint 6 W1 — Foundation Analysis Orchestrator (FL-16).
        # Runs the embedding matcher + catalog enrichment + origin
        # builder so ``state.foundation_analysis`` carries the full
        # :class:`AnalysisResult` for every guest turn.  Must run
        # before :meth:`_apply_foundation_guardrail` so the W3
        # guardrail consumer can read the match — but the W3
        # resolver path also remains available for catalog-only
        # decisions.  No-op when the orchestrator is unwired or
        # ``BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED`` is off, so the
        # default deploy sees no behaviour change.
        await self._run_foundation_analysis(state)
        # Sprint 6 W3 — foundation safety guardrail.  When the
        # FL-01 catalog says ``Should AI Auto-Reply: No`` for the
        # matched scenario the turn is forced into approval mode
        # (LLM still drafts, PM signs off).  No-op when the
        # resolver is unwired or
        # ``BRAIN_FOUNDATION_GUARDRAIL_ENABLED`` is off, so the
        # default deploy sees no behaviour change.
        await self._apply_foundation_guardrail(state)
        # Branch 4: hand the §10 verdict authority to gate the LLM
        # agent.  ``block`` short-circuits with a deterministic deny;
        # ``approval`` lets the LLM draft a reply but flags the turn
        # for PM sign-off; ``auto`` / ``ask`` / no-orchestrator paths
        # fall through unchanged.
        skip_agent = self._enforce_orchestrator_verdict(state)
        state = self._assemble_prompt(state, settings)
        if not skip_agent:
            state = await self._run_agent(state, settings, intent_result)

        # R3 — validate the draft response BEFORE the AG-UI handler
        # bridges ``state.agent_response`` into TEXT_MESSAGE_CONTENT
        # SSE events.  The pipeline does not stream during LLM
        # generation (see server.py:1012 — final_text comes from the
        # populated state, not mid-pipeline tokens), so a synchronous
        # validation step can still rewrite the text before the
        # guest sees it.  No-op when the env flag is off or the
        # GuardrailPipeline was not injected.
        self._validate_agent_response(state)

        # Side-effect: emit missing-info SSE event for sandbox v2 / AG-UI clients.
        # 2026-05-18 Aybüke bug fix: pass the FL-16 foundation
        # analysis so the topic in the SSE payload comes from the
        # matched catalog scenario instead of an LLM guess.  The
        # legacy path let the extractor LLM pick "<topic>", which
        # caused hallucinations like reporting "pricing" on an
        # early-checkin thread.
        await _maybe_emit_missing_info(
            ai_message=state.agent_response or "",
            conversation=state.request,
            foundation_analysis=getattr(state, "foundation_analysis", None),
        )

        # Side-effect: FL-16 Q5-C visibility — surface stage
        # contradictions (Mümin's 2026-05-18 adversarial calendar-
        # vs-message tests) to PM Chat as STAGE_MISMATCH_DETECTED.
        # The orchestrator's ``_detect_stage_contradiction`` step
        # set ``state.foundation_analysis.stage_mismatch`` earlier;
        # this helper emits the SSE event so the operator (and
        # Mümin's regression harness) sees Brain caught the
        # contradiction.  Variant A: observation only — Brain
        # still answers; this is the visibility channel.
        _maybe_emit_stage_mismatch(
            foundation_analysis=getattr(state, "foundation_analysis", None),
            conversation=state.request,
        )

        # Side-effect: surface ops-grade events (maintenance / emergency
        # / security / cleaning / noise) to PM Chat.  Mümin's reviewer
        # reported on 2026-04-28 that "ops işlemleri PM'e düşmüyor" —
        # the missing-info path only fired for soft-deferral language,
        # so a clear maintenance complaint never reached the PM panel
        # even though the classifier had already flagged it.  Sharing
        # the MISSING_INFO_DETECTED channel keeps the UI contract
        # stable; the ``source_field`` distinguishes the trigger so
        # operations can route differently downstream.
        _maybe_emit_ops_attention(
            business_flags=state.business_flags,
            guest_message=state.cleaned_message,
            conversation=state.request,
        )

        # Side-effect: surface a temporal analysis of the client to PM
        # Chat (Phase 3 PR3c.1).  Flag-gated default-off
        # (BRAIN_TEMPORAL_PM_ENABLED) and fully non-fatal — all logic
        # lives in the hook module so this stays a single call.
        await maybe_emit_temporal_analysis(
            property_id=state.request.property_id or "",
            customer_id=state.request.customer_id or "",
        )

        # TODO(sandbox-v2): wire LEARNING_DECISION emission once Mem0 fact
        # extraction is integrated into the synchronous pipeline. Helper
        # `_emit_learning_decision_for_fact` is ready to be called per-fact.

        state = await run_postprocessing(state)
        return state

    def _preprocess(self, state: PipelineState) -> PipelineState:
        """Stage 1: Clean and normalize the message.

        Args:
            state: Pipeline state with raw request.

        Returns:
            State with cleaned_message set.
        """
        raw = state.request.latest_message
        state.raw_message = raw
        state.cleaned_message = clean_message(raw)

        if is_system_message(state.cleaned_message):
            logger.info("Skipping system message: %s", raw[:80])
            state.cleaned_message = ""

        return state

    async def _classify_intent(self, state: PipelineState) -> IntentResult:
        """Стадия 2b: Классификация намерения для фильтрации инструментов.

        При ошибке возвращает UNKNOWN с нулевой уверенностью — fallback
        на полный набор инструментов.

        Args:
            state: Состояние конвейера с очищенным сообщением.

        Returns:
            Результат классификации намерения.
        """
        try:
            result = await self._intent_classifier.classify(
                user_message=state.cleaned_message,
                conversation_context=state.request.history_for_llm,
            )
            logger.info(
                "Intent classified: intent=%s confidence=%.2f reasoning=%s",
                result.intent.value,
                result.confidence,
                result.reasoning[:80],
            )
            return result
        except Exception as exc:
            logger.warning(
                "Intent classification failed, fallback to UNKNOWN: %s", exc
            )
            return IntentResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                reasoning="classification_failed",
            )

    async def _classify(self, state: PipelineState) -> PipelineState:
        """Стадия 2: Классификация сообщения в бизнес-флаги.

        Args:
            state: Состояние конвейера с очищенным сообщением.

        Returns:
            Состояние с заполненными business_flags.
        """
        result = await self._classifier.classify(
            message=state.cleaned_message,
            conversation_history=state.request.history_for_llm,
        )

        state.business_flags = BusinessFlags(
            **result.to_business_flags(),
            scenario_hint=result.scenario_hint,
            decision_type_hint=result.decision_type_hint,
        )
        state.response_language = result.response_language
        state.classification_confidence = result.confidence

        emit_intent_classified(
            intent=result.suggested_category or "unclassified",
            confidence=result.confidence,
            raw_label=result.suggested_subcategory or None,
        )

        logger.info(
            "Classified: flags=%s lang=%s confidence=%.2f",
            state.business_flags.active_flags(),
            result.response_language,
            result.confidence,
        )
        return state

    def _assemble_prompt(
        self,
        state: PipelineState,
        settings: CustomerSettings,
    ) -> PipelineState:
        """Stage 3-4: Build system prompt with three-section memory context.

        Injects [ESTABLISHED FACTS] / [CONVERSATION SUMMARY] / [RECENT MESSAGES]
        between the base prompt and guardrails.  Facts are placed first to exploit
        LLM primacy bias (see "Lost in the Middle" research).

        Args:
            state: Pipeline state with classification.
            settings: Customer settings.

        Returns:
            State with system_prompt and tone_prompt set.
        """
        active_flags = state.business_flags.active_flags()

        guardrails = select_guardrails(settings, active_flags)
        state.active_guardrails = [g.title for g in guardrails]
        guardrail_text = format_guardrails_for_prompt(guardrails)

        # Status-driven operational policies: surfaces the SECURITY
        # clause for inquiry/preapproved statuses so the LLM does not
        # share WiFi passwords / lock codes / GPS before booking.
        # Empty string when no booking context (status-less request)
        # or no policy matches — keeps existing prompts byte-identical.
        matched_policies = policies_for_status(
            _reservation_status(state.request),
        )
        state.active_operational_policies = [p.title for p in matched_policies]
        operational_policy_text = format_policies_for_prompt(matched_policies)

        # R7 — surface the FL-01 catalog scenario the orchestrator
        # matched against the incoming message so the LLM follows the
        # scenario's policy (AI default behavior, auto-reply gate,
        # escalation rule, required data checks).  Pre-R7 the
        # orchestrator populated ``state.foundation_analysis`` for
        # logging / SSE side effects only — the LLM never saw the
        # catalog entry, which is why Sandbox UI replies looked
        # generic while the Postman ``/foundation/analyze`` endpoint
        # surfaced the match correctly.
        foundation_scenario_text = _format_foundation_scenario_hint(
            getattr(state, "foundation_analysis", None),
        )

        tone_text = get_tone_prompt(settings)
        state.tone_prompt = tone_text

        lang_instruction = _reply_language_instruction(
            settings.respond_language,
        )

        custom = settings.custom_instructions
        custom_block = f"\n## Custom Instructions\n{custom}" if custom else ""

        # Three-section memory context (Phase 2 Task 2.3)
        memory_context = self._build_memory_context(state)

        # Customer-level context (cross-property history)
        customer_context = state.customer_context or ""

        # Property knowledge base (from mockup / PMS).  R9.B —
        # when the reservation status is pre-booking (inquiry,
        # follow_up, inquiryPreapproved, inquirynotpossible) we
        # strip sensitive value lines (WiFi password, door /
        # lock / safe codes, exact GPS) so the LLM cannot leak
        # them even if a manager correction landed them in the
        # PM-facts append (which is marked "authoritative" and
        # otherwise wins over the abstract SECURITY policy).
        property_kb = redact_sensitive_for_status(
            state.property_knowledge or "",
            _reservation_status(state.request),
        )

        # Learned-pattern hint (populated by _consult_pattern_rules).
        # Empty string when no rule matched — keeps legacy prompts
        # byte-identical for rule-free turns.
        pattern_hint = _format_matched_rule(state.matched_rule)
        pattern_block = f"\n\n{pattern_hint}" if pattern_hint else ""

        # Grounded reservation snapshot from the request, plus an
        # explicit anti-fabrication directive.  When the snapshot is
        # absent (no booking attached / pre-stay enquiry), the directive
        # still fires so the model defers instead of inventing dates.
        reservation_block = _format_reservation_context(
            getattr(state.request, "reservation_context", None),
        )

        # Per-day availability snapshot pulled upstream from the
        # unified GraphQL ``ratePlans.calendar`` view.  The block
        # quotes each day's status verbatim and forces a deferral when
        # the window is empty so the model cannot improvise a "müsait"
        # answer for blocked dates.
        availability_block = _format_availability_calendar(
            getattr(state.request, "availability_calendar", []),
        )

        operational_policy_block = (
            f"\n\n{operational_policy_text}"
            if operational_policy_text
            else ""
        )
        foundation_scenario_block = (
            f"\n\n{foundation_scenario_text}"
            if foundation_scenario_text
            else ""
        )
        # R12 — expired booking hard deferral.  Placed immediately
        # after the base prompt (before property knowledge and the
        # reservation snapshot) so the "do not confirm / do not
        # share codes / do not offer modifications" instruction
        # wins the LLM's primacy bias.  Empty string for every
        # active reservation status — pre-R12 prompts stay
        # byte-identical for non-expired requests.
        expired_block_text = _format_expired_status_block(
            _reservation_status(state.request),
        )
        expired_block = (
            f"{expired_block_text}\n\n" if expired_block_text else ""
        )
        # R13 — stale reservation detection.  Defensive complement
        # to R12: emits a hard-deferral block when check_out lies
        # strictly before the message timestamp (PMS sync lag,
        # cancelled-but-not-relabelled, sandbox testing with fixed
        # dates).  Placed alongside R12 in the same primacy slot.
        reservation_ctx = getattr(state.request, "reservation_context", None)
        stale_block_text = _format_stale_reservation_block(
            getattr(reservation_ctx, "check_out", "") or "",
            getattr(reservation_ctx, "current_time", "") or "",
        )
        stale_block = (
            f"{stale_block_text}\n\n" if stale_block_text else ""
        )
        # R14 — capacity sanity guard.  An active booking
        # (post-Inquiry) carrying ``num_guests=0`` AND
        # ``num_children=0`` is a data gap, not a real "0 guests
        # so far" fact.  Emits a CAUTION block so the LLM does
        # not run ``0 + N = N`` math against the property max
        # when a guest asks to bring additional people.  Empty
        # string for legitimate cases (counts populated /
        # pre-booking status / status missing).
        capacity_sanity_text = _format_capacity_sanity_block(
            getattr(reservation_ctx, "status", "") or "",
            int(getattr(reservation_ctx, "num_guests", 0) or 0),
            int(getattr(reservation_ctx, "num_children", 0) or 0),
        )
        capacity_sanity_block = (
            f"{capacity_sanity_text}\n\n" if capacity_sanity_text else ""
        )
        # Phase 1 — derived stage block (Sandbox tester 2026-05-20).
        # Tells the LLM whether ``current_time`` lies before, inside,
        # or after the booking window so sensitive-info release is
        # gated by what the calendar says — not by the static PMS
        # ``Status`` label that stays ``confirmed`` across the entire
        # lifecycle.  Empty string for unparseable dates, expired
        # bookings (already handled by R12), and post-checkout
        # bookings (already handled by R13 stale block).
        current_stage_text = _format_current_stage_block(
            getattr(reservation_ctx, "status", "") or "",
            getattr(reservation_ctx, "check_in", "") or "",
            getattr(reservation_ctx, "check_out", "") or "",
            getattr(reservation_ctx, "current_time", "") or "",
        )
        current_stage_block = (
            f"{current_stage_text}\n\n" if current_stage_text else ""
        )

        state.system_prompt = (
            f"{_BASE_SYSTEM_PROMPT}\n\n"
            f"{expired_block}"
            f"{stale_block}"
            f"{capacity_sanity_block}"
            f"{current_stage_block}"
            f"{property_kb}\n\n"
            f"{customer_context}\n\n"
            f"{memory_context}\n\n"
            f"{reservation_block}\n\n"
            f"{availability_block}\n\n"
            f"{tone_text}\n\n"
            f"{guardrail_text}"
            f"{operational_policy_block}"
            f"{foundation_scenario_block}\n"
            f"{custom_block}"
            f"{pattern_block}"
            f"{lang_instruction}"
        )

        return state

    def _build_memory_context(self, state: PipelineState) -> str:
        """Assemble the three-section memory block for the prompt.

        Pulls facts from state.memory_facts (populated by Mem0/SemanticMemory
        if available), summary from state.conversation_summary, and recent
        messages from the request history.

        Falls back gracefully: if no facts or summary are available, those
        sections are simply omitted from the output.

        Args:
            state: Pipeline state with request and optional memory data.

        Returns:
            Rendered context string, or empty string if no data available.
        """
        # Direct attribute access — fields declared on PipelineState
        # in Task 1 (see docs/wiring_audit.md baseline).  Defaults are
        # ``[]`` and ``""`` so the pre-Task-4 path keeps producing an
        # empty memory block without the prior defensive ``getattr``.
        facts = state.memory_facts
        summary = state.conversation_summary

        # Last 5 messages as recent context
        recent = state.request.history_for_llm[-5:]

        if not facts and not summary and not recent:
            return ""

        assembled = self._context_assembler.assemble(
            facts=facts,
            summary=summary,
            recent_messages=recent,
        )
        return assembled.text

    async def _run_agent(
        self,
        state: PipelineState,
        settings: CustomerSettings,
        intent_result: IntentResult | None = None,
    ) -> PipelineState:
        """Стадия 5: Запуск ReAct-агента с инструментами.

        Фильтрует инструменты по intent + customer toggles.

        Args:
            state: Состояние конвейера с system prompt.
            settings: Настройки клиента для переключения инструментов.
            intent_result: Результат intent-классификации для фильтрации.

        Returns:
            Состояние с заполненным agent_response.
        """
        tools = self._get_enabled_tools(settings, intent_result)
        tool_defs = _build_tool_definitions(tools)

        messages = _build_agent_messages(state)

        try:
            response = await litellm.acompletion(
                model=_AGENT_MODEL,
                messages=messages,
                tools=tool_defs if tool_defs else None,
                temperature=_AGENT_TEMPERATURE,
                max_tokens=_AGENT_MAX_TOKENS,
                timeout=_AGENT_REQUEST_TIMEOUT_SECONDS,
            )

            msg = response.choices[0].message
            if msg.tool_calls:
                state = await self._execute_tool_calls(
                    state,
                    msg.tool_calls,
                    tools,
                    messages,
                )
            else:
                state.agent_response = msg.content or ""

        except Exception as exc:
            logger.error("Agent execution failed: %s", exc, exc_info=True)
            state.agent_response = (
                "I apologize for the inconvenience. "
                "Let me check and get back to you shortly."
            )

        return state

    async def _execute_tool_calls(
        self,
        state: PipelineState,
        tool_calls: list[Any],
        tools: list[Any],
        messages: list[dict[str, Any]],
    ) -> PipelineState:
        """Execute tool calls and get final response.

        Runs up to 3 rounds of tool calls (agent loop).

        Args:
            state: Pipeline state.
            tool_calls: Pending tool calls from LLM.
            tools: Available tool functions.
            messages: Current message list.

        Returns:
            State with agent_response from final LLM call.
        """
        tool_map = {_get_tool_name(t): t for t in tools}
        max_rounds = 3

        for _ in range(max_rounds):
            # Add assistant message with tool calls
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            # Execute each tool call (with blocker check for sensitive actions)
            for tc in tool_calls:
                blocked_msg = await self._check_tool_blockers(tc, state)
                if blocked_msg:
                    result = blocked_msg
                else:
                    result = await _call_tool(
                        tool_map,
                        tc,
                        state,
                    )
                state.tools_used.append(tc.function.name)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

            # Get next LLM response
            response = await litellm.acompletion(
                model=_AGENT_MODEL,
                messages=messages,
                tools=_build_tool_definitions(tools),
                temperature=_AGENT_TEMPERATURE,
                max_tokens=_AGENT_MAX_TOKENS,
                timeout=_AGENT_REQUEST_TIMEOUT_SECONDS,
            )

            msg = response.choices[0].message
            if msg.tool_calls:
                tool_calls = msg.tool_calls
                continue

            state.agent_response = msg.content or ""
            return state

        state.agent_response = msg.content or ""
        return state

    async def _load_property_knowledge(self, state: PipelineState) -> None:
        """Load property knowledge into state from mockup or PMS.

        Tries mockup loader first (config/properties.json). If not found,
        falls back to PMS API to fetch basic property info (name, address,
        amenities, check-in/out times). This ensures any PMS property
        has at least baseline knowledge in the system prompt.

        After the base knowledge is loaded the manager-confirmed facts
        captured by the regenerate-pm-knowledge endpoint are appended,
        so a PM correction taught earlier (e.g. WiFi password) is the
        first thing the AI sees on the next guest turn.

        Args:
            state: Pipeline state with request data.
        """
        property_id = state.request.property_id or ""
        if not property_id:
            return

        loaded = False

        # Try mockup data first (has WiFi, codes, rules)
        try:
            from brain_engine.api.mockup_loader import get_property

            prop = get_property(property_id)
            if prop:
                state.property_knowledge = _format_mockup_knowledge(prop)
                logger.debug(
                    "Property knowledge from mockup: %d chars, property=%s",
                    len(state.property_knowledge),
                    property_id,
                )
                loaded = True
        except Exception:
            logger.debug("Mockup loader failed", exc_info=True)

        # Cached profile from the onboarding bootstrap (channelEntityId).
        # Mounted before the PMS REST fallback because the chat carries
        # propertyChannelId, which the PMS endpoint rejects with HTTP 400.
        if not loaded and self._profile_store is not None:
            try:
                profile = await self._profile_store.get(property_id)
            except Exception:
                profile = None
                logger.debug("Profile store lookup failed", exc_info=True)
            if profile is not None:
                state.property_knowledge = _format_profile_knowledge(profile)
                logger.info(
                    "Property knowledge from profile cache: %d chars, "
                    "property=%s",
                    len(state.property_knowledge),
                    property_id,
                )
                loaded = True

        # R2 — append owner flexibility rules (amenity exceptions,
        # fee rules, stay rules, check-in policies) when a profile
        # store is wired AND the property has an owner profile.  This
        # is the missing surface that produced the baby-crib denial
        # captured on 2026-05-18: the static_payload had no entry
        # for the conditional "baby crib for reservations over $2000"
        # carve-out the owner had stored in
        # ``owner_flexibility_profiles.amenity_exceptions``.  Failures
        # are non-fatal — a missing owner snapshot must not break the
        # base property-knowledge load.
        if self._owner_profile_store is not None and property_id:
            owner_block = await self._load_owner_flexibility_block(
                state,
                property_id,
            )
            if owner_block:
                separator = "\n\n" if state.property_knowledge else ""
                state.property_knowledge = (
                    f"{state.property_knowledge}{separator}{owner_block}"
                )

        # PMS REST fallback retired 2026-04-28; property knowledge now
        # comes from the profile cache (UnifiedData GraphQL → harvester
        # → :class:`PropertyProfileStore`).  When the cache miss leaves
        # ``state.property_knowledge`` empty the conversation continues
        # without static knowledge — runtime tools (availability,
        # rateplan, RAG) still answer specific questions on demand.

        # NOTE: ``_append_pm_facts`` is intentionally NOT called from
        # here — it now runs unconditionally from ``_run_pipeline`` so
        # the empty ``property_id`` early-return above does not block
        # the PM-correction read path.  Customer-wide facts must still
        # surface when a request omits ``property_id``.

    async def _load_owner_flexibility_block(
        self,
        state: PipelineState,
        property_id: str,
    ) -> str:
        """Fetch the owner flexibility snapshot and render it as text.

        Looks up the snapshot via the injected
        :class:`OwnerProfileStore` keyed by ``(owner_id, property_id)``
        — ``owner_id`` is resolved with :meth:`_resolve_owner_id`,
        which already falls back to ``customer_id`` for the V1 mapping.

        Returns ``""`` (so the caller can splice without a separator)
        in three short-circuit cases:

        1. The store is not injected — handled before this method
           runs by the call site, but checked again here so the
           helper is safe to invoke directly from tests.
        2. ``owner_id`` cannot be resolved.
        3. The store returned ``None`` (no snapshot for this owner
           / property) or raised — store errors are swallowed and
           logged at debug, the live chat must not depend on a
           healthy owner store.

        Otherwise renders the snapshot via
        :func:`_format_owner_flexibility` and returns the text.
        """
        if self._owner_profile_store is None:
            return ""
        owner_id = (await self._resolve_owner_id(state)).strip()
        if not owner_id:
            return ""
        try:
            owner_profile = await self._owner_profile_store.get(
                owner_id,
                property_id,
            )
        except Exception:
            logger.debug(
                "OwnerProfileStore lookup failed",
                exc_info=True,
            )
            return ""
        if owner_profile is None:
            return ""
        block = _format_owner_flexibility(owner_profile)
        if block:
            logger.info(
                "Owner flexibility loaded: owner=%s property=%s",
                owner_id,
                property_id,
            )
        return block

    async def _append_pm_facts(
        self,
        state: PipelineState,
        property_id: str,
    ) -> None:
        """Fold PM-confirmed knowledge into ``state.property_knowledge``.

        Reads from the injected :class:`PmFactStore` for the
        (customer, property) scope plus customer-wide rows.  Failures
        are non-fatal — the live-chat pipeline must never go down
        because the fact store is unreachable.
        """
        if self._pm_fact_store is None:
            return
        customer_id = state.request.customer_id or ""
        if not customer_id:
            return

        try:
            facts = await self._pm_fact_store.list_facts(
                customer_id=customer_id,
                property_channel_id=property_id,
            )
        except Exception:
            logger.warning(
                "PmFactStore.list_facts failed",
                exc_info=True,
            )
            return

        if not facts:
            return

        bullet_lines = "\n".join(
            f"- {fact.fact_text.strip()}"
            for fact in facts
            if fact.fact_text.strip()
        )
        if not bullet_lines:
            return

        appendix = (
            "\n\nMANAGER-CONFIRMED KNOWLEDGE "
            "(authoritative — prefer over generic defaults):\n"
            f"{bullet_lines}"
        )
        state.property_knowledge = (state.property_knowledge or "") + appendix
        logger.info(
            "PM facts merged into property knowledge: count=%d "
            "property=%s customer=%s",
            len(facts),
            property_id,
            customer_id,
        )

        # Diagnostic (measure-before-fix): PURE OBSERVABILITY.  The
        # helper owns the metric + emit so this file does not grow.
        # Gathered so the team can decide on evidence whether to
        # move from "dump every PM fact" to topic-relevant top-K
        # retrieval (tester complaint #4).
        log_pm_fact_relevance(
            [fact.fact_text for fact in facts],
            state.cleaned_message or "",
            property_id=property_id,
            customer_id=customer_id,
            logger=logger,
        )

    async def _load_memory_context(self, state: PipelineState) -> None:
        """Populate ``state.memory_facts`` and ``conversation_summary``.

        Two-stage retrieval against the injected
        :class:`brain_engine.memory.factory.MemorySystem`:

        1. Bi-encoder search via ``MemorySystem.semantic.search`` for
           the top-N candidates, scoped through a metadata filter
           built from the request's ``customer_id`` and
           ``property_id``.  The filter is the multi-tenancy guard —
           one customer's facts must never leak into another's
           conversation.
        2. When ``BRAIN_RERANKER_ENABLED`` is truthy, the Sprint A
           cross-encoder rescores the candidates and the top-K best
           are kept; otherwise the bi-encoder top-K reach the state.

        When the conversation has accumulated more than
        :data:`_MEMORY_SUMMARY_MIN_MESSAGES` turns, a summary is also
        assembled from the most recent episodic entries.

        No-op when:
        * the ``memory_system`` slot is empty (Task 3 lifespan flag
          off, the legacy memory was never aliased);
        * ``BRAIN_MEMORY_RETRIEVAL_ENABLED`` is falsy;
        * the request carries no cleaned message to query against.

        Any exception during retrieval is caught at WARN — the
        pipeline must never go down because the cognitive memory is
        slow or unreachable.
        """
        if self._memory_system is None:
            return
        if not _memory_retrieval_enabled():
            return
        query = state.cleaned_message
        if not query:
            return

        if _unified_recall_enabled():
            # Property-scoped recall across the knowledge graph and a
            # scoped semantic search.  ``recall_property_scoped`` fails
            # open to ``[]`` itself, so a tier outage degrades to the
            # legacy-empty behaviour rather than breaking the reply.
            state.memory_facts = await recall_property_scoped(
                memory_system=self._memory_system,
                property_id=state.request.property_id,
                query=query,
                status=_reservation_status(state.request),
                conversation_id=(
                    getattr(state.request, "conversation_id", "") or ""
                ),
            )
            logger.info(
                "memory_context.loaded facts=%d mode=unified",
                len(state.memory_facts),
            )
            return

        try:
            metadata_filter = _build_memory_filter(state.request)
            records = await self._memory_system.semantic.search(
                query=query,
                top_k=_MEMORY_TOP_N_BI_ENCODER,
                metadata_filter=metadata_filter or None,
            )

            facts = self._maybe_rerank_records(query, records)
            state.memory_facts = facts

            if len(state.request.messages) > _MEMORY_SUMMARY_MIN_MESSAGES:
                summary = await self._build_conversation_summary()
                state.conversation_summary = summary

            logger.info(
                "memory_context.loaded facts=%d summary_chars=%d reranked=%s",
                len(state.memory_facts),
                len(state.conversation_summary),
                _reranker_enabled(),
            )
        except Exception as exc:  # fail open — pipeline must not break
            # Memory retrieval is non-critical: log and continue with
            # whatever the state already had (empty defaults from
            # Task 1).  The agent will still answer using property
            # knowledge and PM facts.
            logger.warning(
                "memory_context.load_failed (%s): %s",
                type(exc).__name__,
                exc,
            )

    def _maybe_rerank_records(
        self,
        query: str,
        records: list[Any],
    ) -> list[str]:
        """Apply the Sprint A cross-encoder when enabled, else slice.

        Returns the final list of fact texts that lands on
        ``state.memory_facts``.  Reranker construction is lazy and
        cached for the lifetime of the service so repeated calls
        within one process do not reload the 568 MB checkpoint.
        """
        if not records:
            return []

        if _reranker_enabled():
            reranker = self._get_or_build_reranker()
            if reranker is not None:
                reranked = reranker.rerank(
                    query=query,
                    candidates=list(records),
                    text_of=_record_text,
                    top_n=_MEMORY_TOP_K_FINAL,
                )
                return [_record_text(r.item) for r in reranked]

        return [_record_text(r) for r in records[:_MEMORY_TOP_K_FINAL]]

    def _get_or_build_reranker(self) -> Any:
        """Lazy-build the Sprint A reranker, cache on the service.

        Returns ``None`` when ``BRAIN_RERANKER_ENABLED`` is falsy or
        the underlying ``sentence_transformers`` import fails.  The
        cache attribute is created on demand so existing tests that
        instantiate ``ConversationService()`` without ever invoking
        the memory path do not pay any reranker cost.
        """
        cache = getattr(self, "_reranker_cache", _UNSET)
        if cache is _UNSET:
            from brain_engine.memory.reranker import (
                build_default_reranker,
            )

            try:
                cache = build_default_reranker()
            except Exception as exc:  # fail open — fall back to bi-encoder
                logger.warning(
                    "reranker.build_failed (%s): %s",
                    type(exc).__name__,
                    exc,
                )
                cache = None
            self._reranker_cache = cache
        return cache

    async def _build_conversation_summary(self) -> str:
        """Assemble a flat summary string from recent episodic events.

        Empty when the episodic store is unavailable or returns no
        entries.  The current implementation is intentionally a
        deterministic concatenation — a future iteration can swap in
        an LLM-summariser without changing the call site.
        """
        if self._memory_system is None:
            return ""
        try:
            episodes = await self._memory_system.episodic.get_recent(
                n=_MEMORY_SUMMARY_EPISODES,
            )
        except Exception as exc:  # fail open — empty summary is fine
            logger.warning(
                "memory_context.episodic_failed (%s): %s",
                type(exc).__name__,
                exc,
            )
            return ""
        if not episodes:
            return ""
        return _summarize_episodes(episodes)

    async def _consult_pattern_rules(self, state: PipelineState) -> None:
        """Consult the learned-rule runtime and annotate ``state``.

        Runs after classification and before prompt assembly.  The stage
        is a no-op when any required dependency is absent — the legacy
        LLM-only path is strictly preserved unless the full chain
        (router + PMS fetcher + reservation + property) is present.

        Side effects:
            Sets :attr:`PipelineState.matched_rule` to the
            :class:`PatternRule` returned by
            :meth:`PatternRuleRouter.match`, or leaves it as ``None``.

        Error handling:
            Every external call (PMS fetch, feature build, router
            lookup) is guarded — a failure here must not surface to
            the guest.  On any error the stage logs and returns, and
            the pipeline falls through to plain LLM generation.

        Args:
            state: Pipeline state with business flags already populated.
        """
        if self._rule_router is None or self._pms_fetcher is None:
            return

        reservation_id = (state.request.reservation_id or "").strip()
        property_id = (state.request.property_id or "").strip()
        if not reservation_id or not property_id:
            return

        try:
            reservation = await self._pms_fetcher.get_reservation(
                reservation_id,
            )
        except Exception:
            logger.warning(
                "PatternRule consult: PMS reservation fetch failed",
                exc_info=True,
            )
            reservation = None

        # Sandbox / no-PMS fallback: when the unified GraphQL lookup
        # finds nothing for this reservation_id (typical of sandbox UI
        # which mints fake UUIDs that never reach Elasticsearch), fall
        # through to whatever the client shipped in
        # ``state.request.reservation_context``.  Without this branch
        # learned rules cannot be exercised in sandbox mode because the
        # feature dict stays empty and every condition fails on
        # ``actual is None``.  Live traffic with a real reservation_id
        # is unaffected — the ES record wins, the fallback is skipped.
        if not reservation:
            ctx = getattr(state.request, "reservation_context", None)
            reservation = _reservation_context_to_feature_dict(ctx)
            if not reservation:
                return
            logger.info(
                "PatternRule consult: reservation_context fallback used",
                reservation_id=reservation_id,
                property_id=property_id,
            )

        check_in = str(reservation.get("check_in", ""))
        check_out = str(reservation.get("check_out", ""))
        try:
            calendar = await self._pms_fetcher.get_calendar(
                property_id,
                check_in,
                check_out,
            )
        except Exception:
            logger.debug(
                "PatternRule consult: PMS calendar fetch failed, "
                "continuing with empty calendar",
                exc_info=True,
            )
            calendar = None

        try:
            features = self._feature_builder.build(
                reservation,
                calendar or {},
            ).to_dict()
        except Exception:
            logger.warning(
                "PatternRule consult: feature build failed",
                exc_info=True,
            )
            return

        # The PMS reservation already has check_in/check_out fetched
        # above; current_time comes from the UI-supplied
        # ReservationContext (sandbox v2 has a "Message Sent Date"
        # picker).  Empty values fall back to keyword logic.
        reservation_context = getattr(
            state.request,
            "reservation_context",
            None,
        )
        current_time_iso = (
            getattr(reservation_context, "current_time", "") or ""
        )

        classification = self._decision_classifier.classify(
            business_flags=state.business_flags,
            message_text=state.cleaned_message or "",
            response_text="",
            tools_used=tuple(state.tools_used),
            reservation_id=reservation_id,
            check_in=check_in,
            check_out=check_out,
            current_time=current_time_iso,
        )

        owner_id = getattr(state.request, "owner_id", "") or ""
        portfolio_id = getattr(state.request, "portfolio_id", "") or ""

        # Sprint-1 bi-temporal anchor: pass the message-sent timestamp
        # to the router so historical sandbox replays (and out-of-order
        # ingestion) match the rule that was *in effect at that
        # moment*, not whatever is active today.  An unparseable or
        # missing timestamp falls back to ``None``, preserving the
        # pre-Sprint-1 "active rules only" behaviour for live traffic.
        as_of = _parse_iso_timestamp(current_time_iso)

        try:
            match = await self._rule_router.match(
                scenario=classification.scenario,
                property_id=property_id,
                owner_id=owner_id or None,
                portfolio_id=portfolio_id or None,
                features=features,
                as_of=as_of,
            )
        except Exception:
            logger.warning(
                "PatternRule consult: router match failed",
                exc_info=True,
            )
            return

        if match is not None:
            state.matched_rule = match.rule
            logger.debug(
                "PatternRule matched: id=%s scope=%s confidence=%.2f",
                match.rule.pattern_id[:8],
                match.scope.value,
                match.rule.confidence,
            )

    async def _consult_orchestrator(self, state: PipelineState) -> None:
        """Walk the §10 priority chain and annotate ``state``.

        Builds a :class:`DecisionContext` from the request + classifier
        outputs, calls :meth:`ExecutionOrchestrator.decide`, and stores
        the verdict on :attr:`PipelineState.orchestrator_decision`.
        Failures are swallowed so a flaky resolver cannot break the
        guest-facing reply — the orchestrator is advisory in Branch 3
        and gains hard short-circuit power only once the action runner
        in Branch 4 honours its verdicts.

        The Cendra ``ownerId`` is not exposed by the unified GraphQL
        layer, so the V1 mapping treats ``customer_id`` as the owner
        scope.  When :class:`PropertyProfile` ships a non-empty
        ``owner_id`` (Cendra MCP path), it wins; the request-level
        ``customer_id`` is the always-available fallback.

        Args:
            state: Pipeline state with classification populated.
        """
        if self._orchestrator is None:
            return

        scenario = self._derive_scenario(state)
        owner_id = await self._resolve_owner_id(state)
        property_id = (state.request.property_id or "").strip()
        if not scenario or not owner_id or not property_id:
            return

        ctx = DecisionContext(
            scenario=scenario,
            property_id=property_id,
            owner_id=owner_id,
            tenant_id=(state.request.org_id or "").strip(),
            reservation_id=(state.request.reservation_id or "").strip(),
            guest_id=(state.request.guest_name or "").strip(),
            message_text=state.cleaned_message or "",
            message_language=state.response_language or "",
        )
        try:
            decision: Decision = await self._orchestrator.decide(ctx)
        except Exception:
            logger.warning(
                "ExecutionOrchestrator.decide failed",
                exc_info=True,
            )
            return

        state.orchestrator_decision = decision
        logger.debug(
            "Orchestrator decision: tier=%s action=%s mode=%s scenario=%s",
            decision.tier,
            decision.action,
            decision.mode,
            scenario,
        )

    async def _run_foundation_analysis(
        self,
        state: PipelineState,
    ) -> None:
        """Push the guest message through the FL-16 Foundation pipeline.

        Sprint 6 W1 — converts the inbound :class:`PipelineState` into
        an :class:`AnalysisEvent` and hands it to the injected
        :class:`FoundationAnalysisOrchestrator`.  The resulting
        :class:`AnalysisResult` (foundation match, memory routes,
        provenance trail, guardrail block flag) lands on
        ``state.foundation_analysis`` so downstream stages (the W3
        guardrail consumer, the future W11 memory router, the
        decision-case logger) can read it without re-running the
        match.

        Four guards keep the default deploy untouched:

        1. ``BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED`` env flag — must
           be truthy for the step to engage; default off.
        2. The orchestrator must be injected at lifespan.  ``None``
           keeps the step a no-op.
        3. An empty / whitespace-only ``cleaned_message`` skips the
           call: the matcher needs text and the rest of the pipeline
           already short-circuits empty turns earlier in
           :meth:`_run_pipeline`.
        4. Any exception from the orchestrator is swallowed (logged
           at warning) so a flaky matcher / catalog never breaks the
           guest-facing reply.

        The orchestrator is duck-typed — declared :class:`Any` on the
        constructor — so this module never imports the analysis
        package's runtime dependencies (``fastembed``, ``asyncpg``)
        for tests that only exercise the conversation surface.  The
        production app factory instantiates the orchestrator with the
        wired :class:`ScenarioMatcher` + :class:`FoundationCatalogStore`
        and hands it through.
        """
        if not _foundation_orchestrator_enabled():
            return
        orchestrator = self._foundation_orchestrator
        if orchestrator is None:
            return
        message_text = (state.cleaned_message or "").strip()
        if not message_text:
            return

        request = state.request
        property_id = (request.property_id or "").strip()
        reservation_id = (request.reservation_id or "").strip() or None
        guest_id = (request.guest_name or "").strip() or None
        # FL-16 Q5-C — populate calendar_snapshot from the
        # ``reservation_context`` the UI shipped so the
        # orchestrator's ``_detect_stage_contradiction`` step
        # can compare the calendar-implied stage against the
        # matched scenario's stage.  Empty dict when the field
        # is missing — Q5-C silently no-ops in that case.
        reservation_context = getattr(request, "reservation_context", None)
        calendar_snapshot: dict[str, str] = {}
        if reservation_context is not None:
            ctx_check_in = getattr(reservation_context, "check_in", "") or ""
            ctx_check_out = getattr(reservation_context, "check_out", "") or ""
            ctx_current_time = (
                getattr(reservation_context, "current_time", "") or ""
            )
            if ctx_check_in:
                calendar_snapshot["check_in"] = str(ctx_check_in)
            if ctx_check_out:
                calendar_snapshot["check_out"] = str(ctx_check_out)
            if ctx_current_time:
                calendar_snapshot["current_time"] = str(ctx_current_time)
        event = AnalysisEvent(
            event_id=str(uuid.uuid4()),
            event_type=AnalysisEventType.MESSAGE,
            property_id=property_id,
            occurred_at=datetime.now(UTC),
            text=message_text,
            payload={"customer_id": request.customer_id},
            reservation_id=reservation_id,
            guest_id=guest_id,
            calendar_snapshot=calendar_snapshot,
        )

        try:
            result = await orchestrator.analyze(event)
        except Exception:
            logger.warning(
                "foundation_orchestrator.analyze_failed",
                exc_info=True,
            )
            return

        state.foundation_analysis = result
        match = getattr(result, "foundation_match", None)
        dominant = getattr(match, "dominant_scenario_id", None)
        logger.info(
            "Foundation analysis completed: "
            "property_id=%s dominant=%s match_size=%d "
            "guardrail_block=%s routes=%d",
            property_id,
            dominant or "<none>",
            len(getattr(match, "candidates", ()) or ()),
            getattr(result, "guardrail_block", False),
            len(getattr(result, "memory_routes", ()) or ()),
        )

    async def _apply_foundation_guardrail(
        self,
        state: PipelineState,
    ) -> None:
        """Force PM approval when the FL-01 catalog forbids auto-reply.

        Sprint 6 W3 — consults the optional
        :pyattr:`_foundation_guardrail_resolver` to decide whether
        the matched foundation scenario carries
        ``Should AI Auto-Reply: No``.  When the resolver says yes,
        the pipeline mirrors the ``approval`` path of
        :meth:`_enforce_orchestrator_verdict`: the LLM still drafts
        a reply but ``send_status`` flips to ``False`` and
        ``is_need_attention`` flips to ``True`` so downstream
        adapters (AG-UI, PM panel) know not to auto-send.

        Three guards keep the default deploy untouched:

        1. ``BRAIN_FOUNDATION_GUARDRAIL_ENABLED`` env flag — must
           be truthy for the guardrail to engage; default off.
        2. The resolver callable must be injected at lifespan.
           ``None`` keeps the step a no-op.
        3. Resolver errors are swallowed (logged at warning) so a
           flaky catalog never breaks the guest-facing reply.

        The resolver is duck-typed; the production wiring builds a
        closure over ``ScenarioMatcher`` + ``FoundationCatalogStore``
        in the app factory.  Tests inject a hand-built callable
        without pulling either dependency into the test surface.
        """
        if not _foundation_guardrail_enabled():
            return
        resolver = self._foundation_guardrail_resolver
        if resolver is None:
            return
        message_text = (state.cleaned_message or "").strip()
        if not message_text:
            return
        property_id = (state.request.property_id or "").strip()
        try:
            should_block = await _maybe_await(
                resolver(message_text, property_id),
            )
        except Exception:
            logger.warning(
                "foundation_guardrail.resolver_failed",
                exc_info=True,
            )
            return
        if not should_block:
            return
        state.requires_pm_approval = True
        state.response_flags.is_need_attention = True
        state.response_flags.send_status = False
        logger.info(
            "Foundation guardrail forced approval mode: "
            "property_id=%s message_head=%r",
            property_id,
            message_text[:60],
        )

    def _validate_agent_response(self, state: PipelineState) -> None:
        """Run the GuardrailPipeline against the LLM-drafted reply.

        Mirrors what ``cendra_adapter._validate_guest_response`` does
        on the Cendra path so the AG-UI / Sandbox path benefits from
        the same Tier-1 (Format, Lexical), Tier-2 (Repeat,
        RepeatQuestion, Contradiction) and Tier-3 (Hallucination)
        checks.  Pre-R3 the AG-UI path streamed the raw LLM output
        verbatim, which let through the leaks captured on 2026-05-18
        (WiFi password in Inquiry; fake "dispatched repair team").

        Three guards keep the default deploy untouched:

        1. ``BRAIN_RESPONSE_VALIDATION_ENABLED`` env flag — must be
           truthy for validation to engage; default off.
        2. ``self._guardrail_pipeline`` must be injected.  ``None``
           keeps the step a no-op so deployments that have not yet
           wired the pipeline see no behavioural change.
        3. Empty ``state.agent_response`` is a no-op (the model did
           not draft a reply this turn; postprocessing handles that
           case separately).

        Effects on the pipeline state:

        * Replaces ``state.agent_response`` with the pipeline's
          ``cleaned_response`` when the checks rewrote it (e.g.
          Lexical scrubbed a forbidden token, Format trimmed a
          stray fence).  When clean text is byte-identical the
          assignment is a no-op.
        * Flips ``state.response_flags.is_need_attention = True``
          when validation did not pass, so downstream adapters
          (PM panel, telemetry) route the reply for manual review
          even if the cleaned text is sent.
        * Drops the failure summaries onto a fresh
          ``state.response_validation_failures`` list so
          ``_build_response`` and observability sinks can surface
          which checks fired.

        The pipeline call itself swallows internal exceptions and
        records them as failures; an unexpected library-level error
        is logged and silently ignored so a broken check never
        kills the guest-facing reply.
        """
        if not _response_validation_enabled():
            return
        pipeline = self._guardrail_pipeline
        if pipeline is None:
            return
        response_text = state.agent_response or ""
        if not response_text.strip():
            return
        context: dict[str, Any] = {
            "property_id": state.request.property_id or "",
            "customer_id": state.request.customer_id or "",
        }
        try:
            result = pipeline.validate_response(
                response_text,
                context=context,
                knowledge_base=state.property_knowledge or "",
            )
        except Exception:
            logger.warning(
                "response_validation.pipeline_failed",
                exc_info=True,
            )
            return
        cleaned = getattr(result, "cleaned_response", response_text) or ""
        if cleaned and cleaned != response_text:
            state.agent_response = cleaned
        if not getattr(result, "passed", True):
            state.response_flags.is_need_attention = True
            state.response_validation_failures = [
                {
                    "check": str(f.get("check", "")),
                    "message": str(f.get("message", "")),
                    "severity": str(f.get("severity", "")),
                }
                for f in getattr(result, "failures", []) or []
            ]
            logger.info(
                "response_validation.blocked",
                extra={
                    "property_id": context["property_id"],
                    "failures": len(state.response_validation_failures),
                },
            )

    @staticmethod
    def _enforce_orchestrator_verdict(state: PipelineState) -> bool:
        """Apply the §10 verdict to the pipeline and report whether to
        skip the LLM agent.

        Branch 4 turns the orchestrator from "advisory annotation" into
        a runtime gate.  The contract per ``mode``:

        * ``"block"`` — the chain says the action is forbidden (live
          blocker, hard staticity rule, manual deny).  The runtime
          must NOT let the LLM improvise a refusal because an LLM
          refusal can still leak unsafe context.  The pipeline emits
          a deterministic deny, marks ``response_flags.is_need_attention``
          so the PM panel surfaces the case, and short-circuits — the
          method returns ``True`` so the caller skips
          :meth:`_run_agent`.
        * ``"approval"`` — the chain allows the action but requires
          PM sign-off (refunds above threshold, owner override
          territory).  The LLM is still asked to draft the reply so
          the PM has a starting point, but
          :attr:`PipelineState.requires_pm_approval` is set and
          ``is_need_attention`` flips to true so the AG-UI adapter
          and the PM panel both know the draft must not auto-send.
        * ``"auto"`` and ``"ask"`` — no gating; the LLM proceeds
          normally.  The verdict is still attached to the
          DecisionCase so pattern miners can attribute the outcome.
        * ``None`` (orchestrator disabled) — no-op, return ``False``.

        The deterministic deny copy is intentionally short and
        language-agnostic.  Localised post-processing (the existing
        tone / translation step) wraps it in the guest's language so
        Brain Engine never silently pushes English at a Turkish guest.

        Args:
            state: Pipeline state populated by
                :meth:`_consult_orchestrator`.

        Returns:
            ``True`` when the LLM agent run must be skipped; ``False``
            otherwise.
        """
        decision = state.orchestrator_decision
        if decision is None:
            return False

        mode = getattr(decision, "mode", "")
        if mode == "block":
            state.orchestrator_blocked = True
            state.response_flags.is_need_attention = True
            state.response_flags.send_status = False
            state.response_flags.completeness = "none"
            # Keep the agent_response empty so postprocessing emits
            # the deterministic deny instead of an LLM hallucination.
            state.agent_response = (
                "I cannot confirm this request. A property manager will "
                "follow up shortly."
            )
            logger.info(
                "Orchestrator blocked turn: tier=%s action=%s",
                getattr(decision, "tier", ""),
                getattr(decision, "action", ""),
            )
            return True

        if mode == "approval":
            state.requires_pm_approval = True
            state.response_flags.is_need_attention = True
            # Approval mode = LLM still drafts, but the message must
            # not auto-send to the guest until a human signs off.
            # ``send_status`` is the documented Cendra signal carrying
            # that intent to downstream channel adapters.
            state.response_flags.send_status = False
            logger.info(
                "Orchestrator requires PM approval: tier=%s action=%s",
                getattr(decision, "tier", ""),
                getattr(decision, "action", ""),
            )
            return False

        return False

    @staticmethod
    def _verdict_payload(state: PipelineState) -> dict[str, Any]:
        """Serialise :attr:`PipelineState.orchestrator_decision` for storage.

        Returns an empty mapping when the orchestrator was not
        consulted (legacy / disabled paths).  The schema mirrors the
        :class:`brain_engine.orchestrator.decision.Decision` value
        object so the JSONB row is self-describing without requiring
        a join back to the runtime types.

        Args:
            state: Pipeline state with the optional decision attached.

        Returns:
            A JSON-safe mapping with ``tier``, ``action``, ``mode``,
            ``rationale`` and ``params`` keys, or ``{}`` when no
            verdict is available.
        """
        decision = state.orchestrator_decision
        if decision is None:
            return {}
        params = getattr(decision, "params", None) or {}
        return {
            "tier": getattr(decision, "tier", ""),
            "action": getattr(decision, "action", ""),
            "mode": getattr(decision, "mode", ""),
            "rationale": getattr(decision, "rationale", ""),
            "params": dict(params),
        }

    @staticmethod
    def _derive_scenario(state: PipelineState) -> str:
        """Pick the orchestrator scenario for the current turn.

        Priority order:

        1. The :class:`PatternRule` matched in
           :meth:`_consult_pattern_rules` — that path already runs the
           full :class:`DecisionClassifier`, so reusing its scenario
           keeps the two consults aligned.
        2. A small heuristic over the live :class:`BusinessFlags` —
           handles turns where the pattern path bailed early (no
           reservation_id, no PMS fetcher).  The mapping intentionally
           mirrors the resolvers' default scenario vocabulary so a
           heuristic-derived scenario can still hit a configured
           preference / safety / blocker rule.

        Returns:
            Scenario string, or ``""`` when nothing classifies cleanly.
        """
        rule = state.matched_rule
        if rule is not None:
            scenario_attr = getattr(rule, "scenario", None)
            scenario_value = getattr(scenario_attr, "value", scenario_attr)
            if isinstance(scenario_value, str) and scenario_value:
                return scenario_value

        flags = state.business_flags
        if flags.is_discount_request:
            return "discount_request"
        if flags.is_complaint or flags.is_cleaning_issue:
            return "complaint_compensation"
        if flags.is_maintenance_issue:
            return "vendor_dispatch"
        return ""

    async def _resolve_owner_id(self, state: PipelineState) -> str:
        """Resolve the Cendra ``ownerId`` for the orchestrator context.

        Falls back to ``customer_id`` when the property profile does
        not yet carry an explicit ``owner_id`` — the V1 mapping per
        ``project_auth_boundary_cendra_vs_brain_engine.md``.  Returns
        an empty string when neither is available so the orchestrator
        consult bails out instead of routing on a fabricated owner.

        Args:
            state: Pipeline state with the original request.

        Returns:
            Resolved owner id, or ``""`` when none can be derived.
        """
        property_id = (state.request.property_id or "").strip()
        if self._profile_store is not None and property_id:
            try:
                profile = await self._profile_store.get(property_id)
            except Exception:
                profile = None
                logger.debug(
                    "PropertyProfile lookup failed during orchestrator "
                    "owner-id resolution",
                    exc_info=True,
                )
            if profile is not None and getattr(profile, "owner_id", ""):
                return str(profile.owner_id).strip()

        customer_id = (state.request.customer_id or "").strip()
        return customer_id

    async def _load_customer_context(self, state: PipelineState) -> None:
        """Load customer-level context into pipeline state.

        Fetches cross-property history for the current customer and
        stores it in ``state.customer_context`` for prompt injection.

        Args:
            state: Pipeline state with request data.
        """
        if self._customer_memory is None:
            return

        customer_id = getattr(state.request, "customer_id", "") or ""
        if not customer_id:
            return

        try:
            property_id = state.request.property_id or ""
            context = await self._customer_memory.build_customer_context(
                customer_id,
                current_property_id=property_id,
            )
            if context:
                state.customer_context = context
                logger.debug(
                    "Customer context loaded: %d chars, customer=%s",
                    len(context),
                    customer_id[:8],
                )
        except Exception:
            logger.warning(
                "Failed to load customer context",
                exc_info=True,
            )

    async def _log_decision_case(self, state: PipelineState) -> None:
        """Log one or more DecisionCases after the pipeline completes.

        Captures the full operational context (message, response,
        tools used) for pattern learning.  Per ali.md §3 a single
        thread can carry multiple operational decisions (the
        canonical example fans out to amenity_exception +
        guest_count_mismatch + access_code_release in one PM
        exchange); :meth:`DecisionClassifier.classify_all` fans
        those out into one classification per scenario, and we
        persist a separate :class:`DecisionCase` for each so the
        pattern miner sees every learning signal.  Single-scenario
        threads still log exactly one case, preserving the pre-P7
        behaviour.

        Silently skips when ``case_store`` is not configured.

        Args:
            state: Fully processed pipeline state.
        """
        if self._case_store is None:
            return

        try:
            property_id = state.request.property_id or ""
            owner_id = getattr(state.request, "owner_id", "")
            reservation_id = getattr(state.request, "reservation_id", None)
            # ``ConversationRequest`` carries no ``guest_id`` field, so the
            # legacy ``getattr`` always yielded ``None`` and every persisted
            # case was guest-anonymous — which is why memory recall could
            # only scope by ``property_id`` and leaked one guest's facts
            # (e.g. a WhatsApp number) into another guest's reply.  The
            # conversation thread id is the stable per-guest key (it also
            # keys the Redis history ``conv:{property_id}:{guest_id}``), so
            # use it as the guest identifier; fall back to any explicit
            # ``guest_id`` attribute for non-sandbox callers.
            guest_id = (
                (getattr(state.request, "conversation_id", "") or "").strip()
                or getattr(state.request, "guest_id", None)
            )

            # Surface ReservationContext date fields to the classifier
            # so the stage prior is derived from "when was the question
            # asked" instead of brittle keyword matching.  Missing
            # fields preserve the legacy keyword behaviour.
            reservation_context = getattr(
                state.request,
                "reservation_context",
                None,
            )
            ctx_check_in = getattr(reservation_context, "check_in", "") or ""
            ctx_check_out = getattr(reservation_context, "check_out", "") or ""
            ctx_current_time = (
                getattr(reservation_context, "current_time", "") or ""
            )

            classifications = self._decision_classifier.classify_all(
                business_flags=state.business_flags,
                message_text=state.cleaned_message or "",
                response_text=state.agent_response or "",
                tools_used=tuple(state.tools_used),
                reservation_id=reservation_id or None,
                check_in=ctx_check_in,
                check_out=ctx_check_out,
                current_time=ctx_current_time,
            )

            # Sprint 9 forward-path — fetch ``reservation.data.createdAt``
            # via the unified GraphQL gateway so ``lead_time_hours``
            # lands on the new case.  Pre-Sprint-9 path stays
            # bit-for-bit identical when ``self._reservation_prefetcher``
            # is ``None`` (bootstrap leaves it unset unless
            # ``BRAIN_LEAD_TIME_FETCH_ENABLED`` is truthy).  Soft-fails
            # to ``None`` on any GraphQL error so a transient upstream
            # blip cannot turn off live conversation logging.
            pms_data: dict[str, Any] | None = None
            if (
                self._reservation_prefetcher is not None
                and property_id
                and reservation_id
            ):
                try:
                    pms_data = (
                        await self._reservation_prefetcher.fetch_pms_payload(
                            property_id=property_id,
                            reservation_id=str(reservation_id),
                        )
                    )
                except Exception:
                    logger.warning(
                        "reservation_prefetch_unexpected_error",
                        exc_info=True,
                        property_id=property_id,
                        reservation_id=reservation_id,
                    )
                    pms_data = None

            # Sandbox / no-PMS fallback: when the prefetch missed (typical
            # of sandbox UI which mints fake UUIDs that never land in
            # Elasticsearch), populate ``pms_data`` from whatever the
            # client shipped in ``state.request.reservation_context`` so
            # the resulting DecisionCase carries a non-empty
            # ``pms_snapshot`` and shows up correctly in /patterns/cases
            # debugging.  Live traffic with a real reservation is
            # unaffected — the prefetched payload wins, the fallback
            # branch is skipped.
            if not pms_data:
                fallback_features = _reservation_context_to_feature_dict(
                    reservation_context,
                )
                if fallback_features:
                    pms_data = fallback_features

            primary_case_id: str | None = None
            for classification in classifications:
                # Mümin round-4 follow-up: live cases must carry a
                # synthesised :class:`CaseOutcome` so
                # :attr:`DecisionCase.has_outcome` is True and the
                # PatternExtractor admits them into mining.  The
                # derivation is shared with the historical extractor
                # via :meth:`CaseOutcome.from_decision_type`, so the
                # live and bootstrap paths produce identical outcome
                # shapes for the same ``decision_type``.
                outcome = CaseOutcome.from_decision_type(
                    classification.decision_type,
                )
                # Sprint 6 W1 follow-up — bridge the FL-16 analysis
                # output onto the logged case so PatternMiner (W5)
                # can propagate ``foundation_scenario_id`` into the
                # resulting :class:`PatternRule`'s origin trail.
                # ``None`` when the orchestrator is unwired or
                # produced no dominant match — the pre-W1 path stays
                # bit-for-bit identical.
                foundation_analysis = getattr(
                    state,
                    "foundation_analysis",
                    None,
                )
                foundation_match = getattr(
                    foundation_analysis,
                    "foundation_match",
                    None,
                )
                dominant_scenario_id = getattr(
                    foundation_match,
                    "dominant_scenario_id",
                    None,
                )
                # Mümin 2026-05-15 round-5 #3 — propagate the
                # orchestrator's PatternOrigin (which already carries
                # ``source_event_ids=(event.event_id,)`` per
                # ``FoundationAnalysisOrchestrator._log_origin``) onto
                # the persisted DecisionCase so the rule miner can
                # aggregate it into ``PatternRule.origin`` and the
                # FL-12 ``/rules/{id}/origin`` endpoint stops
                # returning an empty ``source_event_ids`` list.
                foundation_origin = getattr(
                    foundation_analysis,
                    "origin",
                    None,
                )
                case = await self._case_builder.build(
                    message_text=state.cleaned_message or "",
                    response_text=state.agent_response or "",
                    property_id=property_id,
                    owner_id=owner_id,
                    stage=classification.stage,
                    scenario=classification.scenario,
                    decision_type=classification.decision_type,
                    reservation_id=reservation_id,
                    guest_id=guest_id,
                    pms_data=pms_data,
                    executed_actions=tuple(state.tools_used),
                    orchestrator_verdict=self._verdict_payload(state),
                    outcome=outcome,
                    foundation_scenario_id=dominant_scenario_id,
                    origin=foundation_origin,
                )
                await self._case_store.store(case)
                await self._memory_fanout.record_case(
                    case,
                    source="live",
                )
                if primary_case_id is None:
                    primary_case_id = case.case_id
                logger.debug(
                    "DecisionCase logged: %s scenario=%s (tools=%s)",
                    case.case_id[:8],
                    classification.scenario.value,
                    state.tools_used,
                )

            # Customer memory carries the primary case_id for
            # backward compatibility; sibling cases are still
            # recoverable from the case store via reservation_id.
            if primary_case_id is not None:
                await self._record_customer_event(state, primary_case_id)
        except Exception:
            logger.warning("Failed to log DecisionCase", exc_info=True)

    async def _record_customer_event(
        self,
        state: PipelineState,
        case_id: str,
    ) -> None:
        """Record a conversation event in customer memory.

        Captures the interaction as a CustomerEvent so the PM's full
        operational history is tracked across all properties.

        Args:
            state: Pipeline state with request and response data.
            case_id: ID of the DecisionCase that was logged.
        """
        if self._customer_memory is None:
            return

        customer_id = getattr(state.request, "customer_id", "") or ""
        if not customer_id:
            return

        try:
            tools_str = (
                ", ".join(state.tools_used) if state.tools_used else "none"
            )
            summary = (
                f"Guest message processed ({tools_str})"
                if state.agent_response
                else "Guest message — no response generated"
            )

            await self._customer_memory.record_event(
                customer_id=customer_id,
                workspace_id=getattr(state.request, "workspace_id", ""),
                property_id=state.request.property_id or "",
                event_type="conversation",
                summary=summary,
                details={
                    "case_id": case_id,
                    "tools_used": state.tools_used,
                    "confidence": state.classification_confidence,
                    "language": state.response_language,
                },
                guest_name=getattr(state.request, "guest_name", ""),
                reservation_id=getattr(state.request, "reservation_id", ""),
            )
        except Exception:
            logger.debug("Customer event recording failed", exc_info=True)

    async def _check_tool_blockers(
        self,
        tool_call: Any,
        state: PipelineState,
    ) -> str | None:
        """Check if a tool call is blocked by active blockers.

        Maps tool names to ActionTypes and queries the BlockerEngine.
        Returns a human-readable block message, or None if not blocked.

        Args:
            tool_call: The tool call from LLM.
            state: Current pipeline state.

        Returns:
            Block message string if blocked, None otherwise.
        """
        if self._blocker_engine is None:
            return None

        from brain_engine.approval.models import ActionType

        # Map tool names to ActionTypes for blocker checking
        tool_action_map: dict[str, ActionType] = {
            "send_access_code": ActionType.SEND_ACCESS_CODE,
            "send_door_code": ActionType.SEND_ACCESS_CODE,
            "charge_guest": ActionType.CHARGE_GUEST,
            "submit_damage_claim": ActionType.SUBMIT_DAMAGE_CLAIM,
            "late_checkout": ActionType.LATE_CHECKOUT,
            "offer_discount": ActionType.OFFER_DISCOUNT,
        }

        tool_name = tool_call.function.name
        action_type = tool_action_map.get(tool_name)
        if action_type is None:
            return None

        property_id = state.request.property_id or ""
        reservation_id = getattr(state.request, "reservation_id", None)

        try:
            blockers = await self._blocker_engine.check_blockers(
                property_id=property_id,
                reservation_id=reservation_id,
                action_type=action_type,
            )
            hard_blockers = [b for b in blockers if b.is_hard]
            if hard_blockers:
                descriptions = "; ".join(b.description for b in hard_blockers)
                logger.warning(
                    "Tool %s blocked: %s",
                    tool_name,
                    descriptions,
                )
                return (
                    f"Action blocked: {descriptions}. "
                    "Please resolve the blocker(s) before proceeding."
                )
        except Exception:
            logger.warning(
                "Blocker check failed for tool %s",
                tool_name,
                exc_info=True,
            )

        return None

    def _get_enabled_tools(
        self,
        settings: CustomerSettings,
        intent_result: IntentResult | None = None,
    ) -> list[Any]:
        """Получить инструменты, доступные для данного запроса.

        Применяет два уровня фильтрации:
        1. Customer toggles — клиент может отключить инструменты.
        2. Intent domain — intent определяет релевантную подгруппу.

        При UNKNOWN intent или confidence < порога — все инструменты доступны.

        Args:
            settings: Настройки клиента с переключателями инструментов.
            intent_result: Результат intent-классификации.

        Returns:
            Список функций доступных инструментов.
        """
        from brain_engine.conversation_tools import ALL_TOOLS, TOOL_TOGGLE_MAP

        # Определяем допустимые по intent имена инструментов
        intent_tools = _resolve_intent_tools(intent_result)

        enabled: list[Any] = []
        for tool_func in ALL_TOOLS:
            name = _get_tool_name(tool_func)

            # Фильтр 1: customer toggle
            toggle_field = TOOL_TOGGLE_MAP.get(name, "")
            if not settings.is_tool_enabled(toggle_field):
                continue

            # Фильтр 2: intent domain (None = все разрешены)
            if intent_tools is not None and name not in intent_tools:
                continue

            enabled.append(tool_func)

        if intent_tools is not None:
            logger.info(
                "Tool filtering: intent=%s → %d/%d tools enabled",
                intent_result.intent.value if intent_result else "none",
                len(enabled),
                len(ALL_TOOLS),
            )

        return enabled

    def _build_response(self, state: PipelineState) -> ConversationResponse:
        """Assemble final API response from pipeline state.

        Args:
            state: Fully processed pipeline state.

        Returns:
            ConversationResponse for the API.
        """
        elapsed = int((time.monotonic() - state.started_at) * 1000)

        return ConversationResponse(
            status=True,
            message=state.agent_response,
            business_flags=state.business_flags,
            response_language=state.response_language,
            confidence=state.classification_confidence,
            response_flags=state.response_flags,
            message_tags=state.message_tags,
            rag_sources=state.rag_sources,
            tools_used=state.tools_used,
            sentiment=state.sentiment,
            tasks_created=state.tasks,
            process_time_ms=elapsed,
            model_used=_AGENT_MODEL,
            orchestrator_blocked=state.orchestrator_blocked,
            requires_pm_approval=state.requires_pm_approval,
        )


# ── Helpers ──────────────────────────────────────────────────── #


# Per-conversation dedup ledger for ``MISSING_INFO_DETECTED``.
#
# Keys are ``(conversation_id, gap_fingerprint)`` and values are the
# monotonic timestamp at which the flag was last emitted.  A 1-hour
# TTL is enough to suppress the "asks 4 times in a row" PM Chat
# spam Mümin's team reported while still re-flagging genuinely new
# turns the next morning.  In-memory by design: the SSE channel is
# ephemeral, persistence here would be cargo-culted infra.
_MISSING_INFO_DEDUP: dict[tuple[str, str], float] = {}
_MISSING_INFO_TTL_SECONDS: Final[float] = 60.0 * 60.0
_MISSING_INFO_MAX_ENTRIES: Final[int] = 4096
_GAP_FINGERPRINT_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)


def _gap_fingerprint(
    intervention_reason: str, missing_information: str
) -> str:
    """Reduce a gap to a stable, language-tolerant fingerprint.

    Lowercases, strips punctuation, collapses whitespace, and keeps the
    first 80 chars so semantically-equal gaps from two consecutive
    turns ("WiFi password", "Wi-Fi password!!") collapse to one.

    Args:
        intervention_reason: Free-form reason emitted to PM Chat.
        missing_information: Bulleted gap list from the extractor.

    Returns:
        Lowercase fingerprint string (≤80 chars), or ``""`` on empty.
    """
    raw = (intervention_reason or missing_information or "").strip()
    if not raw:
        return ""
    cleaned = _GAP_FINGERPRINT_RE.sub(" ", raw.lower())
    return " ".join(cleaned.split())[:80]


def _missing_info_already_emitted(
    *,
    conversation_id: str,
    fingerprint: str,
    now: float,
) -> bool:
    """Return True iff this gap was emitted for the same conversation
    within the TTL window.  Side effect: prunes expired keys lazily and
    bounds the ledger so a runaway test loop cannot leak unboundedly.

    Args:
        conversation_id: AG-UI conversation identifier.
        fingerprint: :func:`_gap_fingerprint` output.
        now: Monotonic clock reading from the caller.

    Returns:
        True when the same flag fired for this conversation recently.
    """
    if not conversation_id or not fingerprint:
        return False

    if len(_MISSING_INFO_DEDUP) > _MISSING_INFO_MAX_ENTRIES:
        cutoff = now - _MISSING_INFO_TTL_SECONDS
        for stale_key in [
            key
            for key, seen_at in _MISSING_INFO_DEDUP.items()
            if seen_at < cutoff
        ]:
            _MISSING_INFO_DEDUP.pop(stale_key, None)

    key = (conversation_id, fingerprint)
    seen_at = _MISSING_INFO_DEDUP.get(key)
    if seen_at is None:
        return False
    return (now - seen_at) <= _MISSING_INFO_TTL_SECONDS


# Map each ops-grade business flag to a PM-facing question.  Keeping
# this static (rather than rebuilding the prompt from a template per
# call) gives operations a single audit point for tone tuning and
# avoids the per-message LLM round-trip the missing-info extractor
# would otherwise add.
_OPS_FLAG_QUESTIONS: dict[str, tuple[str, str]] = {
    "IS_EMERGENCY": (
        "Guest reported a possible emergency (fire / flood / gas / "
        "break-in). Confirm the situation and dispatch help.",
        "ops_emergency",
    ),
    "IS_MAINTENANCE_ISSUE": (
        "Guest reports a maintenance problem at the property. "
        "Confirm the issue and dispatch repair.",
        "ops_maintenance",
    ),
    "IS_SECURITY_ISSUE": (
        "Guest reports a security concern (broken lock / suspicious "
        "person / theft). Verify and respond.",
        "ops_security",
    ),
    "IS_CLEANING_ISSUE": (
        "Guest reports a cleanliness problem at the property. "
        "Schedule a cleaning visit.",
        "ops_cleaning",
    ),
    "IS_NOISE_COMPLAINT": (
        "Guest reports a noise complaint. Decide whether to mediate "
        "with neighbours or offer compensation.",
        "ops_noise",
    ),
}

# Priority order for ops emission: only one event fires per turn so
# the PM Chat panel does not get duplicate flags for a single message
# that the classifier annotated with multiple ops flags.  Emergency
# always wins, then security, then maintenance, then cleaning, then
# noise — strictest-first so life-safety beats convenience issues.
_OPS_FLAG_PRIORITY: tuple[str, ...] = (
    "IS_EMERGENCY",
    "IS_SECURITY_ISSUE",
    "IS_MAINTENANCE_ISSUE",
    "IS_CLEANING_ISSUE",
    "IS_NOISE_COMPLAINT",
)


def _maybe_emit_ops_attention(
    *,
    business_flags: Any,
    guest_message: str,
    conversation: Any,
) -> None:
    """Emit a PM-facing flag when the guest message is ops-grade.

    Picks the highest-priority active flag from
    :data:`_OPS_FLAG_PRIORITY` and emits one
    ``MISSING_INFO_DETECTED`` event into the same SSE stream the
    PM Chat panel already consumes.  Re-uses the missing-info dedup
    ledger so a multi-turn maintenance thread does not flood the
    panel with one flag per guest message — only the first hit on a
    given ``(conversation_id, fingerprint)`` pair within the TTL
    surfaces.

    Failures are swallowed; ops detection must never break the main
    pipeline.

    Args:
        business_flags: :class:`BusinessFlags` instance from the
            classifier.  When ``None`` or absent of ops flags the
            function is a no-op.
        guest_message: Cleaned guest message — folded into the
            fingerprint so two distinct maintenance complaints in
            the same conversation each surface once.
        conversation: Conversation request — only ``conversation_id``
            is read.
    """
    try:
        if business_flags is None:
            return
        active = set(business_flags.active_flags())
        if not active:
            return

        flag = next(
            (name for name in _OPS_FLAG_PRIORITY if name in active),
            None,
        )
        if flag is None:
            return

        question, source_field = _OPS_FLAG_QUESTIONS[flag]

        conversation_id = str(
            getattr(conversation, "conversation_id", "") or "",
        )
        fingerprint = _gap_fingerprint(flag, guest_message or "")
        now = time.monotonic()
        if _missing_info_already_emitted(
            conversation_id=conversation_id,
            fingerprint=fingerprint,
            now=now,
        ):
            logger.debug(
                "ops_emit_dedup_suppressed conversation=%s flag=%s",
                conversation_id,
                flag,
            )
            return

        emit_missing_info_detected(
            question=question,
            missing_information=guest_message or flag,
            source_field=source_field,
        )
        if conversation_id and fingerprint:
            _MISSING_INFO_DEDUP[(conversation_id, fingerprint)] = now
    except Exception:
        logger.exception("ops emit failed (non-fatal)")


def _maybe_emit_stage_mismatch(
    *,
    foundation_analysis: Any,
    conversation: Any,
) -> None:
    """Emit STAGE_MISMATCH_DETECTED when Q5-C found a contradiction.

    Reads ``foundation_analysis.stage_mismatch`` set by the
    FL-16 orchestrator's ``_detect_stage_contradiction`` step
    (PR #301).  When ``True``, parses the
    ``stage_mismatch_detail`` string
    (``"calendar=<stage> scenario=<stage>"``) and emits one SSE
    event into the PM Chat stream.  Re-uses the missing-info
    dedup ledger so a guest who keeps re-asking from the same
    impossible calendar context does not flood PM Chat with one
    flag per turn — only the first hit on a given
    ``(conversation_id, mismatch_fingerprint)`` pair within the
    1-hour TTL surfaces.

    Failures are swallowed: visibility detection must never
    break the main pipeline (Q5-A / Q5-B / Q5-C themselves are
    upstream and unaffected).

    Args:
        foundation_analysis: ``AnalysisResult`` attached to the
            conversation state by ``_run_foundation_analysis``.
            ``None`` when the orchestrator is unwired or the
            event arrived before W1 — the function is a no-op
            in either case.
        conversation: Conversation request — only
            ``conversation_id`` is read for dedup keying.
    """
    try:
        if foundation_analysis is None:
            return
        mismatch = bool(
            getattr(foundation_analysis, "stage_mismatch", False),
        )
        if not mismatch:
            return
        detail = str(
            getattr(foundation_analysis, "stage_mismatch_detail", "") or "",
        )
        if not detail:
            return

        match = getattr(foundation_analysis, "foundation_match", None)
        dominant_entry = getattr(match, "dominant_catalog_entry", None)
        scenario_id = (
            str(getattr(dominant_entry, "scenario_id", "") or "")
            if dominant_entry is not None
            else str(getattr(match, "dominant_scenario_id", "") or "")
        )

        calendar_stage, scenario_stage = _parse_stage_mismatch_detail(detail)

        conversation_id = str(
            getattr(conversation, "conversation_id", "") or "",
        )
        fingerprint = _gap_fingerprint(detail, scenario_id or "")
        now = time.monotonic()
        if _missing_info_already_emitted(
            conversation_id=conversation_id,
            fingerprint=fingerprint,
            now=now,
        ):
            logger.debug(
                "stage_mismatch_emit_dedup_suppressed "
                "conversation=%s detail=%s",
                conversation_id,
                detail,
            )
            return

        emit_stage_mismatch_detected(
            detail=detail,
            scenario_id=scenario_id,
            calendar_stage=calendar_stage,
            scenario_stage=scenario_stage,
        )
        if conversation_id and fingerprint:
            _MISSING_INFO_DEDUP[(conversation_id, fingerprint)] = now
    except Exception:
        logger.exception("stage_mismatch emit failed (non-fatal)")


def _parse_stage_mismatch_detail(detail: str) -> tuple[str, str]:
    """Split ``"calendar=X scenario=Y"`` into ``(X, Y)``.

    Returns ``("", "")`` when the detail string does not match
    the expected shape — the SSE event still fires with the raw
    ``detail`` so the PM operator sees the full string.
    """
    calendar_stage = ""
    scenario_stage = ""
    for fragment in detail.split():
        if fragment.startswith("calendar="):
            calendar_stage = fragment[len("calendar=") :]
        elif fragment.startswith("scenario="):
            scenario_stage = fragment[len("scenario=") :]
    return calendar_stage, scenario_stage


def _topic_from_foundation_analysis(
    foundation_analysis: Any,
) -> str:
    """Return the matched scenario's title or ``""``.

    Reads ``foundation_analysis.foundation_match.dominant_catalog_entry.title``
    defensively — any missing layer collapses to ``""`` so the
    caller can decide whether to fall back to the LLM-extracted
    topic.  Variant B fix for the 2026-05-18 Aybüke bug where the
    extractor LLM hallucinated the topic ("pricing") on an
    early-checkin conversation.
    """
    if foundation_analysis is None:
        return ""
    match = getattr(foundation_analysis, "foundation_match", None)
    if match is None:
        return ""
    dominant_entry = getattr(match, "dominant_catalog_entry", None)
    if dominant_entry is None:
        return ""
    return str(getattr(dominant_entry, "title", "") or "")


# Transitional boilerplate patterns the LLM-fallback extractor emits
# verbatim because :mod:`brain_engine.conversation.missing_info_extractor`
# ``_SYSTEM_PROMPT`` (line 257 today) teaches them as the canonical
# template.  Captured live three times in #71 / #17 sandbox turns
# (tester reports 2026-05-19/20).  The durable fix is rewriting the
# extractor system prompt (tester proposal A1) — at that point this
# tuple becomes dead code and can be removed alongside
# :func:`_sanitize_intervention_reason`.
_INTERVENTION_BOILERPLATE_PATTERNS: Final[tuple[str, ...]] = (
    "which is not in the knowledge base",
    "which is not in our knowledge base",
    "that is not in the knowledge base",
    "that is not in our knowledge base",
)


_INTERVENTION_PREFIX_PATTERNS: Final[tuple[str, ...]] = (
    "guest needs information about",
    "guest needs",
    "the guest needs",
)


_INTERVENTION_TRAIL_PUNCT: Final[str] = ",.;: \t\n"


def _sanitize_intervention_reason(text: str) -> str:
    """Strip the extractor's English boilerplate from ``text``.

    The :mod:`missing_info_extractor` SYSTEM_PROMPT teaches the LLM
    to emit
    ``"Guest needs <topic> which is not in the knowledge base"`` as
    the canonical ``intervention_reason`` shape.  PM Chat surfaces
    the resulting string verbatim, so a Turkish-speaking PM sees a
    sentence that is half-Turkish (topic) and half-English
    (template) — tester complaints #1 and #6 (2026-05-19/20, live
    captures in #71 and #17 sandbox turns).

    This helper drops the leading ``Guest needs ...`` prefix and
    the trailing ``which is not in the knowledge base`` suffix when
    present, then collapses leftover whitespace and dangling
    punctuation.  Anything else passes through verbatim — including
    the topic content itself, in whatever language the LLM produced
    it.  When the sanitisation result is empty (the entire string
    was boilerplate), the original text is returned so PM Chat
    never sees an empty flag.

    Args:
        text: Raw ``intervention_reason`` from the extractor.

    Returns:
        Sanitised text, or the original text if sanitisation
        would otherwise collapse it to empty.
    """
    if not text:
        return text
    cleaned = text.strip()
    # Drop any trailing punctuation BEFORE the suffix check so a
    # stray full stop / exclamation mark that the LLM occasionally
    # appends ("…not in the knowledge base.") does not block the
    # match.
    cleaned = cleaned.rstrip(_INTERVENTION_TRAIL_PUNCT)
    lowered = cleaned.lower()
    for suffix in _INTERVENTION_BOILERPLATE_PATTERNS:
        if lowered.endswith(suffix):
            cleaned = cleaned[: len(cleaned) - len(suffix)]
            cleaned = cleaned.rstrip(_INTERVENTION_TRAIL_PUNCT)
            lowered = cleaned.lower()
            break
    for prefix in _INTERVENTION_PREFIX_PATTERNS:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            cleaned = cleaned.lstrip(_INTERVENTION_TRAIL_PUNCT)
            break
    # Strip a wrapping pair of single / double quotes that the
    # legacy template left around the topic literal.
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {
        "'",
        '"',
    }:
        cleaned = cleaned[1:-1]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or text.strip()


async def _maybe_emit_missing_info(
    *,
    ai_message: str,
    conversation: Any,
    foundation_analysis: Any = None,
) -> None:
    """Run the missing-info extractor and emit an SSE event when gaps exist.

    Called after LLM response generation in the streaming pipeline.
    Three layers gate the emission:

    1. :func:`response_has_deferral` skips the extractor LLM when the
       AI's latest response is a definitive answer.
    2. The extractor's tightened system prompt scopes analysis to the
       latest exchange (see ``missing_info_extractor._SYSTEM_PROMPT``)
       so a deferral 5 turns ago no longer leaks into every new turn.
    3. :func:`_missing_info_already_emitted` suppresses repeats of the
       same gap within a 1-hour TTL per ``conversation_id``.

    Topic resolution (2026-05-18 Aybüke bug fix):

    * The legacy path let the extractor LLM pick the ``<topic>`` in
      ``"Guest needs <topic> which is not in the knowledge base"``.
      That free-form choice was prone to hallucination — Aybüke
      reported the LLM picking ``"pricing"`` on an early-checkin
      thread.
    * When ``foundation_analysis`` is supplied and its dominant
      catalog entry has a non-empty ``title``, that title is the
      authoritative topic — the ScenarioMatcher already classified
      the message via embedding similarity against the 469-row
      catalog, so reusing that classification is strictly more
      accurate than asking another LLM to guess.
    * When no foundation analysis is available (orchestrator
      unwired, Q5-A similarity gate cleared the entry, blank
      title), the helper falls back to the LLM-derived
      ``intervention_reason`` — same behaviour as before this fix.

    Failures are swallowed — missing-info detection must never break
    the main pipeline.

    Args:
        ai_message: AI's latest response text.
        conversation: Conversation request — read for
            ``conversation_id`` (dedup) and ``messages`` (LLM
            history).
        foundation_analysis: Optional ``AnalysisResult`` from the
            FL-16 orchestrator.  Defaults to ``None`` — backwards
            compatible with pre-fix callers.  When provided and the
            dominant scenario has a title, that title overrides the
            LLM-guessed topic in the SSE payload.
    """
    try:
        from brain_engine.conversation.models import SenderType

        history = [
            {
                "role": "user"
                if m.sender_type == SenderType.GUEST
                else "assistant",
                "content": m.text,
            }
            for m in (conversation.messages or [])
        ]
        result = await extract_missing_information(
            MissingInfoRequest(
                ai_message=ai_message,
                messages=history,
            )
        )
        if not result.missing_information:
            return

        catalog_topic = _topic_from_foundation_analysis(
            foundation_analysis,
        )
        if catalog_topic:
            # Foundation catalog title is the canonical, English,
            # property-agnostic topic label.  Tester report
            # 2026-05-20 (third live capture in #71 / #17 turns)
            # flagged the legacy boilerplate
            # ``"Guest needs <topic> which is not in the knowledge
            # base"`` as both noisy (PM Chat reads only the topic
            # anyway) and language-mixed (the LLM-fallback path
            # injected the EN suffix onto a TR topic).  Emitting
            # the bare catalog title fixes both: clean text + no
            # template-vs-topic language drift.
            intervention_reason = catalog_topic
            source_field = "foundation_dominant_topic"
        else:
            # LLM fallback path: the extractor's SYSTEM_PROMPT
            # example at ``missing_info_extractor.py:257`` still
            # teaches the model to suffix the boilerplate, so we
            # strip it here as a transitional layer.  Long-term
            # fix is the A1 SYSTEM_PROMPT rewrite (tester proposal
            # 2026-05-19) — at that point this sanitizer becomes
            # redundant and can be deleted.
            intervention_reason = _sanitize_intervention_reason(
                result.intervention_reason or result.missing_information
            )
            source_field = "extract_missing_information"

        fingerprint = _gap_fingerprint(
            intervention_reason,
            result.missing_information,
        )
        conversation_id = str(
            getattr(conversation, "conversation_id", "") or ""
        )
        now = time.monotonic()
        if _missing_info_already_emitted(
            conversation_id=conversation_id,
            fingerprint=fingerprint,
            now=now,
        ):
            logger.debug(
                "missing_info_dedup_suppressed conversation=%s gap=%s",
                conversation_id,
                fingerprint,
            )
            return

        # PM Chat surfaces ``question`` verbatim.  The extractor now
        # returns a full, guest-language sentence in ``pm_question``
        # so the PM reads "The guest is asking whether early check-in
        # is possible …" instead of the bare two-word topic (tester
        # 2026-06-10: bare ``intervention_reason`` escalations read as
        # noise).  Fall back to the bare topic only when the LLM omits
        # the sentence — dedup and ``source_field`` stay keyed on the
        # topic so the anti-hallucination override (catalog) and the
        # multi-turn dedup ledger are untouched.
        pm_question = (result.pm_question or "").strip() or intervention_reason
        emit_missing_info_detected(
            question=pm_question,
            missing_information=result.missing_information,
            source_field=source_field,
        )
        if conversation_id and fingerprint:
            _MISSING_INFO_DEDUP[(conversation_id, fingerprint)] = now
    except Exception:
        logger.exception("missing-info emit failed (non-fatal)")


async def _emit_learning_decision_for_fact(
    *,
    fact: Any,
    surprise: Any,
    decision: str,
) -> None:
    """Emit LEARNING_DECISION for a single fact-extraction outcome.

    Called per-fact during in-band Mem0 extraction. Non-fatal —
    any error inside is logged and swallowed.
    """
    try:
        emit_learning_decision(
            surprise_score=float(getattr(surprise, "raw_score", 0.0)),
            should_memorize=bool(getattr(surprise, "should_memorize", False)),
            memory_strength=float(getattr(surprise, "memory_strength", 0.0)),
            fact_type=str(getattr(fact, "fact_type", "info")),
            decision=decision,
        )
    except Exception:
        logger.exception("learning-decision emit failed (non-fatal)")


def _resolve_intent_tools(
    intent_result: IntentResult | None,
) -> frozenset[str] | None:
    """Определить допустимые инструменты на основе intent-классификации.

    Возвращает None (все инструменты разрешены) если:
    - intent_result не задан
    - intent = UNKNOWN
    - confidence ниже порога

    Args:
        intent_result: Результат intent-классификации.

    Returns:
        Множество имён допустимых инструментов, или None для полного набора.
    """
    if intent_result is None:
        return None

    if intent_result.intent == Intent.UNKNOWN:
        return None

    if intent_result.confidence < _INTENT_CONFIDENCE_THRESHOLD:
        logger.debug(
            "Intent confidence %.2f < %.2f threshold, skipping tool filter",
            intent_result.confidence,
            _INTENT_CONFIDENCE_THRESHOLD,
        )
        return None

    return get_tools_for_intent(intent_result.intent)


def _build_agent_messages(
    state: PipelineState,
) -> list[dict[str, Any]]:
    """Build LLM messages list for the agent.

    Args:
        state: Pipeline state with system prompt and history.

    Returns:
        List of message dicts for litellm.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": state.system_prompt},
    ]

    # Add conversation history (max last 10 messages)
    for msg in state.request.history_for_llm[-10:]:
        messages.append(msg)

    return messages


def _build_tool_definitions(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert tool functions to OpenAI tool definition format.

    The @tool decorator stores its schema as a flat
    ``{"name", "description", "parameters"}`` dict on ``tool_def``
    (asserted by tests). OpenAI's tools API expects that dict nested
    under a ``{"type": "function", "function": {...}}`` envelope, so
    we wrap each here before handing off to ``litellm.acompletion``.
    Without the wrapper OpenAI returns 400 ``Missing required
    parameter: 'tools[0].type'`` and the agent fails with no output.

    Args:
        tools: List of @tool-decorated functions.

    Returns:
        List of tool definition dicts in OpenAI ``type=function`` shape.
    """
    defs: list[dict[str, Any]] = []
    for t in tools:
        if hasattr(t, "tool_def"):
            td = t.tool_def
            if (
                isinstance(td, dict)
                and td.get("type") == "function"
                and "function" in td
            ):
                defs.append(td)
            else:
                defs.append({"type": "function", "function": td})
    return defs


def _get_tool_name(tool_func: Any) -> str:
    """Get the name of a tool function.

    Args:
        tool_func: Tool function (may have .name or __name__).

    Returns:
        Tool name string.
    """
    return getattr(tool_func, "name", getattr(tool_func, "__name__", ""))


async def _call_tool(
    tool_map: dict[str, Any],
    tool_call: Any,
    state: PipelineState,
) -> str:
    """Execute a single tool call.

    Args:
        tool_map: Map of tool name -> function.
        tool_call: The tool call object from LLM.
        state: Pipeline state for runtime injection.

    Returns:
        Tool result as string.
    """
    import json as _json

    name = tool_call.function.name
    func = tool_map.get(name)

    if not func:
        return f"Tool '{name}' not found."

    try:
        args = _json.loads(tool_call.function.arguments)
    except _json.JSONDecodeError:
        args = {}

    # Inject runtime — surface the GraphQL-sourced reservation snapshot
    # and availability window so tools can answer from authoritative
    # data instead of falling back to PMS adapters or mockups.
    from brain_engine.tools.runtime import ToolRuntime

    request = state.request
    calendar_state = [
        day.model_dump() for day in (request.availability_calendar or [])
    ]
    reservation_state = (
        request.reservation_context.model_dump()
        if request.reservation_context is not None
        else None
    )
    runtime = ToolRuntime(
        state={
            "messages": request.history_for_llm,
            "availability_calendar": calendar_state,
            "reservation_context": reservation_state,
        },
        config={
            "property_id": request.property_id,
            "reservation_id": request.reservation_id,
            "customer_id": request.customer_id,
            "org_id": request.org_id,
        },
    )
    args["runtime"] = runtime

    try:
        import inspect

        if inspect.iscoroutinefunction(func):
            result = await func(**args)
        else:
            result = func(**args)
        return str(result)
    except Exception as exc:
        logger.error("Tool %s failed: %s", name, exc, exc_info=True)
        return f"Tool error: {exc}"


# ── PatternRule prompt injection ─────────────────────────────── #


def _parse_iso_timestamp(raw: str) -> datetime | None:
    """Best-effort parse of an ISO-8601 timestamp into aware UTC.

    Used by ``_consult_pattern_rules`` to derive the ``as_of`` anchor
    from the UI-supplied "Message Sent Date" so the
    :class:`PatternRuleRouter` matches against the rule that was
    valid at the moment the guest spoke.  Returns ``None`` when the
    input is empty or unparseable, in which case the router falls
    back to its legacy "active rules only" path — preserving live
    behaviour for traffic that does not carry a timestamp.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _reservation_context_to_feature_dict(
    ctx: ReservationContext | None,
) -> dict[str, Any]:
    """Project a :class:`ReservationContext` into the feature dict shape.

    Used as the sandbox / no-PMS fallback in
    :meth:`ConversationService._consult_pattern_rules` when the unified
    GraphQL lookup finds nothing for the supplied ``reservation_id``.
    Mirrors the snake-case keys produced by
    :func:`brain_engine.integrations.unified_data.pms_fetcher.to_feature_dict`
    so :class:`FeatureBuilder` consumes either source interchangeably.

    Returns an empty dict when the context is unusable (``None`` or
    missing the ``check_in`` anchor); callers must treat the empty
    return as "skip rule consult" — building features with no anchor
    would produce bogus zeroes that contaminate matching.

    The float coercion on ``total_price`` tolerates the wire-form
    string the UI ships (``ReservationContext.total_price`` is typed
    ``str`` to round-trip empty values cleanly).
    """
    if ctx is None or not (ctx.check_in or ""):
        return {}
    try:
        total_price = float(ctx.total_price) if ctx.total_price else 0.0
    except (TypeError, ValueError):
        total_price = 0.0
    num_guests = int(ctx.num_guests or 0)
    num_children = int(ctx.num_children or 0)
    adults = max(num_guests - num_children, 0)
    if adults == 0 and num_guests > 0:
        adults = num_guests
    return {
        "check_in": (ctx.check_in or "")[:10],
        "check_out": (ctx.check_out or "")[:10],
        "adults": adults,
        "children": num_children,
        "total_price": total_price,
        "currency": ctx.currency or "",
        "status": ctx.status or "",
        "source": (ctx.booking_channel or "manual").lower(),
        "guest_name": ctx.guest_name or "",
    }


def _format_matched_rule(rule: PatternRule | None) -> str:
    """Render a matched :class:`PatternRule` as a prompt-ready block.

    Returns an empty string when ``rule`` is ``None`` so the caller can
    keep the prompt byte-identical for rule-free turns.  The block
    nudges the LLM toward the learned action without commanding it —
    hard enforcement is the approval gateway's job, not the prompt's.

    Args:
        rule: The matched rule, or ``None``.

    Returns:
        Formatted block (starts with ``[LEARNED PATTERN]``) or ``""``.
    """
    if rule is None:
        return ""
    action_value = rule.action.action_type.value
    mode_value = rule.execution_mode.value
    return (
        "[LEARNED PATTERN]\n"
        "This property manager has previously handled similar "
        f"situations by action={action_value} "
        f"(confidence={rule.confidence:.2f}, mode={mode_value}).\n"
        "Prefer this action unless a guardrail or blocker prevents it."
    )


# ``_format_availability_calendar`` + ``_format_reservation_context``
# (and their no-data fallback constants) were extracted to
# ``brain_engine.conversation.prompt_formatters`` as part of the R8
# refactor.  They are re-exported from this module so existing
# call sites (and any external imports from
# ``brain_engine.conversation.service``) keep working unchanged.
# See ``prompt_formatters.py`` for the implementations.


# ── Base system prompt ───────────────────────────────────────── #

_BASE_SYSTEM_PROMPT = """You are an AI assistant for a vacation rental property management company.

Your job is to help guests with their questions and requests about their stay.

Key behaviors:
- Use the provided tools to find accurate information before responding
- NEVER invent or assume information not provided by tools or knowledge base
- Speak as the property manager (first person, "we")
- Be helpful, concise, and accurate
- For multiple topics in one message, address each one
- If you cannot answer something, say you will check and get back

Tool usage rules:
- Before calling any tool, state your reasoning in ONE sentence explaining why this \
specific tool is needed for this specific request
- Read each tool's "Do NOT use" instructions carefully — calling the wrong tool wastes \
time and degrades quality
- Prefer the FEWEST tool calls needed — do not call tools speculatively
- If a tool returns no results, do NOT retry with a different query unless the guest \
asks a follow-up question

Available context:
- You may have access to the property's knowledge base (use rag_document_search)
- You may have access to reservation details (use reservation_info_retriever)
- You may have access to availability/pricing (use availability_checker)
"""


# ── Property knowledge helpers ───────────────────────────────── #


def _reply_language_instruction(respond_language: str | None) -> str:
    """Build the reply-language directive for the agent system prompt.

    Driven by logic, not a fixed language set: an explicit customer
    ``respond_language`` pins the reply language; otherwise the assistant
    mirrors the guest's own language for ANY language of the world (the
    model sees the current message and matches it).

    Args:
        respond_language: Customer-level forced reply language; empty /
            ``None`` means auto (mirror the guest).

    Returns:
        The instruction line to splice into the system prompt.
    """
    pinned = (respond_language or "").strip()
    if pinned:
        return f"\nIMPORTANT: Respond in {pinned}."
    return (
        "\nIMPORTANT: Write your entire reply in the same language as the "
        "guest's most recent message, whatever language it is. Do not "
        "switch languages."
    )


def _format_mockup_knowledge(prop: dict[str, Any]) -> str:
    """Format mockup property data into a knowledge base section.

    Args:
        prop: Property dict from mockup_loader.

    Returns:
        Formatted knowledge base text.
    """
    lines: list[str] = [
        "## Property Knowledge Base",
        f"Property: {prop.get('name', 'Unknown')}",
        f"Address: {prop.get('address', 'N/A')}",
        f"City: {prop.get('city', '')}, Country: {prop.get('country', '')}",
        f"Check-in time: {prop.get('check_in_time', 'N/A')}",
        f"Check-out time: {prop.get('check_out_time', 'N/A')}",
        f"Max guests: {prop.get('max_guests', 'N/A')}",
    ]

    if prop.get("early_checkin_fee"):
        lines.append(f"Early check-in fee: ${prop['early_checkin_fee']}")
    if prop.get("late_checkout_fee"):
        lines.append(f"Late checkout fee: ${prop['late_checkout_fee']}")

    access = prop.get("property_access", {})
    if access:
        lines.append("")
        lines.append("### Access & WiFi")
        lines.append(f"WiFi network: {access.get('wifi_name', 'N/A')}")
        lines.append(f"WiFi password: {access.get('wifi_password', 'N/A')}")
        if access.get("building_door_code"):
            lines.append(f"Building door code: {access['building_door_code']}")
        if access.get("lockbox_code"):
            lines.append(f"Lockbox code: {access['lockbox_code']}")

    rules = prop.get("house_rules", [])
    if rules:
        lines.append("")
        lines.append("### House Rules")
        for rule in rules:
            lines.append(f"- {rule}")

    return "\n".join(lines)


def _format_profile_knowledge(profile: PropertyProfile) -> str:
    """Format a cached :class:`PropertyProfile` into a system-prompt block.

    Mirrors the shape produced by :func:`_format_mockup_knowledge`
    so prompt assembly downstream cannot tell the source apart.

    Reads the unified ``static_payload`` populated by the onboarding
    bootstrap (Hostaway / GraphQL adapter), which already contains the
    WiFi / parking / pet / check-in fields the chat needs.

    Args:
        profile: Cached property snapshot from
            :class:`PropertyProfileStore`.

    Returns:
        Multi-line knowledge text (empty when the payload has no
        meaningful fields).
    """
    sp = dict(profile.static_payload or {})

    def _yes_no(flag: Any) -> str:
        if flag is True:
            return "yes"
        if flag is False:
            return "no"
        return "unknown"

    lines: list[str] = [
        "## Property Knowledge Base",
        f"Property: {profile.title or sp.get('title') or 'Unknown'}",
    ]

    address_parts = [
        sp.get("address") or sp.get("street") or "",
        sp.get("zip_code") or "",
    ]
    address = " ".join(p for p in address_parts if p).strip()
    if address:
        lines.append(f"Address: {address}")

    city = profile.city or sp.get("city") or ""
    country = profile.country or sp.get("country") or ""
    if city or country:
        lines.append(f"City: {city}, Country: {country}")

    if sp.get("time_zone"):
        lines.append(f"Time zone: {sp['time_zone']}")

    if sp.get("check_in_time"):
        lines.append(f"Check-in time: {sp['check_in_time']}")
    if sp.get("check_out_time"):
        lines.append(f"Check-out time: {sp['check_out_time']}")

    if profile.max_occupancy:
        lines.append(f"Max guests: {profile.max_occupancy}")
    if profile.bedrooms:
        lines.append(f"Bedrooms: {profile.bedrooms}")
    if profile.bathrooms:
        lines.append(f"Bathrooms: {profile.bathrooms}")
    if sp.get("min_nights") or sp.get("max_nights"):
        lines.append(
            f"Stay length: min {sp.get('min_nights', '?')} / "
            f"max {sp.get('max_nights', '?')} nights",
        )

    if profile.base_price:
        lines.append(
            f"Base price: {profile.base_price} {profile.base_currency}",
        )
    cleaning_fee = sp.get("cleaning_fee")
    if cleaning_fee is not None:
        if cleaning_fee > 0:
            lines.append(f"Cleaning fee: {cleaning_fee}")
        else:
            lines.append("Cleaning fee: none (no separate cleaning fee charged)")
    security_deposit_fee = sp.get("security_deposit_fee")
    if security_deposit_fee is not None:
        if security_deposit_fee > 0:
            lines.append(f"Security deposit: {security_deposit_fee}")
        else:
            lines.append("Security deposit: none (no deposit required)")

    lines.append("")
    lines.append("### Amenities & Access")
    lines.append(f"WiFi available: {_yes_no(sp.get('has_wifi'))}")
    if sp.get("wifi_network"):
        lines.append(f"WiFi network: {sp['wifi_network']}")
    lines.append(f"Parking available: {_yes_no(sp.get('has_parking'))}")
    lines.append(f"Pets allowed: {_yes_no(sp.get('pets_allowed'))}")
    pet_fee = sp.get("pet_fee")
    if pet_fee is not None:
        if pet_fee > 0:
            lines.append(f"Pet fee: {pet_fee}")
        else:
            lines.append("Pet fee: none (no separate pet fee charged)")
    if sp.get("instant_bookable") is not None:
        lines.append(
            f"Instant bookable: {_yes_no(sp.get('instant_bookable'))}"
        )
    if sp.get("door_code"):
        lines.append(f"Door code: {sp['door_code']}")

    amenities = sp.get("amenities") or []
    if amenities:
        names = [
            str(a.get("name") or a.get("code") or "").strip()
            for a in amenities
            if isinstance(a, Mapping)
        ]
        names = [n for n in names if n]
        if names:
            lines.append("Amenities: " + ", ".join(sorted(set(names))))

    descriptions = sp.get("descriptions") or []
    if descriptions:
        lines.append("")
        lines.append("### Descriptions")
        for desc in descriptions:
            if not isinstance(desc, Mapping):
                continue
            text = str(desc.get("text") or "").strip()
            if not text:
                continue
            lang = str(desc.get("language") or "").strip()
            type_code = str(desc.get("typeCode") or "").strip()
            header = (
                " / ".join(p for p in (type_code, lang) if p) or "Description"
            )
            lines.append(f"- [{header}] {text}")

    return "\n".join(lines)


def _format_owner_flexibility(profile: Any) -> str:
    """Render an :class:`OwnerFlexibilityProfile` as an LLM-readable block.

    Surfaces the three field groups the live chat actually needs:

    * ``amenity_exceptions`` — owner-level carve-outs (e.g. baby crib
      "available for reservations over $2000, $50 fee").  This is the
      group that closes the baby-crib denial captured on 2026-05-18
      where the agent answered "we do not have a baby crib" because
      it never saw the conditional rule.
    * ``checkin_rules`` — early-checkin / late-checkout policies the
      LLM should quote verbatim instead of inferring them from
      cross-property data (the Italy "late check-in is paid" leak).
    * ``fee_rules`` — extra-guest / pet / cleaning / child surcharges
      so quoted prices match the owner baseline.
    * ``stay_rules`` — min-stay / max-stay / advance-booking window.
    * ``occupancy_capacity`` — pets-allowed, infants-count-as-guests,
      max-guests when the owner has stated.

    Returns ``""`` when every group is empty so the caller can splice
    without a stray header — the most common case for properties
    that have no owner overrides yet.

    Args:
        profile: :class:`OwnerFlexibilityProfile` snapshot.  Typed as
            ``Any`` to avoid a circular import — the owner_profile
            module already imports from conversation models.

    Returns:
        Multi-line knowledge text, or ``""`` when the snapshot adds
        no information beyond what the property profile already
        exposes.
    """
    lines: list[str] = []

    def _push(header: str, items: list[str]) -> None:
        """Append ``header`` then ``items`` only when ``items`` is non-empty."""
        if items:
            if lines:
                lines.append("")
            lines.append(header)
            lines.extend(items)

    capacity = getattr(profile, "occupancy_capacity", None)
    if capacity is not None:
        cap_items: list[str] = []
        if capacity.max_guests is not None:
            cap_items.append(f"- Max guests (owner): {capacity.max_guests}")
        if capacity.max_adults is not None:
            cap_items.append(f"- Max adults: {capacity.max_adults}")
        if capacity.max_children is not None:
            cap_items.append(f"- Max children: {capacity.max_children}")
        if capacity.pets_allowed is not None:
            cap_items.append(
                f"- Pets allowed: {'yes' if capacity.pets_allowed else 'no'}"
            )
        if capacity.infants_count_as_guests is not None:
            cap_items.append(
                "- Infants count toward max guests: "
                f"{'yes' if capacity.infants_count_as_guests else 'no'}"
            )
        _push("### Owner Capacity Rules", cap_items)

    fees = getattr(profile, "fee_rules", None)
    if fees is not None:
        fee_items: list[str] = []
        if fees.extra_guest_fee is not None:
            fee_items.append(f"- Extra guest fee: {fees.extra_guest_fee}")
        if fees.child_fee is not None:
            fee_items.append(f"- Child fee: {fees.child_fee}")
        if fees.infant_fee is not None:
            fee_items.append(f"- Infant fee: {fees.infant_fee}")
        if fees.pet_fee is not None:
            fee_items.append(f"- Pet fee: {fees.pet_fee}")
        if fees.cleaning_fee is not None:
            fee_items.append(f"- Cleaning fee: {fees.cleaning_fee}")
        _push("### Owner Fee Rules", fee_items)

    stay = getattr(profile, "stay_rules", None)
    if stay is not None:
        stay_items: list[str] = []
        if stay.default_min_stay is not None:
            stay_items.append(f"- Default min stay: {stay.default_min_stay}")
        if stay.hard_min_stay_floor is not None:
            stay_items.append(
                f"- Hard min stay floor: {stay.hard_min_stay_floor}"
            )
        if stay.max_stay is not None:
            stay_items.append(f"- Max stay: {stay.max_stay}")
        if stay.advance_booking_window is not None:
            stay_items.append(
                f"- Advance booking window (days): "
                f"{stay.advance_booking_window}"
            )
        _push("### Owner Stay Rules", stay_items)

    checkin = getattr(profile, "checkin_rules", None)
    if checkin is not None:
        ci_items: list[str] = []
        if checkin.std_checkin_time:
            ci_items.append(
                f"- Standard check-in time: {checkin.std_checkin_time}"
            )
        if checkin.std_checkout_time:
            ci_items.append(
                f"- Standard check-out time: {checkin.std_checkout_time}"
            )
        if checkin.early_checkin_policy:
            ci_items.append(
                f"- Early check-in policy: {checkin.early_checkin_policy}"
            )
        if checkin.late_checkout_policy:
            ci_items.append(
                f"- Late check-out policy: {checkin.late_checkout_policy}"
            )
        _push("### Owner Check-in Rules", ci_items)

    amenity_exceptions = getattr(profile, "amenity_exceptions", ()) or ()
    if amenity_exceptions:
        ax_items: list[str] = []
        for ax in amenity_exceptions:
            availability = "AVAILABLE" if ax.available else "NOT AVAILABLE"
            note = f" — {ax.notes}" if ax.notes else ""
            ax_items.append(f"- {ax.amenity_code}: {availability}{note}")
        _push(
            "### Owner Amenity Exceptions "
            "(authoritative — overrides static amenities)",
            ax_items,
        )

    local_recs = getattr(profile, "local_recommendations", ()) or ()
    if local_recs:
        # Group by category so the LLM can scan "restaurant" / "cafe"
        # blocks without re-parsing a flat list of mixed types.
        by_category: dict[str, list[Any]] = {}
        for rec in local_recs:
            by_category.setdefault(rec.category or "general", []).append(rec)
        rec_items: list[str] = []
        for category in sorted(by_category):
            rec_items.append(f"**{category}**:")
            for rec in by_category[category]:
                distance = f" ({rec.distance})" if rec.distance else ""
                notes = f" — {rec.notes}" if rec.notes else ""
                rec_items.append(f"  - {rec.name}{distance}{notes}")
        _push(
            "### Owner Local Recommendations "
            "(authoritative — quote these for nearest-place questions)",
            rec_items,
        )

    if not lines:
        return ""

    header = (
        "## Owner Flexibility Rules "
        "(authoritative — apply BEFORE generic property knowledge)"
    )
    return f"{header}\n" + "\n".join(lines)


def _format_foundation_scenario_hint(analysis: Any) -> str:
    """Render the FL-01 catalog scenario the orchestrator matched.

    The orchestrator (``_run_foundation_analysis``) populates
    ``state.foundation_analysis`` with a :class:`FoundationMatch`
    that carries the top-K catalog candidates and — when the
    matcher had a confident hit — the full
    :class:`FoundationScenario` entry.  Pre-R7 the analysis was
    used for telemetry / DecisionCase logging / SSE side effects
    only; the LLM never saw the matched scenario, which is why
    Sandbox UI replies looked generic while the Postman
    ``/foundation/analyze`` endpoint surfaced the match.

    This helper renders the matched scenario into a Markdown block
    the LLM treats as authoritative:

    * **Scenario:** title + id + stage label so the agent knows
      which workbook row fired.
    * **AI Default Behavior** — verbatim from the workbook so the
      LLM follows the policy author's wording.
    * **Auto-reply / Escalation / Learn** policies so the agent
      knows whether it should answer at all (``No`` ⇒ defer).
    * **Required Data Checks** — bullet list so the LLM defers
      when the data is missing.
    * **What Not To Learn** — verbatim safety note when present.

    Returns ``""`` (empty string) when any of the following hold,
    so the caller can splice without a dangling header:

    1. ``analysis is None`` — orchestrator unwired or disabled.
    2. ``foundation_match`` is empty (no candidates above the
       similarity floor).
    3. ``dominant_catalog_entry is None`` — the matcher had
       candidates but the catalog lookup returned no scenario
       (Q5-A similarity gate trip).
    """
    if analysis is None:
        return ""
    match = getattr(analysis, "foundation_match", None)
    if match is None or getattr(match, "is_empty", True):
        return ""
    entry = getattr(match, "dominant_catalog_entry", None)
    if entry is None:
        return ""

    lines: list[str] = [
        "## Matched Foundation Scenario "
        "(authoritative — follow this scenario's policy)",
    ]

    title = getattr(entry, "title", "")
    scenario_id = getattr(entry, "scenario_id", "")
    stage_label = getattr(entry, "stage_label", "")
    if title or scenario_id:
        identity_parts: list[str] = []
        if scenario_id:
            identity_parts.append(f"id: ``{scenario_id}``")
        if stage_label:
            identity_parts.append(f"stage: {stage_label}")
        suffix = f" ({', '.join(identity_parts)})" if identity_parts else ""
        lines.append(f"**Scenario:** {title}{suffix}")

    behaviour = (getattr(entry, "ai_default_behavior", "") or "").strip()
    if behaviour:
        lines.append(f"**AI Default Behavior:** {behaviour}")

    auto_reply = (getattr(entry, "should_auto_reply", "") or "").strip()
    if auto_reply:
        lines.append(f"**Auto-reply policy:** {auto_reply}")

    escalate = (getattr(entry, "should_escalate_to_pm", "") or "").strip()
    if escalate:
        lines.append(f"**Escalate to PM:** {escalate}")

    create_task = (getattr(entry, "should_create_task", "") or "").strip()
    if create_task:
        lines.append(f"**Create task:** {create_task}")

    required = getattr(entry, "required_data_checks", ()) or ()
    if required:
        lines.append("**Required Data Checks:**")
        for check in required:
            lines.append(f"  - {check}")

    safety = (getattr(entry, "what_not_to_learn", "") or "").strip()
    if safety:
        lines.append(f"**Safety — what NOT to commit to:** {safety}")

    return "\n".join(lines)


# ── Memory context helpers (Task 4) ──────── #


# Sentinel for "reranker cache uninitialised" so ``None`` can mean
# "reranker disabled or build failed" without ambiguity.
_UNSET: Final[object] = object()


def _memory_retrieval_enabled() -> bool:
    """Whether ``_load_memory_context`` should consult MemorySystem.

    Read on every call so a deploy can flip
    ``BRAIN_MEMORY_RETRIEVAL_ENABLED`` without restarting the API
    pod.  Default off — the conversation pipeline keeps producing an
    empty ``state.memory_facts`` until the team explicitly opts in.
    """
    raw = os.environ.get(_MEMORY_RETRIEVAL_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _unified_recall_enabled() -> bool:
    """Whether ``_load_memory_context`` uses the property-scoped unified
    recall (knowledge graph + scoped semantic) instead of the legacy
    single semantic search.

    Read on every call so the toggle is live without a pod restart.
    Default off — opting in is what activates the richer recall.
    """
    raw = os.environ.get(_UNIFIED_RECALL_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _reranker_enabled() -> bool:
    """Thin wrapper that defers to the Sprint A reranker module.

    Kept here as a private helper so the conversation service does
    not import the env-var name directly — when the flag's name
    changes upstream, only ``brain_engine.memory.reranker`` updates.
    """
    from brain_engine.memory.reranker import (
        reranker_enabled as _flag,
    )

    return _flag()


def _reservation_status(request: ConversationRequest) -> str:
    """Return the reservation status attached to ``request`` or ``""``.

    Reads ``request.reservation_context.status`` and forwards the raw
    PMS label verbatim — case normalisation is handled downstream by
    :func:`policies_for_status`.  Returns ``""`` (rather than raising)
    when no reservation context is attached, so a status-less request
    quietly skips the operational-policy block instead of failing the
    prompt assembly.
    """
    ctx = getattr(request, "reservation_context", None)
    if ctx is None:
        return ""
    return getattr(ctx, "status", "") or ""


def _build_memory_filter(
    request: ConversationRequest,
) -> dict[str, str]:
    """Build the multi-tenancy metadata filter for semantic search.

    Two scopes the platform must never blur:

    * ``customer_id`` — the tenant.  Leaking another tenant's facts
      into this one's conversation is a confidentiality breach.
    * ``property_id`` — the property within the tenant.  An owner
      with two listings should not see early-checkin policies from
      one bleed into the other.

    Either field empty omits its constraint — the request shape
    permits property-less customer-scoped queries (e.g. multi-stay
    history) without forcing an artificial property filter.
    """
    out: dict[str, str] = {}
    if request.customer_id:
        out["customer_id"] = request.customer_id
    if request.property_id:
        out["property_id"] = request.property_id
    return out


def _record_text(record: Any) -> str:
    """Extract the prompt-ready text from a memory record.

    Accepts both :class:`brain_engine.memory.semantic_memory.MemoryRecord`
    (``record.text``) and the dict shape returned by some legacy
    backends (``record["text"]``).  Returns an empty string when
    neither shape carries text — the caller filters those out via
    truthiness when assembling the final list.
    """
    if hasattr(record, "text"):
        text = getattr(record, "text", "") or ""
    elif isinstance(record, Mapping):
        text = str(record.get("text", "") or "")
    else:
        text = ""
    return str(text).strip()


def _summarize_episodes(episodes: list[Any]) -> str:
    """Concatenate episode contents into a flat summary string.

    Deterministic by design — ``ContextAssembler`` will trim the
    summary to its token budget downstream, so no LLM-summarisation
    is needed at this layer.  An LLM-based summariser can replace
    this helper without changing the call site.
    """
    parts: list[str] = []
    for ep in episodes:
        content = getattr(ep, "content", None)
        if content is None and isinstance(ep, Mapping):
            content = ep.get("content")
        text = str(content or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)
