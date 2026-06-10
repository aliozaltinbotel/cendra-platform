"""Conversation API endpoints — FastAPI routes for guest messaging and OPS.

Provides the HTTP interface for:
- Guest conversation processing (POST /conversations)
- OPS pipeline (generate, parse, classify, verify, pm-agent)
- Customer settings management
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from brain_engine.conversation.models import (
    ConversationRequest,
    ConversationResponse,
)
from brain_engine.conversation.service import ConversationService
from brain_engine.ops.models import (
    OpsClassifyRequest,
    OpsClassifyResponse,
    OpsGenerateRequest,
    OpsGenerateResponse,
    OpsParseReplyRequest,
    OpsParseReplyResponse,
    OpsVerifyRequest,
    OpsVerifyResponse,
    PmAgentRequest,
    PmAgentResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["conversation"])

# ── Dependency injection ─────────────────────────────────────── #

_conversation_service: ConversationService | None = None


def get_conversation_service(request: Request) -> ConversationService:
    """Get or create the conversation service singleton.

    The service is lazily initialised with the DecisionCase store, the
    PM fact store, and the §10 priority-chain
    :class:`ExecutionOrchestrator` that were assembled during
    application startup (see ``api_server/server.py`` lifespan,
    :func:`brain_engine.patterns.wiring.build_decision_case_store`,
    and :func:`brain_engine.orchestrator.wiring.build_execution_orchestrator`).
    Missing dependencies on ``app.state`` resolve to ``None`` and
    silently disable the corresponding pipeline hook, preserving the
    previous behaviour for minimal-config environments.

    Args:
        request: Incoming FastAPI request — used only to read
            ``request.app.state``.

    Returns:
        ConversationService singleton bound to the app's stores and
        orchestrator.
    """
    global _conversation_service
    if _conversation_service is None:
        case_store = getattr(request.app.state, "case_store", None)
        pm_fact_store = getattr(request.app.state, "pm_fact_store", None)
        orchestrator = getattr(request.app.state, "orchestrator", None)
        reservation_prefetcher = getattr(
            request.app.state, "reservation_prefetcher", None,
        )
        # Task 2 of CLAUDE_CODE_WIRING_FIX_PLAN.md — surface the
        # cognitive memory system to the conversation pipeline.  The
        # actual lifespan wire-up that populates ``app.state`` lands
        # in Task 3; until then this resolves to ``None`` and the
        # service stays on the pre-Task-4 path.
        memory_system = getattr(
            request.app.state, "memory_system", None,
        )
        # Mümin 2026-05-13 (PR #F): forward the shared memory
        # fan-out so live conversation extraction emits the same
        # timeline / semantic / KG entries the bootstrap path
        # already does.  Stays a no-op when absent.
        memory_fanout = getattr(
            request.app.state, "memory_fanout", None,
        )
        # Sprint 6 W1 follow-up — the FL-16 orchestrator is built once
        # in lifespan and stashed on ``app.state.foundation_orchestrator``.
        # When present every live guest message produces an
        # AnalysisResult attached to ``state.foundation_analysis``,
        # whose dominant slug the service propagates onto the logged
        # :class:`DecisionCase`'s ``foundation_scenario_id`` so the
        # pattern miner (W5) can surface it on the resulting
        # :class:`PatternRule` origin trail.  ``None`` keeps the
        # pre-W1 path bit-for-bit identical.
        foundation_orchestrator = getattr(
            request.app.state, "foundation_orchestrator", None,
        )
        # Property knowledge surface — without these the REST pipeline
        # skips ``_load_property_knowledge`` (profile_store is None) and
        # the agent defers every property question ("no info, ask PM")
        # even though the harvested profile carries the answer.  The
        # AG-UI SSE handler already wires both; this brings the REST
        # ``/conversations`` endpoint to parity.
        profile_store = getattr(
            request.app.state, "property_profile_store", None,
        )
        owner_profile_store = getattr(
            request.app.state, "owner_profile_store", None,
        )
        _conversation_service = ConversationService(
            case_store=case_store,
            pm_fact_store=pm_fact_store,
            orchestrator=orchestrator,
            reservation_prefetcher=reservation_prefetcher,
            memory_system=memory_system,
            memory_fanout=memory_fanout,
            foundation_orchestrator=foundation_orchestrator,
            profile_store=profile_store,
            owner_profile_store=owner_profile_store,
        )
    return _conversation_service


# ── Guest Conversation ───────────────────────────────────────── #


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    summary="Process a guest message",
)
async def process_conversation(
    request: ConversationRequest,
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    """Process a guest message through the full pipeline.

    Pipeline: preprocess -> classify -> guardrails -> agent -> postprocess.

    Args:
        request: Conversation request with messages.
        service: Injected conversation service.

    Returns:
        AI response with metadata, tags, and routing flags.
    """
    return await service.process(request)


# ── OPS Endpoints ────────────────────────────────────────────── #


@router.post(
    "/ops/generate-message",
    response_model=OpsGenerateResponse,
    summary="Generate OPS message",
)
async def ops_generate_message(
    request: OpsGenerateRequest,
) -> OpsGenerateResponse:
    """Generate a message for cleaner, vendor, owner, or guest.

    Args:
        request: Message generation request with context.

    Returns:
        Generated message with approval and urgency flags.
    """
    from brain_engine.ops.message_generator import generate_ops_message
    return await generate_ops_message(request)


@router.post(
    "/ops/parse-reply",
    response_model=OpsParseReplyResponse,
    summary="Parse vendor/cleaner reply",
)
async def ops_parse_reply(
    request: OpsParseReplyRequest,
) -> OpsParseReplyResponse:
    """Parse a vendor or cleaner reply into structured data.

    Args:
        request: Reply parsing request.

    Returns:
        Structured data: confirmation, ETA, cost, next actions.
    """
    from brain_engine.ops.reply_parser import parse_ops_reply
    return await parse_ops_reply(request)


@router.post(
    "/ops/classify-issue",
    response_model=OpsClassifyResponse,
    summary="Classify maintenance issue",
)
async def ops_classify_issue(
    request: OpsClassifyRequest,
) -> OpsClassifyResponse:
    """Classify a maintenance issue into vendor categories.

    Args:
        request: Issue classification request.

    Returns:
        Matched vendor categories with urgency levels.
    """
    from brain_engine.ops.issue_classifier import classify_ops_issue
    return await classify_ops_issue(request)


@router.post(
    "/ops/verify-message",
    response_model=OpsVerifyResponse,
    summary="Verify OPS message safety",
)
async def ops_verify_message(
    request: OpsVerifyRequest,
) -> OpsVerifyResponse:
    """Verify a generated message contains no fabricated content.

    Args:
        request: Verification request.

    Returns:
        Safety flag and list of issues found.
    """
    from brain_engine.ops.message_verifier import verify_ops_message
    return await verify_ops_message(request)


@router.post(
    "/ops/pm-agent",
    response_model=PmAgentResponse,
    summary="PM operations agent",
)
async def ops_pm_agent(
    request: PmAgentRequest,
) -> PmAgentResponse:
    """Process a PM's request through the operations agent.

    The agent reads context, reasons about intent, and
    builds an action plan.

    Args:
        request: PM agent request with context.

    Returns:
        Action plan, message to PM, and clarification flag.
    """
    from brain_engine.ops.pm_agent import run_pm_agent
    return await run_pm_agent(request)


# ── OPS Parse PM Instruction ──────────────────────────────────── #


@router.post("/ops/parse-pm-instruction", summary="Parse PM instruction")
async def ops_parse_pm_instruction(request: dict) -> dict:
    """Parse a PM's informal instruction into a structured action."""
    from brain_engine.ops.models import OpsParsePmInstructionRequest
    from brain_engine.ops.parse_pm_instruction import (
        parse_pm_instruction,
    )
    req = OpsParsePmInstructionRequest(**request)
    result = await parse_pm_instruction(req)
    return result.model_dump()


# ── Missing Information ──────────────────────────────────────── #


@router.post("/extract-missing-information", summary="Extract missing info")
async def extract_missing_info(request: dict) -> dict:
    """Identify unresolved guest inquiries from conversation."""
    from brain_engine.conversation.missing_info_extractor import (
        MissingInfoRequest,
        extract_missing_information,
    )
    req = MissingInfoRequest(**request)
    result = await extract_missing_information(req)
    return result.model_dump()


# ── Guardrail Preprocessing ──────────────────────────────────── #


@router.post("/guardrail-preprocessing", summary="Preprocess guardrails")
async def guardrail_preprocessing(request: dict) -> dict:
    """Determine active guardrails based on business flags.

    Takes a customer_id and business flags, returns the
    guardrails that should be applied for this request.
    """
    from brain_engine.customer.settings_service import CustomerSettingsService
    from brain_engine.guardrails.customer_guardrails import (
        format_guardrails_for_prompt,
        select_guardrails,
    )

    customer_id = request.get("customer_id", "")
    flags = request.get("flags", [])

    svc = CustomerSettingsService()
    settings = await svc.get_settings(customer_id)
    guardrails = select_guardrails(settings, flags)

    return {
        "status": True,
        "guardrails": [
            {"title": g.title, "guardrail": g.guardrail, "priority": g.priority.value}
            for g in guardrails
        ],
        "guardrail_prompt": format_guardrails_for_prompt(guardrails),
        "count": len(guardrails),
    }


# ── Customer Tags ────────────────────────────────────────────── #


@router.post("/customer-tags", summary="Match customer tags")
async def customer_tags(request: dict) -> dict:
    """Match a guest message against customer-defined semantic tags."""
    from brain_engine.conversation.customer_tags_service import (
        CustomerTagsRequest,
        match_customer_tags,
    )
    req = CustomerTagsRequest(**request)
    result = await match_customer_tags(req)
    return result.model_dump()


# ── Custom Tone Generation ───────────────────────────────────── #


@router.post("/custom-tone", summary="Generate custom tone")
async def custom_tone(request: dict) -> dict:
    """Generate a structured tone prompt from PM free-text instructions."""
    from brain_engine.customer.tone_generator import (
        CustomToneRequest,
        generate_custom_tone,
    )
    req = CustomToneRequest(**request)
    result = await generate_custom_tone(req)
    return result.model_dump()


# ── RAG Endpoints ────────────────────────────────────────────── #


@router.post("/rag-answer", summary="Generate RAG answer")
async def rag_answer(request: dict) -> dict:
    """Generate an answer using RAG retrieval from knowledge base."""
    from brain_engine.conversation.rag_indexer import (
        RagAnswerRequest,
        generate_rag_answer,
    )
    req = RagAnswerRequest(**request)
    result = await generate_rag_answer(req)
    return result.model_dump()


@router.post("/rag-index-conversations", summary="Index conversations in RAG")
async def rag_index_conversations(request: dict) -> dict:
    """Index completed conversations into the RAG vector database."""
    from brain_engine.conversation.rag_indexer import (
        IndexConversationRequest,
        index_conversation,
    )
    req = IndexConversationRequest(**request)
    result = await index_conversation(req)
    return result.model_dump()


# ── Escalation Resolution ────────────────────────────────────── #


@router.post("/regenerate-escalation-resolution", summary="Resolve escalation")
async def regenerate_escalation_resolution(request: dict) -> dict:
    """Process an escalation resolution — convert to knowledge and regenerate."""
    from brain_engine.conversation.regenerate_service import (
        UpdateKnowledgeRequest,
        regenerate_with_knowledge,
    )
    req = UpdateKnowledgeRequest(**request)
    result = await regenerate_with_knowledge(req)
    return result.model_dump()


# ── Regenerate Endpoints ──────────────────────────────────────── #


@router.post("/regenerate", summary="Regenerate AI response with new info")
async def regenerate(request: dict) -> dict:
    """Regenerate an AI response with new information from PM."""
    from brain_engine.conversation.regenerate_service import (
        RegenerateRequest,
        regenerate_response,
    )
    req = RegenerateRequest(**request)
    result = await regenerate_response(req)
    return result.model_dump()


@router.post("/regenerate-multiple", summary="Batch regenerate responses")
async def regenerate_multiple(request: dict) -> dict:
    """Batch regenerate multiple AI responses."""
    from brain_engine.conversation.regenerate_service import (
        RegenerateMultipleRequest,
    )
    from brain_engine.conversation.regenerate_service import (
        regenerate_multiple as regen_multi,
    )
    req = RegenerateMultipleRequest(**request)
    result = await regen_multi(req)
    return result.model_dump()


@router.post("/regenerate-pm-knowledge", summary="Update KB and regenerate")
async def regenerate_pm_knowledge(request: dict) -> dict:
    """Update knowledge base and regenerate response."""
    from brain_engine.conversation.regenerate_service import (
        UpdateKnowledgeRequest,
        regenerate_with_knowledge,
    )
    req = UpdateKnowledgeRequest(**request)
    result = await regenerate_with_knowledge(req)
    return result.model_dump()


# ── Rule Creation Endpoints ──────────────────────────────────── #


@router.post("/rule-creation/start", summary="Start rule creation workflow")
async def rule_creation_start(request: dict) -> dict:
    """Start a new agentic rule creation workflow."""
    from brain_engine.rule_creation.models import RuleCreationRequest
    from brain_engine.rule_creation.workflow import start_workflow
    req = RuleCreationRequest(**request)
    result = await start_workflow(req)
    return result.model_dump()


@router.post("/rule-creation/send-message", summary="Send message to workflow")
async def rule_creation_send(request: dict) -> dict:
    """Send a PM message to an active rule creation workflow."""
    from brain_engine.rule_creation.models import RuleCreationRequest
    from brain_engine.rule_creation.workflow import send_message
    req = RuleCreationRequest(**request)
    result = await send_message(req)
    return result.model_dump()


@router.get(
    "/rule-creation/status/{workflow_id}",
    summary="Get workflow status",
)
async def rule_creation_status(workflow_id: str) -> dict:
    """Get current status of a rule creation workflow."""
    from brain_engine.rule_creation.workflow import get_workflow_status
    result = get_workflow_status(workflow_id)
    return result.model_dump()


# ── WhatsApp Channel ─────────────────────────────────────────── #


@router.post("/whatsapp/process", summary="Process WhatsApp message")
async def whatsapp_process(request: dict) -> dict:
    """Process a WhatsApp message without property context."""
    from brain_engine.whatsapp.channel_service import (
        WhatsAppRequest,
        process_whatsapp_message,
    )
    req = WhatsAppRequest(**request)
    result = await process_whatsapp_message(req)
    return result.model_dump()


# ── Reviews ──────────────────────────────────────────────────── #


@router.post("/reviews", summary="Create tasks from guest review")
async def create_review_tasks(request: dict) -> dict:
    """Analyze a guest review and create actionable tasks."""
    from brain_engine.smart_engine.review_task_creator import (
        ReviewTaskRequest,
        create_tasks_from_review,
    )
    req = ReviewTaskRequest(**request)
    result = await create_tasks_from_review(req)
    return result.model_dump()


# ── Upsell ───────────────────────────────────────────────────── #


@router.post("/upsell-templates", summary="Generate upsell message")
async def upsell_template(request: dict) -> dict:
    """Generate an approved upsell offer message."""
    from brain_engine.smart_engine.upsell_templates import (
        UpsellTemplateRequest,
        generate_upsell_message,
    )
    req = UpsellTemplateRequest(**request)
    result = await generate_upsell_message(req)
    return result.model_dump()


@router.post("/gap-night-offer", summary="Generate gap night offer")
async def gap_night_offer(request: dict) -> dict:
    """Generate a personalized gap night discount offer."""
    from brain_engine.smart_engine.gap_night_offer import (
        GapNightRequest,
        generate_gap_night_offer,
    )
    req = GapNightRequest(**request)
    result = await generate_gap_night_offer(req)
    return result.model_dump()


# ── Private/Legacy Conversation ───────────────────────────────── #


@router.post("/private-conversation", summary="Legacy conversation (no agent)")
async def private_conversation(
    request: ConversationRequest,
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    """Legacy conversation endpoint — processes without full agent pipeline.

    Fetches listing/reservation from PMS, extracts flags, generates
    response via direct LLM call. No tool usage, no ReAct agent.
    Kept for backward compatibility with older integrations.
    """
    return await service.process(request)


# ── Health Check ─────────────────────────────────────────────── #


@router.get("/conversation-health")
async def conversation_health() -> dict[str, str]:
    """Health check for conversation pipeline.

    Returns:
        Status dict.
    """
    return {"status": "ok", "service": "conversation-pipeline"}
