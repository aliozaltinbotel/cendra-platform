"""Cendra API Adapter — The API that Cendra platform calls.

Brain Engine receives events, thinks, returns decisions + MCP tool calls.
Cendra executes actions through its own infrastructure (Temporal, MCP, WhatsApp).

Endpoints:
    POST /api/v1/guest/message       -> Replaces Guest Agent (LangGraph ReAct)
    POST /api/v1/ops/event           -> Replaces OpsSessionWorkflow (963 lines)
    POST /api/v1/ops/contact-reply   -> Replaces Cleaner/Vendor Agents
    POST /api/v1/approval/decision   -> Learns from owner/PM decisions
    POST /api/v1/booking/new         -> Full booking lifecycle
    GET  /api/v1/property/{id}/intelligence -> Accumulated knowledge
    POST /api/v1/memory/consolidate  -> Nightly/monthly consolidation trigger
    GET  /api/v1/metrics             -> Learning metrics
    GET  /api/v1/health              -> Health + stats

Each endpoint follows the orchestrator pattern: small coordinator
function calls focused sub-functions. Max ~30 lines per function.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from brain_engine.api.conversation_memory import (
    load_conversation_history,
    save_conversation_turn,
)
from brain_engine.api.models import (
    ActiveProcessListResponse,
    ActiveProcessResponse,
    ApprovalDecisionRequest,
    BrainEngineResponse,
    ClassificationFlags,
    ConsolidationRequest,
    ContactReplyRequest,
    EscalationInfo,
    GuestMessageRequest,
    HealthResponse,
    KnowledgeSyncRequest,
    KnowledgeSyncResponse,
    MCPAction,
    MetricsResponse,
    NewBookingRequest,
    OpsEventRequest,
    OpsSessionState,
    ProcessParticipant,
    ProcessReplyRequest,
    PropertyIntelligenceResponse,
    RuleCreateRequest,
    RuleListResponse,
    RuleResponse,
    RuleUpdateRequest,
    TaskItem,
    TemplateApplyRequest,
    TemplateApplyResponse,
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdateRequest,
    UpsellAnalysisRequest,
    UpsellAnalysisResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Dependencies injected at startup via configure_dependencies()
_deps: dict[str, Any] = {}

_start_time = time.monotonic()


def configure_dependencies(
    cognitive_controller: Any,
    complexity_router: Any,
    llm_router: Any,
    guardrail_pipeline: Any,
    interaction_recorder: Any,
    skill_engine: Any,
    nightly_consolidator: Any,
    monthly_evaluator: Any,
    adaptive_autonomy: Any,
    stakeholder_model: Any,
    memory_system: Any,
    business_classifier: Any | None = None,
    ops_session_manager: Any | None = None,
    durable_pipeline: Any | None = None,
) -> None:
    """Inject all dependencies into the API module.

    Args:
        cognitive_controller: CognitiveController instance.
        complexity_router: ComplexityRouter instance.
        llm_router: LLMRouter instance.
        guardrail_pipeline: GuardrailPipeline instance.
        interaction_recorder: InteractionRecorder instance.
        skill_engine: SkillEvolutionEngine instance.
        nightly_consolidator: NightlyConsolidator instance.
        monthly_evaluator: MonthlyEvaluator instance.
        adaptive_autonomy: AdaptiveAutonomyManager instance.
        stakeholder_model: StakeholderModel instance.
        memory_system: MemorySystem instance.
        business_classifier: BusinessFlagClassifier instance.
        ops_session_manager: OpsSessionManager instance.
        durable_pipeline: DurablePipeline instance (enables checkpointing).
    """
    from brain_engine.approval.knowledge_sync import KnowledgeSyncService

    _deps.update({
        "cognitive": cognitive_controller,
        "complexity": complexity_router,
        "llm": llm_router,
        "guardrails": guardrail_pipeline,
        "recorder": interaction_recorder,
        "skills": skill_engine,
        "consolidator": nightly_consolidator,
        "evaluator": monthly_evaluator,
        "autonomy": adaptive_autonomy,
        "stakeholder": stakeholder_model,
        "memory": memory_system,
        "classifier": business_classifier,
        "ops_sessions": ops_session_manager,
        "durable_pipeline": durable_pipeline,
        "knowledge_sync": KnowledgeSyncService(),
    })


# ═══════════════════════════════════════════════════════════════════════
# GUEST MESSAGE — replaces LangGraph Guest Agent
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/guest/message",
    response_model=BrainEngineResponse,
    tags=["Guest Agent"],
    summary="Process guest message",
    description=(
        "**Replaces Cendra's Guest Agent (LangGraph ReAct).**\n\n"
        "Full cognitive pipeline for every inbound guest message:\n\n"
        "1. **Classify** — 12 business flags (emergency, complaint, maintenance, cleaning, etc.) "
        "via LLM + keyword fallback\n"
        "2. **Route** — Complexity Router determines cognitive depth: "
        "L1 Instinct (FAQ, <500ms) → L4 Strategy (multi-factor critical)\n"
        "3. **Memory Retrieval** — 6-level cognitive memory: working, episodic, semantic, "
        "procedural (learned rules), temporal (decay), knowledge graph\n"
        "4. **LLM Generation** — GPT-4o-mini (L1/L2) or GPT-4o (L3/L4) with full context\n"
        "5. **Guardrails** — 6-layer pipeline + NeuroSymbolic Cascade (94%+ accuracy)\n"
        "6. **Escalation** — Apply agent_config escalation rules (tag → behavior mapping)\n"
        "7. **Task Creation** — Auto-create ops tasks for maintenance/cleaning issues\n"
        "8. **Record** — Store interaction for skill evolution (no training, frozen weights)\n\n"
        "### Response includes:\n"
        "- `reply_text` — AI-generated reply for the guest\n"
        "- `classification` — 12 business flags\n"
        "- `tasks[]` — Ops tasks to create in Cendra\n"
        "- `actions[]` — MCP tool calls for Cendra to execute\n"
        "- `escalation` — Escalation info if human needed\n"
        "- `send_status` — Whether to auto-send or wait for PM review\n"
        "- `is_need_attention` — Flag for PM dashboard\n"
        "- `reasoning_trace` — Full reasoning audit trail\n"
    ),
)
async def handle_guest_message(
    request: GuestMessageRequest,
) -> BrainEngineResponse:
    """Process a guest message through the full cognitive pipeline.

    Uses durable pipeline when checkpointer is available (Redis connected).
    Falls back to direct execution otherwise.
    """
    durable = _deps.get("durable_pipeline")
    if durable:
        logger.info("GUEST_MSG: using DURABLE path")
        from brain_engine.api.durable_guest import (
            process_guest_message_durable,
            set_deps,
        )
        set_deps(_deps)
        return await process_guest_message_durable(request, durable)

    logger.info("GUEST_MSG: using DIRECT path")
    return await _handle_guest_message_direct(request)


async def _handle_guest_message_direct(
    request: GuestMessageRequest,
) -> BrainEngineResponse:
    """Direct (non-durable) guest message processing — fallback."""
    start = time.monotonic()
    guest_id = (
        (request.guest_profile.guest_id if request.guest_profile else "")
        or request.reservation_id
        or ""
    )

    try:
        # Load conversation history from Redis if client didn't send it
        if not request.conversation_history:
            request.conversation_history = await load_conversation_history(
                redis=_deps.get("redis"),
                guest_id=guest_id,
                property_id=request.property_id,
            )

        guest_ctx = await _load_guest_context_direct(guest_id)
        classification = await _classify_message(request)
        level = _route_guest_complexity(request, classification)
        memory_ctx = await _retrieve_memory(request.message, request.property_id)
        response = await _generate_guest_reply(
            request, classification, level, memory_ctx, guest_ctx,
        )
        response = _validate_guest_response(response, request)
        response = _apply_escalation_rules(response, request, classification)
        response = _add_ops_tasks(response, classification, request)
        await _record_guest_interaction(request, response, level, classification)
        await _update_guest_memory_direct(guest_id, request, classification)

        # Save conversation turn to Redis
        await save_conversation_turn(
            redis=_deps.get("redis"),
            guest_id=guest_id,
            property_id=request.property_id,
            user_message=request.message,
            assistant_reply=response.reply_text,
        )

        # Create active process for trackable events
        process = await _create_guest_process(
            request, classification, response,
        )
        if process:
            response.active_process = _build_process_ref(process)

        response.latency_ms = _elapsed_ms(start)
        return response
    except Exception:
        logger.error("Guest message handling failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Processing error")


async def _create_guest_process(
    request: GuestMessageRequest,
    classification: ClassificationFlags,
    response: BrainEngineResponse,
) -> dict[str, Any]:
    """Create an active process for trackable guest interactions.

    Only creates processes for emergencies, complaints, maintenance,
    cleaning issues, and discount requests — not simple FAQ queries.

    Args:
        request: Guest message request.
        classification: Message classification flags.
        response: Generated response.

    Returns:
        Created process dict, or empty dict if not trackable.
    """
    needs_process = (
        classification.is_emergency
        or classification.is_complaint
        or classification.is_maintenance_issue
        or classification.is_cleaning_issue
        or classification.is_security_issue
        or classification.is_discount_request
    )
    if not needs_process:
        return {}

    store = _deps.get("active_process_store")
    if not store:
        return {}

    # Determine process type from classification
    if classification.is_emergency:
        ptype = "emergency"
    elif classification.is_maintenance_issue:
        ptype = "maintenance"
    elif classification.is_cleaning_issue:
        ptype = "cleaning"
    elif classification.is_complaint:
        ptype = "complaint"
    elif classification.is_security_issue:
        ptype = "security"
    else:
        ptype = "guest_request"

    guest_name = request.guest_name or "guest"
    return await store.create(
        process_type=ptype,
        property_id=request.property_id,
        reason=f"{ptype.title()} from {guest_name}: {request.message[:100]}",
        participants=[
            {
                "contact_id": guest_name,
                "role": "guest",
                "status": "waiting_response",
                "last_message": request.message[:200],
                "last_message_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        context={
            "guest_name": guest_name,
            "channel": request.channel or "",
            "original_message": request.message,
            "ai_response": response.reply_text[:200],
            "cognitive_level": response.cognitive_level or "",
            "reservation_id": request.reservation_id or "",
        },
        related_booking=request.reservation_id or "",
    )


async def _classify_message(request: GuestMessageRequest) -> ClassificationFlags:
    """Run business flag classification on the guest message.

    Args:
        request: Guest message request.

    Returns:
        ClassificationFlags with all business flags.
    """
    classifier = _deps.get("classifier")
    if not classifier:
        return ClassificationFlags()

    labels = request.guest_profile.labels if request.guest_profile else []
    result = await classifier.classify(
        message=request.message,
        conversation_history=request.conversation_history,
        guest_labels=labels,
    )

    return ClassificationFlags(
        is_emergency=result.flags.get("IS_EMERGENCY", False),
        is_property_related=result.flags.get("IS_PROPERTY_RELATED", False),
        is_availability_related=result.flags.get("IS_AVAILABILITY_RELATED", False),
        is_reservation_related=result.flags.get("IS_RESERVATION_RELATED", False),
        is_complaint=result.flags.get("IS_COMPLAINT", False),
        is_check_in_out_related=result.flags.get("IS_CHECK_IN_OUT_RELATED", False),
        is_navigation_query=result.flags.get("IS_NAVIGATION_QUERY", False),
        is_discount_request=result.flags.get("IS_DISCOUNT_REQUEST", False),
        is_invoice_request=result.flags.get("IS_INVOICE_REQUEST", False),
        is_cleaning_issue=result.flags.get("IS_CLEANING_ISSUE", False),
        is_maintenance_issue=result.flags.get("IS_MAINTENANCE_ISSUE", False),
        is_noise_complaint=result.flags.get("IS_NOISE_COMPLAINT", False),
        is_security_issue=result.flags.get("IS_SECURITY_ISSUE", False),
    )


def _route_guest_complexity(
    request: GuestMessageRequest, classification: ClassificationFlags,
) -> str:
    """Route guest message to cognitive level based on flags.

    Args:
        request: Guest message request.
        classification: Business flag results.

    Returns:
        Cognitive level string.
    """
    from brain_engine.reasoning.complexity_router import MemoryState

    ctx: dict[str, Any] = {"property_id": request.property_id}
    if classification.is_emergency:
        ctx["urgency"] = "critical"
    if classification.is_complaint:
        ctx["sentiment"] = "negative"
    if request.guest_profile and request.guest_profile.is_vip:
        ctx["guest_score"] = 90

    level = _deps["complexity"].route("guest_message", ctx, MemoryState())
    return level.value


async def _generate_guest_reply(
    request: GuestMessageRequest, classification: ClassificationFlags,
    level: str, memory_ctx: dict[str, Any],
    guest_ctx: str = "",
) -> BrainEngineResponse:
    """Generate LLM reply for the guest.

    Args:
        request: Guest message request.
        classification: Business flags.
        level: Cognitive level.
        memory_ctx: Retrieved memory context.
        guest_ctx: Guest memory context string.

    Returns:
        BrainEngineResponse with reply text.
    """
    from brain_engine.reasoning.complexity_router import CognitiveLevel

    config = _deps["complexity"].get_model_config(CognitiveLevel(level))
    context_text = await _deps["cognitive"].build_full_context(
        query=request.message, entity_ids=[request.property_id],
    )
    if guest_ctx:
        context_text = f"GUEST HISTORY:\n{guest_ctx}\n\n{context_text}"

    system_prompt = _build_guest_system_prompt(request, classification, context_text)
    messages = _build_chat_messages(
        system_prompt, request.conversation_history, request.message,
    )
    llm_response = await _deps["llm"].call(messages, config)

    return BrainEngineResponse(
        reply_text=llm_response.content,
        confidence=0.85,
        cognitive_level=level.upper(),
        classification=classification,
        model_used=llm_response.model_used,
        send_status=True,
        is_need_attention=classification.is_emergency,
    )


def _build_guest_system_prompt(
    request: GuestMessageRequest, classification: ClassificationFlags,
    context_text: str,
) -> str:
    """Build system prompt with config, guardrails, and context.

    Args:
        request: Guest message request.
        classification: Business flags.
        context_text: Memory context text.

    Returns:
        Full system prompt string.
    """
    parts = [
        "You are an AI property manager assistant.",
        "Respond helpfully, concisely, and accurately.",
        "NEVER invent information not provided in the context below.",
    ]

    if request.agent_config:
        if request.agent_config.custom_instructions:
            parts.append(f"\nInstructions:\n{request.agent_config.custom_instructions}")
        parts.append(f"\nTone: {request.agent_config.tone_type}")
        for rule in request.agent_config.guardrails:
            if _guardrail_applies(rule, classification, request):
                parts.append(f"\nGUARDRAIL: {rule.rule_text}")

    if classification.is_navigation_query:
        parts.append(_build_navigation_context(request))

    if request.property_summary:
        parts.append(f"\nProperty:\n{request.property_summary}")
    if context_text:
        parts.append(f"\nKnowledge:\n{context_text}")
    if request.booking_status:
        parts.append(f"\nBooking status: {request.booking_status}")
    if request.agent_config and request.agent_config.signature:
        parts.append(f"\nSign messages with: {request.agent_config.signature}")

    return "\n".join(parts)


def _build_navigation_context(request: GuestMessageRequest) -> str:
    """Build navigation context when guest needs directions.

    Provides GPS coordinates, address, and Google Maps link
    so LLM generates directions instead of irrelevant info.

    Args:
        request: Guest message request with property_location.

    Returns:
        Navigation instruction block for system prompt.
    """
    parts = [
        "\n⚠️ NAVIGATION QUERY DETECTED — The guest is asking for DIRECTIONS "
        "or cannot find the property. Do NOT suggest WiFi, amenities, or "
        "unrelated info. Provide the property ADDRESS, DIRECTIONS, and a "
        "Google Maps link.",
    ]

    loc = request.property_location
    if loc:
        if loc.address:
            parts.append(f"Property address: {loc.address}")
        if loc.city:
            parts.append(f"City: {loc.city}, {loc.country}")
        maps_url = loc.google_maps_url()
        if maps_url:
            parts.append(f"Google Maps: {maps_url}")
        if loc.latitude is not None and loc.longitude is not None:
            parts.append(f"GPS: {loc.latitude}, {loc.longitude}")
        if loc.entrance_instructions:
            parts.append(f"Entrance: {loc.entrance_instructions}")
    elif request.property_summary:
        parts.append(
            "Use the property address from the property summary below "
            "to provide directions."
        )

    return "\n".join(parts)


def _guardrail_applies(
    rule: Any, classification: ClassificationFlags,
    request: GuestMessageRequest,
) -> bool:
    """Check if a guardrail rule should be applied.

    Args:
        rule: GuardrailRule.
        classification: Business flags.
        request: Guest message request.

    Returns:
        True if the rule applies.
    """
    if rule.priority == "always":
        return True
    if rule.priority == "contextual" and rule.trigger_flags:
        flags_dict = classification.model_dump() if classification else {}
        return any(flags_dict.get(f.lower(), False) for f in rule.trigger_flags)
    if rule.priority == "label_based" and rule.trigger_labels:
        labels = request.guest_profile.labels if request.guest_profile else []
        return any(label in labels for label in rule.trigger_labels)
    return False


def _validate_guest_response(
    response: BrainEngineResponse, request: GuestMessageRequest,
) -> BrainEngineResponse:
    """Run guardrail pipeline on the response.

    Args:
        response: Generated response.
        request: Original request.

    Returns:
        Validated response.
    """
    result = _deps["guardrails"].validate_response(
        response.reply_text,
        context={"property_id": request.property_id},
        knowledge_base=request.property_summary,
    )
    if result.cleaned_response != response.reply_text:
        response.reply_text = result.cleaned_response
    if not result.passed:
        response.is_need_attention = True
        response.send_status = False
    return response


def _apply_escalation_rules(
    response: BrainEngineResponse, request: GuestMessageRequest,
    classification: ClassificationFlags,
) -> BrainEngineResponse:
    """Apply escalation rules from agent config.

    Args:
        response: Current response.
        request: Original request.
        classification: Business flags.

    Returns:
        Response with escalation flags updated.
    """
    if not request.agent_config or not request.agent_config.escalations:
        response.message_tags = _get_active_tags(classification)
        return response

    active_tags = _get_active_tags(classification)
    for rule in request.agent_config.escalations:
        if rule.tag not in active_tags:
            continue
        if rule.behavior == "escalate":
            response.is_need_attention = True
            response.send_status = False
        elif rule.behavior == "answer_and_escalate":
            response.is_need_attention = True

    response.message_tags = active_tags
    return response


def _get_active_tags(classification: ClassificationFlags) -> list[str]:
    """Convert active flags to tag strings.

    Args:
        classification: Business flags.

    Returns:
        List of active tag strings.
    """
    flags = classification.model_dump() if classification else {}
    return [k.upper() for k, v in flags.items() if v]


def _add_ops_tasks(
    response: BrainEngineResponse, classification: ClassificationFlags,
    request: GuestMessageRequest,
) -> BrainEngineResponse:
    """Create ops tasks if classification requires operational action.

    Args:
        response: Current response.
        classification: Business flags.
        request: Original request.

    Returns:
        Response with tasks added.
    """
    msg = request.message[:200]

    if classification.is_cleaning_issue:
        response.tasks.append(TaskItem(
            task="Schedule cleaning", description=f"Guest: {msg}",
            main_category="Cleaning and Hygiene",
            sub_category="Cleanliness Complaints",
            tags=["cleaning", "urgent" if classification.is_complaint else "normal"],
        ))
    if classification.is_maintenance_issue:
        response.tasks.append(TaskItem(
            task="Arrange maintenance", description=f"Guest: {msg}",
            main_category="Maintenance", sub_category="Repair Request",
            tags=["maintenance"],
        ))
    if classification.is_security_issue:
        response.tasks.append(TaskItem(
            task="Handle security issue", description=f"Guest: {msg}",
            main_category="Security", sub_category="Security Issue",
            tags=["security", "urgent"], priority="high",
        ))
    if classification.is_noise_complaint:
        response.tasks.append(TaskItem(
            task="Address noise complaint", description=f"Guest: {msg}",
            main_category="Guest Experience", sub_category="Noise Complaint",
            tags=["noise"],
        ))

    return response


async def _record_guest_interaction(
    request: GuestMessageRequest, response: BrainEngineResponse,
    level: str, classification: ClassificationFlags,
) -> None:
    """Record guest interaction for skill evolution.

    Args:
        request: Original request.
        response: Generated response.
        level: Cognitive level used.
        classification: Business flags.
    """
    from brain_engine.continual_learning.interaction_recorder import BrainEngineInteraction

    await _deps["recorder"].record(BrainEngineInteraction(
        event_type="guest_message",
        input_message=request.message,
        context={"property_id": request.property_id, "channel": request.channel,
                 "flags": classification.model_dump() if classification else {}},
        output_response=response.reply_text,
        output_actions=[a.model_dump() for a in response.actions],
        confidence=response.confidence, cognitive_level=level,
        property_id=request.property_id,
    ))


# ═══════════════════════════════════════════════════════════════════════
# OPS EVENT — replaces OpsSessionWorkflow (963 lines)
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/ops/event",
    response_model=BrainEngineResponse,
    tags=["Ops Agent"],
    summary="Create ops session and dispatch first contact",
    description=(
        "**Replaces Cendra's OpsSessionWorkflow (963 lines of if/else).**\n\n"
        "When a guest reports an issue (e.g., 'the place is dirty'), this endpoint:\n\n"
        "1. **Creates OpsSession** — tracks the entire resolution lifecycle\n"
        "2. **Detects duplicates** — prevents parallel sessions for same property+category\n"
        "3. **Detects recurring issues** (Scenario 10) — flags if same issue happened recently\n"
        "4. **Dispatches first contact** — picks first contact from cascade and sends WhatsApp\n\n"
        "### Contact Cascade:\n"
        "Ordered list of contacts to try. If first declines or doesn't reply, "
        "Brain Engine auto-dispatches the next via `/ops/contact-reply`.\n\n"
        "### Response includes:\n"
        "- `ops_session` — session state (id, status, current contact, contacts remaining)\n"
        "- `actions[]` — MCP tool calls (sendWhatsApp to first contact)\n"
        "- `escalation` — if no contacts available, escalates to PM\n"
    ),
)
async def handle_ops_event(request: OpsEventRequest) -> BrainEngineResponse:
    """Process ops event and initiate contact cascade."""
    start = time.monotonic()
    try:
        session = await _create_ops_session(request)
        dispatch = await _deps["ops_sessions"].dispatch_next_contact(session.session_id)
        response = _build_ops_response(request, session, dispatch)

        # Create active process for ops tracking
        process = await _create_ops_process(request, session)
        response.active_process = _build_process_ref(process)

        response.latency_ms = _elapsed_ms(start)
        return response
    except Exception:
        logger.error("Ops event handling failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Processing error")


async def _create_ops_process(
    request: OpsEventRequest,
    session: Any,
) -> dict[str, Any]:
    """Create an active process for an ops event.

    Args:
        request: Ops event request.
        session: Created OpsSession.

    Returns:
        Created process dict.
    """
    store = _deps.get("active_process_store")
    if not store:
        return {}
    participants = [
        {
            "contact_id": c.contact_id,
            "role": c.contact_type if hasattr(c, "contact_type") else "contact",
            "status": "waiting",
            "last_message": "",
            "last_message_at": "",
        }
        for c in (request.contacts or [])
    ]
    return await store.create(
        process_type=request.event_type,
        property_id=request.property_id,
        reason=request.description or f"Ops event: {request.event_type}",
        participants=participants,
        context={
            "session_id": session.session_id if session else "",
            "event_type": request.event_type,
            "category": request.category or "",
            "reservation_id": request.reservation_id or "",
        },
        related_booking=request.reservation_id or "",
    )


async def _create_ops_session(request: OpsEventRequest) -> Any:
    """Create OpsSession from request.

    Args:
        request: Ops event request.

    Returns:
        Created OpsSession.
    """
    ops_mgr = _deps.get("ops_sessions")
    if not ops_mgr:
        raise HTTPException(status_code=503, detail="OpsSessionManager not available")

    return await ops_mgr.create_session(
        property_id=request.property_id,
        event_type=request.event_type,
        category=request.category or request.event_type,
        description=request.description,
        contact_cascade=[c.contact_id for c in request.contacts],
        cost_threshold=request.cost_threshold,
        reservation_id=request.reservation_id or "",
        subcategory=request.subcategory, tags=request.tags,
    )


def _build_ops_response(
    request: OpsEventRequest, session: Any, dispatch: dict[str, Any],
) -> BrainEngineResponse:
    """Build response from session and dispatch result.

    Args:
        request: Original request.
        session: OpsSession.
        dispatch: Dispatch action dict.

    Returns:
        BrainEngineResponse.
    """
    actions = [MCPAction(tool=t["tool"], params=t.get("params", {}))
               for t in dispatch.get("mcp_tools", [])]

    contact_id = dispatch.get("contact_id", "")
    if dispatch.get("action") == "contact" and contact_id:
        from brain_engine.api.mcp_tools import MCPToolFormatter
        wa = MCPToolFormatter.send_whatsapp(
            contact_id=contact_id,
            message=f"Hi, we need help with {request.category.lower()}: {request.description}. Available?",
        )
        actions.append(MCPAction(tool=wa.tool, params=wa.params))

    escalation = None
    if dispatch.get("action") == "escalate_to_pm":
        escalation = EscalationInfo(
            reason=dispatch.get("reason", "No contacts"), escalation_type="no_contacts",
            severity="high", suggested_action="Find and assign a contact",
        )

    return BrainEngineResponse(
        reply_text=f"Ops session {session.session_id}: {dispatch.get('action', '')}",
        confidence=0.9, cognitive_level="L2", actions=actions,
        ops_session=OpsSessionState(
            session_id=session.session_id, status=session.status,
            current_contact=contact_id,
            contacts_tried=session.current_contact_index,
            contacts_remaining=len(session.contact_cascade) - session.current_contact_index,
            is_recurring=session.is_recurring,
        ),
        escalation=escalation,
        requires_approval=dispatch.get("action") == "request_pm_approval",
        message_tags=request.tags, is_need_attention=escalation is not None,
    )


# ═══════════════════════════════════════════════════════════════════════
# CONTACT REPLY — replaces Cleaner/Vendor Agents
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/ops/contact-reply",
    response_model=BrainEngineResponse,
    tags=["Ops Agent"],
    summary="Process contact reply (10 scenarios)",
    description=(
        "**Replaces Cendra's Cleaner/Vendor Agent multi-turn logic.**\n\n"
        "Handles ALL 10 edge cases from the Cendra ops case study:\n\n"
        "| # | Scenario | Brain Engine Action |\n"
        "|---|----------|--------------------|\n"
        "| 1 | **Confirmed** | Mark resolved, notify guest |\n"
        "| 2 | **Declined + alternative** | Create new contact, dispatch |\n"
        "| 3 | **Cost quoted** | Auto-approve if under threshold, else escalate to PM |\n"
        "| 4 | **No reply** | Send follow-up, then try next in cascade |\n"
        "| 5 | **Ambiguous** | Ask for clarification |\n"
        "| 6 | **Multi-issue** | Split into separate sessions |\n"
        "| 7 | **No contacts left** | Escalate to PM |\n"
        "| 8 | **Voice/image** | Ask for text reply |\n"
        "| 9 | **Late reply** | Check if still needed, acknowledge |\n"
        "| 10 | **Recurring** | Flag and suggest different approach |\n\n"
        "### Reply Classification:\n"
        "Set `reply_classification` to: `confirmed`, `declined`, `cost_quoted`, "
        "`ambiguous`, `no_reply`, or `unprocessable`. If omitted, Brain Engine auto-detects.\n\n"
        "### Cost Negotiation (Scenario 3):\n"
        "When `reply_classification=cost_quoted`, include `cost_amount` and `cost_currency`. "
        "Brain Engine compares against the session's `cost_threshold` and auto-approves "
        "or escalates to PM via `/approval/decision`.\n"
    ),
)
async def handle_contact_reply(request: ContactReplyRequest) -> BrainEngineResponse:
    """Process a contact reply (all 10 edge cases)."""
    start = time.monotonic()
    try:
        reply_type = _determine_reply_type(request)
        result = await _deps["ops_sessions"].process_reply(
            session_id=request.session_id, contact_id=request.contact_id,
            message=request.message, reply_classification=reply_type,
            cost_amount=request.cost_amount, cost_currency=request.cost_currency,
            alternative_contact=request.alternative_contact, eta_minutes=request.eta_minutes,
        )
        response = _build_contact_reply_response(request, result, reply_type)
        await _record_contact_interaction(request, response, reply_type)
        response.latency_ms = _elapsed_ms(start)
        return response
    except Exception:
        logger.error("Contact reply handling failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Processing error")


def _determine_reply_type(request: ContactReplyRequest) -> str:
    """Determine the type of contact reply.

    Args:
        request: Contact reply request.

    Returns:
        Reply type string.
    """
    if request.reply_classification:
        return request.reply_classification
    if request.is_voice_note or request.is_image:
        return "unprocessable"
    if request.cost_amount is not None:
        return "cost_quoted"
    if request.alternative_contact:
        return "declined"
    if not request.message.strip():
        return "no_reply"
    return "ambiguous"


def _build_contact_reply_response(
    request: ContactReplyRequest, result: dict[str, Any], reply_type: str,
) -> BrainEngineResponse:
    """Build response from session manager result.

    Args:
        request: Contact reply request.
        result: Action dict from OpsSessionManager.
        reply_type: Reply type.

    Returns:
        BrainEngineResponse.
    """
    actions = [MCPAction(tool=t["tool"], params=t.get("params", {}))
               for t in result.get("mcp_tools", [])]

    escalation = None
    if result.get("action") == "escalate_to_pm":
        escalation = EscalationInfo(
            reason=result.get("reason", "All contacts exhausted"),
            escalation_type="no_contacts", severity="high",
        )

    requires_approval = result.get("action") == "request_pm_approval"
    approval_request = None
    if requires_approval:
        approval_request = {
            "cost": result.get("cost"), "currency": result.get("currency"),
            "threshold": result.get("threshold"), "session_id": request.session_id,
        }

    return BrainEngineResponse(
        reply_text=result.get("message_to_guest", f"Contact reply: {result.get('action', '')}"),
        confidence=0.85, cognitive_level="L2", actions=actions,
        escalation=escalation, requires_approval=requires_approval,
        approval_request=approval_request,
        ops_session=OpsSessionState(session_id=request.session_id, status=result.get("action", "")),
        message_tags=[reply_type],
        is_need_attention=escalation is not None or requires_approval,
        send_status=result.get("session_resolved", False),
    )


async def _record_contact_interaction(
    request: ContactReplyRequest, response: BrainEngineResponse, reply_type: str,
) -> None:
    """Record contact reply interaction.

    Args:
        request: Contact reply request.
        response: Generated response.
        reply_type: Reply classification.
    """
    from brain_engine.continual_learning.interaction_recorder import BrainEngineInteraction

    await _deps["recorder"].record(BrainEngineInteraction(
        event_type=f"contact_reply_{reply_type}", input_message=request.message,
        context={"session_id": request.session_id, "contact_id": request.contact_id,
                 "contact_type": request.contact_type, "reply_type": reply_type},
        output_response=response.reply_text,
        output_actions=[a.model_dump() for a in response.actions],
        confidence=response.confidence, cognitive_level="L2",
        property_id=request.property_id,
    ))


# ── PM Correction helpers ──────────────────────────────────────────── #


def _handle_pm_correction(
    request: ApprovalDecisionRequest,
    actions: list[MCPAction],
) -> list[MCPAction]:
    """Обработать PM-коррекцию и дополнить список actions.

    Вызывается из ``handle_approval_decision`` когда
    ``decision_type == "modified"`` и ``pm_correction`` не пусто.
    Создаёт ``addKnowledgeEntry`` action и логирует аудит.
    Skills-обновление — fire-and-forget, ошибки логируются но не
    пробрасываются.

    Args:
        request: Оригинальный запрос с полями PM-коррекции.
        actions: Уже собранный список MCP-действий (OPS и т.д.).

    Returns:
        Расширенный список actions с добавленным KB-action (если создан).
    """
    from brain_engine.approval.knowledge_sync import KnowledgeSyncService

    ks: KnowledgeSyncService = _deps.get("knowledge_sync") or KnowledgeSyncService()

    # 1. Создать KB action.
    kb_action = ks.create_mcp_action_for_correction(
        guest_message=request.guest_message or "",
        pm_answer=request.pm_correction or "",
        property_id=request.property_id,
    )
    if kb_action:
        actions.append(MCPAction(
            tool=kb_action.tool,
            params=kb_action.params,
        ))

    # 2. Аудит-лог.
    ks.log_correction(
        request_id=request.request_id,
        owner_id=request.owner_id,
        property_id=request.property_id,
        guest_message=request.guest_message or "",
        original_ai_response=request.original_ai_response or "",
        pm_correction=request.pm_correction or "",
    )

    # 3. Skills — зафиксировать ошибку AI для обучения (fire-and-forget).
    _schedule_skills_correction(request)

    return actions


def _schedule_skills_correction(request: ApprovalDecisionRequest) -> None:
    """Fire-and-forget вызов skills.evolve_on_failure для PM-коррекции."""
    import asyncio

    skills = _deps.get("skills")
    if skills is None:
        return

    async def _evolve() -> None:
        try:
            await skills.evolve_on_failure(
                event_type="pm_correction",
                event_description=f"PM corrected AI for request {request.request_id}",
                action_taken=request.original_ai_response or "",
                failure_reason=request.pm_correction or "",
                context={
                    "request_id": request.request_id,
                    "owner_id": request.owner_id,
                    "property_id": request.property_id,
                },
            )
        except Exception as exc:  # noqa: BLE001 — fire-and-forget
            logger.warning("Skills PM correction evolve failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_evolve())
    except RuntimeError:
        pass  # нет running loop — пропускаем


# ═══════════════════════════════════════════════════════════════════════
# APPROVAL DECISION — learns from owner/PM decisions
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/approval/decision",
    response_model=BrainEngineResponse,
    tags=["Learning"],
    summary="Process owner/PM approval decision and learn from it",
    description=(
        "**Every approval/rejection teaches Brain Engine.**\n\n"
        "When a property manager approves or rejects an AI suggestion:\n\n"
        "1. **Autonomy Tracking** — Records decision for L1→L4 progression. "
        "After 20+ approvals with 85%+ success rate, Brain Engine upgrades from "
        "L1 (Suggest) to L2 (Act & Inform) — auto-sending messages without PM review.\n"
        "2. **Ops Session** — If linked to an ops session, processes PM decision "
        "(approve cost quote → confirm with vendor, reject → try next contact).\n"
        "3. **Skill Evolution** — On rejection, triggers READ→REFLECT→WRITE→VERIFY cycle: "
        "LLM analyzes WHY the action was wrong and updates procedural memory. "
        "Next time, Brain Engine avoids the same mistake. **No training/fine-tuning — "
        "frozen weights, only external memory evolves.**\n\n"
        "### Progression Thresholds:\n"
        "- L1→L2: 20 decisions, 85% success\n"
        "- L2→L3: 50 decisions, 90% success\n"
        "- L3→L4: 100 decisions, 95% success\n"
        "- Demotion: >20% override rate → back to L1\n"
    ),
)
async def handle_approval_decision(request: ApprovalDecisionRequest) -> BrainEngineResponse:
    """Process approval and learn from it."""
    start = time.monotonic()
    try:
        await _deps["autonomy"].record_decision(
            owner_id=request.owner_id, property_id=request.property_id,
            success=request.approved, owner_overrode=not request.approved,
            override_reason=request.reason,
        )

        ops_result = None
        if request.session_id and _deps.get("ops_sessions"):
            ops_result = await _deps["ops_sessions"].process_pm_decision(
                session_id=request.session_id, approved=request.approved,
                reason=request.reason,
            )

        if not request.approved:
            await _deps["skills"].evolve_on_failure(
                event_type="approval_rejected",
                event_description=f"Request {request.request_id} rejected",
                action_taken=f"Proposed action for {request.request_id}",
                failure_reason=request.reason or "Rejected without reason",
                context={"request_id": request.request_id, "owner_id": request.owner_id,
                         "property_id": request.property_id},
            )

        from brain_engine.continual_learning.interaction_recorder import BrainEngineInteraction
        await _deps["recorder"].record(BrainEngineInteraction(
            event_type="approval_decision",
            input_message=f"{'Approved' if request.approved else 'Rejected'}: {request.request_id}",
            context={"request_id": request.request_id, "decision_type": request.decision_type},
            output_response=request.reason, confidence=1.0, cognitive_level="L1",
            property_id=request.property_id,
            owner_approved=request.approved, owner_intervened=not request.approved,
        ))

        actions = [MCPAction(tool=t["tool"], params=t.get("params", {}))
                   for t in (ops_result or {}).get("mcp_tools", [])]

        # PM Correction Hook: PM отредактировал ответ AI → создаём
        # KB-action (addKnowledgeEntry) + тренируем skills через
        # evolve_on_failure("pm_correction").
        if request.decision_type == "modified" and request.pm_correction:
            actions = _handle_pm_correction(request, actions)

        status = "approved" if request.approved else "rejected"
        response = BrainEngineResponse(
            reply_text=f"Decision: {status}. {request.reason}",
            confidence=1.0, cognitive_level="L1", actions=actions,
            message_tags=["approval", status],
        )
        response.latency_ms = _elapsed_ms(start)
        return response
    except Exception:
        logger.error("Approval handling failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Processing error")


# ═══════════════════════════════════════════════════════════════════════
# IoT EVENT PROCESSING — Seam device events → automation + anomaly
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/iot/event",
    response_model=BrainEngineResponse,
    tags=["IoT"],
    summary="Process IoT device event (smart locks, thermostats, sensors)",
    description=(
        "**Processes Seam webhook events from IoT devices.**\n\n"
        "1. **Anomaly detection** — unexpected unlock during vacancy, temp spike\n"
        "2. **Automation routing** — forwards to 9-template automation engine\n"
        "3. **Alerts** — PM notification for anomalies\n"
    ),
)
async def handle_iot_event(request: OpsEventRequest) -> BrainEngineResponse:
    """Process IoT device event with anomaly detection and automation."""
    start = time.monotonic()
    try:
        result = await _process_iot_event(request)
        await _record_iot_event(request, result)
        return _build_iot_response(result, start)
    except Exception:
        logger.error("IoT event handling failed", exc_info=True)
        raise HTTPException(status_code=500, detail="IoT processing error")


async def _process_iot_event(request: OpsEventRequest) -> Any:
    """Run IoT event through processor with deps-injected automation.

    Args:
        request: Ops event request with IoT data.

    Returns:
        IoTProcessingResult.
    """
    from brain_engine.smart_engine.iot_processor import IoTEvent, IoTProcessor

    processor = _deps.get("iot_processor") or IoTProcessor()
    event = IoTEvent(
        device_id=request.event_data.get("device_id", ""),
        device_type=request.event_data.get("device_type", "smart_lock"),
        event_type=request.event_type,
        property_id=request.property_id,
        data=request.event_data,
    )
    return processor.process(event)


async def _record_iot_event(request: OpsEventRequest, result: Any) -> None:
    """Record IoT event for learning.

    Args:
        request: Original request.
        result: Processing result.
    """
    recorder = _deps.get("recorder")
    if not recorder:
        return

    from brain_engine.continual_learning.interaction_recorder import BrainEngineInteraction
    await recorder.record(BrainEngineInteraction(
        event_type=f"iot_{request.event_type}",
        input_message=f"IoT: {request.event_type} at {request.property_id}",
        context={"device_type": request.event_data.get("device_type", ""),
                 "is_anomaly": result.is_anomaly},
        output_response=result.anomaly_reason or "normal",
        confidence=0.9 if not result.is_anomaly else 0.7,
        cognitive_level="L1",
        property_id=request.property_id,
    ))


def _build_iot_response(result: Any, start: float) -> BrainEngineResponse:
    """Build response from IoT processing result.

    Args:
        result: IoTProcessingResult.
        start: Start time.

    Returns:
        BrainEngineResponse.
    """
    actions = []
    matched_rules: list[str] = []
    if result.automation_result:
        actions = [
            MCPAction(tool=a.tool, params=a.params)
            for a in result.automation_result.actions
        ]
        matched_rules = result.automation_result.matched_rules

    confidence = 0.7 if result.is_anomaly else 0.9
    level = "L2" if result.is_anomaly else "L1"

    return BrainEngineResponse(
        reply_text=result.anomaly_reason or f"IoT event {result.event_type} processed",
        confidence=confidence,
        cognitive_level=level,
        actions=actions,
        is_need_attention=result.is_anomaly,
        message_tags=result.alerts + matched_rules,
        latency_ms=_elapsed_ms(start),
    )


# ═══════════════════════════════════════════════════════════════════════
# AUTOMATION — event-driven property automations (9 templates)
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/automation/process",
    response_model=BrainEngineResponse,
    tags=["Automation"],
    summary="Process event through automation rules (9 templates)",
    description=(
        "**9 event-driven automation templates:**\n\n"
        "1. Guest Access Code (booking_confirmed)\n"
        "2. Check-In Prep (60min before check_in)\n"
        "3. Pre-Heat/Cool (30min before check_in)\n"
        "4. Check-Out Cleanup (10min after check_out)\n"
        "5. HVAC Off (30min after check_out)\n"
        "6. Cancel → Revoke (booking_cancelled)\n"
        "7. Stay Extension (booking_modified)\n"
        "8. Vacant Door Alert (lock.unlocked)\n"
        "9. Between Bookings Away (check_out, no next)\n"
    ),
)
async def handle_automation_event(request: OpsEventRequest) -> BrainEngineResponse:
    """Process event through automation rules engine."""
    start = time.monotonic()
    try:
        result = _run_automation(request)
        await _record_automation(request, result)
        return _build_automation_response(result, start)
    except Exception:
        logger.error("Automation processing failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Automation error")


def _run_automation(request: OpsEventRequest) -> Any:
    """Execute automation rules against event.

    Args:
        request: Ops event request.

    Returns:
        AutomationResult.
    """
    from brain_engine.smart_engine.automation_rules import (
        AutomationEngine,
        AutomationEvent,
    )

    engine = _deps.get("automation_engine")
    if not engine:
        from brain_engine.smart_engine.automation_rules import AutomationEngine
        engine = AutomationEngine()
        logger.warning("AutomationEngine not in _deps — using unmanaged instance")
    event = AutomationEvent(
        event_type=request.event_type,
        property_id=request.property_id,
        reservation_id=request.reservation_id or "",
        event_data=request.event_data,
        minutes_offset=int(request.event_data.get("minutes_offset", 0)),
    )
    return engine.process(event)


async def _record_automation(request: OpsEventRequest, result: Any) -> None:
    """Record automation event for skill evolution.

    Args:
        request: Original request.
        result: Automation result.
    """
    recorder = _deps.get("recorder")
    if not recorder:
        return

    from brain_engine.continual_learning.interaction_recorder import BrainEngineInteraction
    await recorder.record(BrainEngineInteraction(
        event_type=f"automation_{request.event_type}",
        input_message=f"Automation: {request.event_type}",
        context={"matched_rules": result.matched_rules,
                 "actions_count": len(result.actions)},
        output_response=f"{len(result.matched_rules)} rules, {len(result.actions)} actions",
        confidence=0.9,
        cognitive_level="L1",
        property_id=request.property_id,
    ))


def _build_automation_response(result: Any, start: float) -> BrainEngineResponse:
    """Build response from automation result.

    Args:
        result: AutomationResult.
        start: Start time.

    Returns:
        BrainEngineResponse.
    """
    actions = [
        MCPAction(tool=a.tool, params=a.params)
        for a in result.actions
    ]

    has_security = any(
        "alert" in r.lower() or "security" in r.lower()
        for r in result.matched_rules
    )

    return BrainEngineResponse(
        reply_text=f"{len(result.matched_rules)} automation rules executed",
        confidence=0.9,
        cognitive_level="L2" if has_security else "L1",
        actions=actions,
        message_tags=result.matched_rules,
        is_need_attention=has_security,
        latency_ms=_elapsed_ms(start),
    )


# ═══════════════════════════════════════════════════════════════════════
# UPSELL — auto-detect revenue opportunities
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/upsell/analyze",
    response_model=UpsellAnalysisResponse,
    tags=["Upsell"],
    summary="Analyze booking for upsell opportunities",
    description=(
        "**Detects 4 types of upsell opportunities:**\n\n"
        "1. **Gap Night** — fill empty nights between bookings at discount\n"
        "2. **Early Check-in** — offer early access (if no same-day checkout)\n"
        "3. **Late Check-out** — extend past checkout (free for VIP guests)\n"
        "4. **Late Check-in** — accommodate late arrivals with access codes\n\n"
        "### Auto-offer rules:\n"
        "- Guest score ≥ 60: auto-offer early check-in, gap night\n"
        "- Guest score ≥ 80: auto-offer free late checkout\n"
        "- Late check-in: always auto (no fee)\n"
    ),
)
async def handle_upsell_analysis(
    request: UpsellAnalysisRequest,
) -> UpsellAnalysisResponse:
    """Analyze booking for upsell opportunities."""
    from brain_engine.smart_engine.upsell_engine import (
        BookingContext,
        UpsellEngine,
    )

    engine = UpsellEngine()
    context = BookingContext(
        reservation_id=request.reservation_id,
        property_id=request.property_id,
        checkin_date=request.checkin_date,
        checkout_date=request.checkout_date,
        checkin_time=request.checkin_time,
        checkout_time=request.checkout_time,
        nightly_rate=request.nightly_rate,
        guest_score=request.guest_score,
        num_guests=request.num_guests,
        next_booking_checkin=request.next_booking_checkin,
        prev_booking_checkout=request.prev_booking_checkout,
    )

    result = engine.analyze(context)

    actions = [
        MCPAction(tool=a["tool"], params=a.get("params", {}))
        for a in result.actions
    ]

    return UpsellAnalysisResponse(
        reservation_id=result.reservation_id,
        property_id=result.property_id,
        guest_score=result.guest_score,
        offers=result.to_dict()["offers"],
        total_revenue_potential=result.total_revenue_potential,
        message_to_guest=result.message_to_guest,
        actions=actions,
    )


# ═══════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE SYNC — bidirectional Cendra KB ↔ Brain Engine
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/knowledge/sync",
    response_model=KnowledgeSyncResponse,
    tags=["Knowledge"],
    summary="Sync Cendra Knowledge Base with Brain Engine SemanticMemory",
    description=(
        "**Bidirectional KB synchronization.**\n\n"
        "### Import (Cendra → Brain Engine):\n"
        "Imports Cendra KB entries into SemanticMemory (Qdrant) for RAG.\n\n"
        "### Export (Brain Engine → Cendra):\n"
        "Exports learned facts from SemanticMemory back to Cendra KB.\n\n"
        "### Knowledge Candidates:\n"
        "Auto-approves candidates with confidence ≥ 0.75. Candidates below "
        "threshold are left for PM review.\n\n"
        "### Conflict Resolution:\n"
        "Resolves conflicts between existing and new knowledge entries.\n"
    ),
)
async def handle_knowledge_sync(
    request: KnowledgeSyncRequest,
) -> KnowledgeSyncResponse:
    """Sync knowledge base between Cendra and Brain Engine."""
    try:
        imported = await _import_kb_entries(request)

        # MIRA Auto-Approver вместо простого threshold из старого
        # _process_kb_candidates: теперь учитывает red_flags,
        # PM_Correction override, и генерит MCP approveKnowledgeCandidate.
        mira_result = await _process_candidates_via_mira(request)

        resolved = _resolve_kb_conflicts(request)
        export_actions = await _export_learned_knowledge(request)

        # Добавляем MCP-действия MIRA к export-actions.
        from brain_engine.api.models import MCPAction as _MCPAction
        for mcp in mira_result.mcp_actions:
            export_actions.append(_MCPAction(
                tool=mcp.tool, params=mcp.params,
            ))

        return KnowledgeSyncResponse(
            imported_count=imported,
            candidates_approved=mira_result.approved_count,
            candidates_rejected=mira_result.skipped_count,
            conflicts_resolved=resolved,
            actions=export_actions,
        )
    except Exception:
        logger.error("Knowledge sync failed", exc_info=True)
        return KnowledgeSyncResponse(errors=["Sync failed"])


async def _import_kb_entries(request: KnowledgeSyncRequest) -> int:
    """Import Cendra KB entries into SemanticMemory.

    Args:
        request: Sync request with entries.

    Returns:
        Number of entries imported.
    """
    if not request.entries:
        return 0

    semantic = _deps["memory"].semantic
    count = 0
    for entry in request.entries:
        if not entry.is_active or not entry.content:
            continue
        text = f"{entry.title}\n{entry.content}" if entry.title else entry.content
        metadata = {
            "source": "cendra_kb",
            "entry_id": entry.entry_id,
            "property_id": entry.property_id or request.property_id,
            "category": entry.category,
        }
        import uuid as _uuid
        deterministic_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"kb_{entry.entry_id}"))
        await semantic.store(
            text=text,
            metadata=metadata,
            record_id=deterministic_id,
        )
        count += 1

    return count


async def _process_candidates_via_mira(
    request: KnowledgeSyncRequest,
) -> Any:
    """Прогнать кандидатов через MIRA Auto-Approver.

    Args:
        request: Sync request с кандидатами.

    Returns:
        :class:`~brain_engine.approval.mira.MIRAResult`.
    """
    from brain_engine.approval.mira import MIRAAutoApprover, MIRAResult

    if not request.candidates:
        return MIRAResult()

    approver = MIRAAutoApprover()
    candidates_dicts = [
        {
            "candidate_id": c.candidate_id,
            "question": c.question,
            "answer": c.answer,
            "confidence": c.confidence,
            "source": c.source,
            "property_id": c.property_id or request.property_id,
            "red_flags": c.red_flags,
        }
        for c in request.candidates
    ]

    semantic = _deps["memory"].semantic if _deps.get("memory") else None
    return await approver.process_candidates(
        candidates_dicts,
        semantic_memory=semantic,
    )


def _resolve_kb_conflicts(request: KnowledgeSyncRequest) -> int:
    """Resolve knowledge base conflicts.

    Args:
        request: Sync request with conflicts.

    Returns:
        Number of conflicts resolved.
    """
    if not request.conflicts:
        return 0

    resolved = 0
    for conflict in request.conflicts:
        if conflict.resolution in ("keep_existing", "use_new", "merge"):
            resolved += 1

    return resolved


async def _export_learned_knowledge(
    request: KnowledgeSyncRequest,
) -> list[MCPAction]:
    """Export learned facts from SemanticMemory back to Cendra KB.

    Args:
        request: Sync request.

    Returns:
        List of MCP actions to create KB entries in Cendra.
    """
    if request.direction not in ("export", "bidirectional"):
        return []

    from brain_engine.api.mcp_tools import MCPToolFormatter

    semantic = _deps["memory"].semantic
    results = await semantic.search(
        query="learned fact",
        top_k=20,
        metadata_filter={"source": "episodic_promotion"},
    )

    actions: list[MCPAction] = []
    for result in results:
        tool = MCPToolFormatter.add_knowledge_entry(
            title=result.get("text", "")[:100],
            content=result.get("text", ""),
            property_id=result.get("metadata", {}).get("property_id", ""),
            category="AI Learned",
        )
        actions.append(MCPAction(tool=tool.tool, params=tool.params))

    return actions


# ═══════════════════════════════════════════════════════════════════════
# MIRA — endpoints для ручного триггера и просмотра статистики
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/mira/process",
    tags=["Knowledge"],
    summary="Trigger MIRA Auto-Approver on a batch of candidates",
    description=(
        "Принимает список dict-проекций knowledge candidates и возвращает "
        "результат: одобренные, отложенные, ошибки и MCP-действия.\n\n"
        "``dry_run=true`` → действия генерируются, но в SemanticMemory "
        "ничего не записывается."
    ),
)
async def mira_process_endpoint(
    candidates: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ручной запуск MIRA для набора кандидатов."""
    from brain_engine.approval.mira import MIRAAutoApprover

    approver = MIRAAutoApprover()
    semantic = _deps["memory"].semantic if _deps.get("memory") else None
    result = await approver.process_candidates(
        candidates, semantic_memory=semantic, dry_run=dry_run,
    )
    return result.to_dict()


@router.post(
    "/mira/stats",
    tags=["Knowledge"],
    summary="Preview MIRA decisions (dry-run count, no side-effects)",
    description=(
        "Принимает кандидатов и возвращает сколько было бы "
        "одобрено / отложено. **Никакие данные не меняются.**"
    ),
)
async def mira_stats_endpoint(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Синхронный preview статистики MIRA."""
    from brain_engine.approval.mira import MIRAAutoApprover

    approver = MIRAAutoApprover()
    return approver.preview_stats(candidates)


# ═══════════════════════════════════════════════════════════════════════
# BOOKING, INTELLIGENCE, CONSOLIDATION, METRICS, HEALTH
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/booking/new",
    response_model=BrainEngineResponse,
    tags=["Guest Agent"],
    summary="Handle new booking lifecycle",
    description=(
        "**Full booking lifecycle: risk assessment → scheduling → welcome.**\n\n"
        "When a new booking is created in Cendra PMS:\n\n"
        "1. **Risk Assessment** — Guest Intelligence scores the guest (loyalty 0-100, risk flags)\n"
        "2. **Scheduling** — Creates preparation tasks (cleaning, access codes, welcome message)\n"
        "3. **Welcome** — Generates personalized welcome message based on guest history\n\n"
        "### Response includes:\n"
        "- `actions[]` — MCP tool calls: createTask, createAccessCode, sendWhatsApp\n"
        "- `tasks[]` — Ops tasks for preparation workflow\n"
    ),
)
async def handle_new_booking(request: NewBookingRequest) -> BrainEngineResponse:
    """Handle new booking lifecycle: risk → schedule → welcome → upsell."""
    start = time.monotonic()
    try:
        risk = await _assess_booking_risk(request)
        actions = _build_booking_actions(request, risk)
        welcome = _generate_welcome_message(request, risk)
        upsell_actions = _check_booking_upsells(request)
        actions.extend(upsell_actions)
        await _enqueue_booking_tasks(request, risk)
        await _record_booking(request, risk)

        # Create active process to track booking lifecycle
        process = await _create_booking_process(request, risk, actions)

        # Launch autonomous lifecycle in background (real calls)
        asyncio.create_task(_launch_booking_lifecycle(request, risk))

        return BrainEngineResponse(
            reply_text=welcome,
            confidence=risk.get("confidence", 0.8),
            cognitive_level="L2" if risk.get("is_high_risk") else "L1",
            actions=actions,
            message_tags=["booking", "new"] + risk.get("flags", []),
            is_need_attention=risk.get("is_high_risk", False),
            active_process=_build_process_ref(process),
            latency_ms=_elapsed_ms(start),
        )
    except Exception:
        logger.error("Booking handling failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Booking error")


async def _create_booking_process(
    request: NewBookingRequest,
    risk: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create an active process for the booking lifecycle.

    Args:
        request: Booking request.
        risk: Risk assessment result.
        actions: Generated actions.

    Returns:
        Created process dict.
    """
    from brain_engine.api import mockup_loader

    store = _deps.get("active_process_store")
    if not store:
        return {}
    guest_name = ""
    if request.guest_profile:
        guest_name = getattr(request.guest_profile, "name", "")

    # Load property data, cleaners, PMS from mockup
    prop = mockup_loader.get_property(request.property_id)
    prop_access = mockup_loader.get_property_access(request.property_id)
    pms_user = mockup_loader.get_pms_user(request.property_id)
    cleaners = mockup_loader.get_cleaners_for_property(request.property_id)

    # Build participants: guest + cleaners (waiting for dispatch)
    participants = [
        {
            "contact_id": guest_name or "guest",
            "role": "guest",
            "status": "confirmed",
            "last_message": "",
            "last_message_at": "",
        },
    ]
    for c in cleaners:
        participants.append({
            "contact_id": c.get("contact_id", c.get("name", "")),
            "role": "cleaner",
            "status": "pending",
            "last_message": "",
            "last_message_at": "",
        })

    return await store.create(
        process_type="booking",
        property_id=request.property_id,
        reason=f"New booking {request.reservation_id} — {guest_name}",
        deadline=str(request.checkin_date) if request.checkin_date else "",
        participants=participants,
        context={
            "reservation_id": request.reservation_id,
            "guest_name": guest_name,
            "checkin_date": str(request.checkin_date) if request.checkin_date else "",
            "checkout_date": str(request.checkout_date) if request.checkout_date else "",
            "num_guests": request.num_guests,
            "booking_source": request.booking_source,
            "risk_score": risk.get("score", 50),
            "risk_level": "high" if risk.get("is_high_risk") else "normal",
            "actions_count": len(actions),
            "property_access": prop_access,
            "pms_user": pms_user,
            "cleaners_available": [
                {"id": c.get("contact_id", ""), "name": c.get("name", "")}
                for c in cleaners
            ],
            "current_step": "cleaners_contacted",
            "cleaner_responses": [],
        },
        related_booking=request.reservation_id,
    )


def _build_process_ref(
    process: dict[str, Any],
) -> ActiveProcessResponse | None:
    """Convert a process dict to API response model.

    Args:
        process: Process dict from store.

    Returns:
        ActiveProcessResponse or None.
    """
    if not process:
        return None
    return ActiveProcessResponse(
        process_id=process.get("process_id", ""),
        type=process.get("type", ""),
        property_id=process.get("property_id", ""),
        status=process.get("status", ""),
        started_at=process.get("started_at", ""),
        deadline=process.get("deadline", ""),
        reason=process.get("reason", ""),
        participants=process.get("participants", []),
        pending_follow_ups=process.get("pending_follow_ups", []),
        history=process.get("history", []),
        context=process.get("context", {}),
    )


async def _assess_booking_risk(request: NewBookingRequest) -> dict[str, Any]:
    """Assess guest risk using Guest Intelligence.

    Args:
        request: New booking request.

    Returns:
        Risk assessment dict with score, flags, confidence.
    """
    score = 50
    flags: list[str] = []
    is_high_risk = False

    if request.guest_profile:
        score = min(100, request.guest_profile.booking_count * 10 + 30)
        if request.guest_profile.is_vip:
            score = max(score, 80)
            flags.append("vip")
        if request.guest_profile.booking_count == 0:
            flags.append("first_time_guest")
            is_high_risk = True

    if request.num_guests > 6:
        flags.append("large_group")
        is_high_risk = True

    return {
        "score": score,
        "flags": flags,
        "is_high_risk": is_high_risk,
        "confidence": 0.9 if request.guest_profile else 0.6,
    }


def _build_booking_actions(
    request: NewBookingRequest,
    risk: dict[str, Any],
) -> list[MCPAction]:
    """Build MCP actions for booking preparation.

    Args:
        request: New booking request.
        risk: Risk assessment result.

    Returns:
        List of MCP actions: task, access code, welcome message.
    """
    actions: list[MCPAction] = []

    actions.append(MCPAction(
        tool="createTask",
        params={
            "task": "Prepare for arrival",
            "main_category": "Booking Management",
            "description": (
                f"Check-in {request.checkin_date}, "
                f"{request.num_guests} guests, "
                f"source: {request.booking_source}"
            ),
            "propertyId": request.property_id,
            "reservationId": request.reservation_id,
        },
    ))

    actions.append(MCPAction(
        tool="createAccessCode",
        params={
            "propertyId": request.property_id,
            "name": f"Guest {request.reservation_id}",
            "startsAt": request.checkin_date,
            "endsAt": request.checkout_date,
        },
    ))

    guest_name = request.guest_profile.name if request.guest_profile else "Guest"
    actions.append(MCPAction(
        tool="sendWhatsApp",
        params={
            "reservationId": request.reservation_id,
            "message": _generate_welcome_message(request, risk),
        },
    ))

    return actions


def _generate_welcome_message(
    request: NewBookingRequest,
    risk: dict[str, Any],
) -> str:
    """Generate personalized welcome message.

    Args:
        request: Booking request.
        risk: Risk assessment with guest score.

    Returns:
        Welcome message text.
    """
    name = request.guest_profile.name if request.guest_profile else "Guest"
    is_vip = "vip" in risk.get("flags", [])

    if is_vip:
        return (
            f"Welcome back, {name}! We're thrilled to have you again. "
            f"Your stay from {request.checkin_date} to {request.checkout_date} "
            f"is confirmed. As a valued guest, you'll enjoy priority support."
        )

    return (
        f"Hello {name}! Your booking is confirmed. "
        f"Check-in: {request.checkin_date}, Check-out: {request.checkout_date}. "
        f"We'll send you access details closer to your arrival."
    )


def _check_booking_upsells(request: NewBookingRequest) -> list[MCPAction]:
    """Check for upsell opportunities on new booking.

    Args:
        request: New booking request.

    Returns:
        MCP actions for auto-applicable upsells.
    """
    from brain_engine.smart_engine.upsell_engine import BookingContext, UpsellEngine

    score = 50
    if request.guest_profile:
        score = min(100, request.guest_profile.booking_count * 10 + 30)

    engine = UpsellEngine()
    ctx = BookingContext(
        reservation_id=request.reservation_id,
        property_id=request.property_id,
        checkin_date=request.checkin_date,
        checkout_date=request.checkout_date,
        nightly_rate=request.booking_data.get("nightly_rate", 100),
        guest_score=score,
        num_guests=request.num_guests,
    )
    result = engine.analyze(ctx)

    return [
        MCPAction(tool=a["tool"], params=a.get("params", {}))
        for a in result.actions
    ]


async def _enqueue_booking_tasks(
    request: NewBookingRequest,
    risk: dict[str, Any],
) -> None:
    """Enqueue background tasks for booking preparation.

    These run asynchronously via WorkerPool — don't block the response.

    Args:
        request: New booking request.
        risk: Risk assessment result.
    """
    queue = _deps.get("task_queue")
    if not queue:
        return

    await queue.enqueue_batch(
        [
            ("send_welcome", {
                "reservation_id": request.reservation_id,
                "property_id": request.property_id,
                "message": _generate_welcome_message(request, risk),
            }),
            ("create_access_code", {
                "property_id": request.property_id,
                "reservation_id": request.reservation_id,
                "checkin": request.checkin_date,
                "checkout": request.checkout_date,
            }),
            ("schedule_cleaning", {
                "property_id": request.property_id,
                "checkout_date": request.checkout_date,
            }),
        ],
        source="booking_new",
        property_id=request.property_id,
    )


async def _record_booking(
    request: NewBookingRequest,
    risk: dict[str, Any],
) -> None:
    """Record new booking for learning.

    Args:
        request: New booking request.
        risk: Risk assessment.
    """
    recorder = _deps.get("recorder")
    if not recorder:
        return

    from brain_engine.continual_learning.interaction_recorder import BrainEngineInteraction
    await recorder.record(BrainEngineInteraction(
        event_type="new_booking",
        input_message=f"Booking {request.reservation_id}: {request.checkin_date}→{request.checkout_date}",
        context={"risk_flags": risk.get("flags", []),
                 "guest_score": risk.get("score", 0),
                 "num_guests": request.num_guests},
        output_response=f"Score: {risk.get('score')}, Actions: 3+",
        confidence=risk.get("confidence", 0.5),
        cognitive_level="L2",
        property_id=request.property_id,
    ))


async def _launch_booking_lifecycle(
    request: NewBookingRequest,
    risk: dict[str, Any],
) -> None:
    """Launch autonomous booking orchestrator in background.

    Creates a BookingOrchestrator that autonomously manages the
    entire turnover process via Telegram: contacts cleaners, waits
    for responses, asks PMS for selection, dispatches cleaner,
    collects photos, triggers OPS if needed, notifies everyone.

    Args:
        request: New booking request.
        risk: Risk assessment result.
    """
    from brain_engine.orchestrator.booking_orchestrator import BookingOrchestrator
    from brain_engine.orchestrator.response_router import response_router

    guest_name = request.guest_profile.name if request.guest_profile else "Guest"
    store = _deps.get("active_process_store")
    telegram_bot = _deps.get("telegram_bot")

    if not telegram_bot:
        logger.warning("No Telegram bot — orchestrator cannot run autonomously")
        return

    # Find or create process_id
    process_id = ""
    if store:
        processes = await store.get_active(
            property_id=request.property_id,
            process_type="booking",
        )
        if processes:
            process_id = processes[0].get("process_id", "")

    if not process_id:
        process_id = f"proc_booking_{request.reservation_id}"

    logger.info(
        "═══ LAUNCHING ORCHESTRATOR ═══ process=%s property=%s guest=%s",
        process_id, request.property_id, guest_name,
    )

    orchestrator = BookingOrchestrator(
        process_id=process_id,
        property_id=request.property_id,
        guest_name=guest_name,
        telegram_bot=telegram_bot,
        process_store=store,
        router=response_router,
    )

    asyncio.create_task(orchestrator.run())


@router.get(
    "/property/{property_id}/intelligence",
    response_model=PropertyIntelligenceResponse,
    tags=["Intelligence"],
    summary="Get accumulated knowledge for a property",
    description=(
        "**Returns everything Brain Engine has learned about a property.**\n\n"
        "Over time, Brain Engine accumulates knowledge from every interaction:\n\n"
        "- **Knowledge Facts** — extracted from guest conversations, reviews, incidents "
        "(e.g., 'WiFi router needs restart every Tuesday', 'guests often can\\'t find entrance')\n"
        "- **Learned Rules** — procedural memory evolved from owner approvals/rejections "
        "(e.g., 'auto-approve late checkout if guest score > 60', 'always call Ayse first for cleaning')\n"
        "- **Interaction Stats** — total interactions, resolution rate, common issues\n\n"
        "This endpoint is useful for:\n"
        "- Property onboarding — see what Brain Engine already knows\n"
        "- Debugging — understand why Brain Engine made a specific decision\n"
        "- Transfer — export knowledge when changing property managers\n"
    ),
)
async def get_property_intelligence(property_id: str) -> PropertyIntelligenceResponse:
    """Return accumulated knowledge for a property."""
    context = await _deps["cognitive"].remember(query="property", entity_ids=[property_id])

    facts = []
    for key, val in context.items():
        if key.startswith("entity_"):
            facts.extend(dict(f) for f in val.get("facts", []))

    rules = [{"name": p.name, "description": p.description, "confidence": p.confidence}
             for p in (await _deps["memory"].procedural.get_all_procedures())[:20]]

    interactions = await _deps["recorder"].get_by_property(property_id, days=30)
    total = len(interactions)
    resolved = sum(1 for i in interactions if getattr(i, "resolved_without_escalation", False))

    return PropertyIntelligenceResponse(
        property_id=property_id, knowledge_facts=facts, learned_rules=rules,
        total_interactions=total, resolution_rate=round(resolved / max(1, total), 3),
    )


@router.post(
    "/memory/consolidate",
    tags=["Learning"],
    summary="Trigger memory consolidation (nightly or monthly)",
    description=(
        "**Memory consolidation WITHOUT training/fine-tuning.**\n\n"
        "### Nightly Cycle (default):\n"
        "1. **Memory Consolidation** — promote frequent episodic memories to semantic, "
        "apply Ebbinghaus forgetting curve decay\n"
        "2. **Skill Evolution** — batch-evolve skills from day's failures\n"
        "3. **Preference Aggregation** — convert consistent owner decisions into stable rules\n"
        "4. **Knowledge Graph** — extract entities from recent episodes\n"
        "5. **Skill Pruning** — deactivate low-confidence and unused skills\n\n"
        "### Monthly Cycle (`cycle_type=monthly`):\n"
        "1. Accuracy metrics across all endpoints\n"
        "2. City maturity assessment (NEW → LEARNING → MATURE)\n"
        "3. Deep cleanup with stricter thresholds\n"
        "4. Report generation\n\n"
        "**Triggered by cron job.** No GPU costs. No training. "
        "All learning through skill evolution + external memory.\n"
    ),
)
async def trigger_consolidation(request: ConsolidationRequest) -> dict[str, Any]:
    """Trigger consolidation. NO training."""
    if request.cycle_type == "monthly":
        return await _deps["consolidator"].run_monthly()
    return await _deps["consolidator"].run_nightly()


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    tags=["System"],
    summary="Get learning and performance metrics",
    description=(
        "**30-day performance snapshot.**\n\n"
        "Returns:\n"
        "- `total_interactions` — how many events Brain Engine processed\n"
        "- `avg_grader_score` — average quality score (0.0-1.0) from APM Grader\n"
        "- `owner_intervention_rate` — % of decisions where owner overrode AI\n"
        "- `self_resolution_rate` — % resolved without escalation\n"
        "- `skills_evolved` — skills created/updated in last 30 days\n"
        "- `skills_total` — total active procedural skills\n"
        "- `llm_cost_total` — cumulative LLM API cost (USD)\n"
    ),
)
async def get_metrics() -> MetricsResponse:
    """Return learning metrics."""
    report = await _deps["evaluator"].evaluate(days=30)
    return MetricsResponse(
        total_interactions=report.total_interactions,
        avg_grader_score=report.avg_grader_score,
        owner_intervention_rate=report.owner_intervention_rate,
        self_resolution_rate=report.self_resolution_rate,
        skills_evolved=report.skills_evolved, skills_total=report.skills_total,
        llm_cost_total=_deps["llm"].costs.total_usd,
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check + system stats",
    description=(
        "**No authentication required.**\n\n"
        "Returns service health status and key stats:\n"
        "- `status` — 'healthy' or 'degraded'\n"
        "- `version` — API version\n"
        "- `skills_count` — total active procedural skills in memory\n"
        "- `accuracy_7d` — 7-day rolling accuracy (grader score average)\n"
        "- `uptime_hours` — server uptime\n"
        "- `llm_costs_24h` — LLM API costs in last 24 hours\n"
    ),
)
async def health() -> HealthResponse:
    """Health check with degradation detection."""
    degraded_components: list[str] = []

    accuracy = await _safe_get_accuracy(degraded_components)
    skills = await _safe_get_skills_count(degraded_components)
    llm_cost = _safe_get_llm_cost(degraded_components)

    status = "degraded" if degraded_components else "healthy"

    return HealthResponse(
        status=status, version="1.0.0", skills_count=skills,
        accuracy_7d=accuracy,
        uptime_hours=round((time.monotonic() - _start_time) / 3600, 2),
        llm_costs_24h=llm_cost,
    )


async def _safe_get_accuracy(
    degraded: list[str],
) -> float:
    """Get accuracy with degradation tracking.

    Args:
        degraded: List to append degraded component name.

    Returns:
        Accuracy score, or 0.0 on failure.
    """
    try:
        return await _deps["evaluator"].get_accuracy(days=7)
    except Exception:
        degraded.append("evaluator")
        return 0.0


async def _safe_get_skills_count(
    degraded: list[str],
) -> int:
    """Get skills count with degradation tracking.

    Args:
        degraded: List to append degraded component name.

    Returns:
        Skills count, or 0 on failure.
    """
    try:
        return await _deps["memory"].procedural.count()
    except Exception:
        degraded.append("memory")
        return 0


def _safe_get_llm_cost(degraded: list[str]) -> float:
    """Get LLM cost with degradation tracking.

    Args:
        degraded: List to append degraded component name.

    Returns:
        LLM cost in USD, or 0.0 on failure.
    """
    try:
        return _deps["llm"].costs.total_usd if _deps.get("llm") else 0.0
    except Exception:
        degraded.append("llm")
        return 0.0


# ═══════════════════════════════════════════════════════════════════════
# RULES CRUD API
# ═══════════════════════════════════════════════════════════════════════


def _procedure_to_rule_response(proc: Any) -> RuleResponse:
    """Convert a Procedure dataclass to RuleResponse.

    Args:
        proc: Procedure object from ProceduralMemory.

    Returns:
        Serializable RuleResponse.
    """
    total = proc.success_count + proc.failure_count
    success_rate = (proc.success_count / total) if total > 0 else None
    return RuleResponse(
        id=proc.procedure_id,
        property_id=getattr(proc, "property_id", ""),
        category=getattr(proc, "category", ""),
        rule=getattr(proc, "rule", "") or proc.description,
        confidence=proc.confidence,
        evidence_count=getattr(proc, "evidence_count", 0),
        success_rate=success_rate,
        source=proc.source,
        immutable=getattr(proc, "immutable", False),
        priority=getattr(proc, "priority", "medium"),
        tags=proc.tags,
        created_by=getattr(proc, "created_by", ""),
        created_at=proc.created_at,
        updated_at=getattr(proc, "updated_at", ""),
    )


@router.post(
    "/rules",
    response_model=RuleResponse,
    tags=["Rules"],
    summary="Create a procedural rule",
    description=(
        "Create a new procedural rule for a property.\n\n"
        "**Source types:**\n"
        "- `manual` — Customer-defined, protected from nightly prune\n"
        "- `immutable` — Safety rules, cannot be updated or deleted\n"
        "- `learned` — Auto-discovered, can be pruned\n"
    ),
)
async def create_rule(request: RuleCreateRequest) -> RuleResponse:
    """Create a new procedural rule for a property."""
    procedural = _deps["memory"].procedural
    proc = await procedural.store_manual_rule(
        property_id=request.property_id,
        category=request.category.value,
        rule_text=request.rule,
        confidence=request.confidence,
        source=request.source.value,
        immutable=request.immutable or request.source == "immutable",
        priority=request.priority.value,
        tags=request.tags,
        created_by=request.created_by,
    )
    logger.info(
        "Rule created: %s for property %s",
        proc.procedure_id, request.property_id,
    )
    return _procedure_to_rule_response(proc)


@router.get(
    "/rules",
    response_model=RuleListResponse,
    tags=["Rules"],
    summary="List rules for a property",
    description="Returns all active rules for a property, sorted by priority.",
)
async def list_rules(
    property_id: str,
    category: str | None = None,
    source: str | None = None,
) -> RuleListResponse:
    """List all rules for a property with optional filters."""
    procedural = _deps["memory"].procedural
    rules = await procedural.get_rules(
        property_id=property_id,
        category=category,
        source=source,
    )
    return RuleListResponse(
        property_id=property_id,
        rules=[_procedure_to_rule_response(r) for r in rules],
        total=len(rules),
    )


@router.get(
    "/rules/{rule_id}",
    response_model=RuleResponse,
    tags=["Rules"],
    summary="Get a single rule",
)
async def get_rule(rule_id: str) -> RuleResponse:
    """Get a single rule by ID."""
    procedural = _deps["memory"].procedural
    proc = await procedural.get_rule(rule_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return _procedure_to_rule_response(proc)


@router.put(
    "/rules/{rule_id}",
    response_model=RuleResponse,
    tags=["Rules"],
    summary="Update a rule",
    description="Update a rule. Immutable rules cannot be updated.",
)
async def update_rule(
    rule_id: str,
    request: RuleUpdateRequest,
) -> RuleResponse:
    """Update a rule. Raises 403 for immutable rules."""
    procedural = _deps["memory"].procedural
    updates = {
        k: v.value if hasattr(v, "value") else v
        for k, v in request.model_dump(exclude_none=True).items()
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        proc = await procedural.update_rule(rule_id, updates)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not proc:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    logger.info("Rule updated: %s", rule_id)
    return _procedure_to_rule_response(proc)


@router.delete(
    "/rules/{rule_id}",
    tags=["Rules"],
    summary="Delete a rule",
    description="Delete a rule. Immutable rules cannot be deleted.",
)
async def delete_rule(rule_id: str) -> dict[str, str]:
    """Delete a rule. Raises 403 for immutable rules."""
    procedural = _deps["memory"].procedural
    try:
        deleted = await procedural.delete_rule(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    logger.info("Rule deleted: %s", rule_id)
    return {"status": "deleted", "rule_id": rule_id}


# ═══════════════════════════════════════════════════════════════════════
# TEMPLATES API
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/templates",
    response_model=TemplateResponse,
    tags=["Templates"],
    summary="Create a rule template",
)
async def create_template(
    request: TemplateCreateRequest,
) -> TemplateResponse:
    """Create a new rule template for bulk onboarding."""
    template_store = _deps["template_store"]
    template = await template_store.create(
        template_name=request.template_name,
        description=request.description,
        rules=[r.model_dump() for r in request.rules],
        version=request.version,
    )
    logger.info("Template created: %s", template["template_id"])
    return TemplateResponse(**template)


@router.get(
    "/templates",
    response_model=TemplateListResponse,
    tags=["Templates"],
    summary="List all templates",
)
async def list_templates() -> TemplateListResponse:
    """List all available rule templates."""
    template_store = _deps["template_store"]
    templates = await template_store.list_all()
    return TemplateListResponse(
        templates=[TemplateResponse(**t) for t in templates],
        total=len(templates),
    )


@router.get(
    "/templates/{template_id}",
    response_model=TemplateResponse,
    tags=["Templates"],
    summary="Get a single template",
)
async def get_template(template_id: str) -> TemplateResponse:
    """Get a single template by ID."""
    template_store = _deps["template_store"]
    template = await template_store.get(template_id)
    if not template:
        raise HTTPException(
            status_code=404,
            detail=f"Template {template_id} not found",
        )
    return TemplateResponse(**template)


@router.put(
    "/templates/{template_id}",
    response_model=TemplateResponse,
    tags=["Templates"],
    summary="Update a template",
)
async def update_template(
    template_id: str,
    request: TemplateUpdateRequest,
) -> TemplateResponse:
    """Update an existing template."""
    template_store = _deps["template_store"]
    updates = {}
    if request.template_name is not None:
        updates["template_name"] = request.template_name
    if request.description is not None:
        updates["description"] = request.description
    if request.rules is not None:
        updates["rules"] = [r.model_dump() for r in request.rules]
    if request.version is not None:
        updates["version"] = request.version
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    template = await template_store.update(template_id, updates)
    if not template:
        raise HTTPException(
            status_code=404,
            detail=f"Template {template_id} not found",
        )
    logger.info("Template updated: %s", template_id)
    return TemplateResponse(**template)


@router.delete(
    "/templates/{template_id}",
    tags=["Templates"],
    summary="Delete a template",
)
async def delete_template(template_id: str) -> dict[str, str]:
    """Delete a template."""
    template_store = _deps["template_store"]
    deleted = await template_store.delete(template_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Template {template_id} not found",
        )
    logger.info("Template deleted: %s", template_id)
    return {"status": "deleted", "template_id": template_id}


@router.post(
    "/templates/{template_id}/apply/{property_id}",
    response_model=TemplateApplyResponse,
    tags=["Templates"],
    summary="Apply template to a property",
)
async def apply_template(
    template_id: str,
    property_id: str,
    created_by: str = "",
) -> TemplateApplyResponse:
    """Apply a rule template to a single property."""
    template_store = _deps["template_store"]
    procedural = _deps["memory"].procedural
    template = await template_store.get(template_id)
    if not template:
        raise HTTPException(
            status_code=404,
            detail=f"Template {template_id} not found",
        )
    return await _apply_template_to_properties(
        template, [property_id], procedural, created_by,
    )


@router.post(
    "/templates/{template_id}/apply-bulk",
    response_model=TemplateApplyResponse,
    tags=["Templates"],
    summary="Apply template to multiple properties",
)
async def apply_template_bulk(
    template_id: str,
    request: TemplateApplyRequest,
) -> TemplateApplyResponse:
    """Apply a rule template to multiple properties at once."""
    template_store = _deps["template_store"]
    procedural = _deps["memory"].procedural
    template = await template_store.get(template_id)
    if not template:
        raise HTTPException(
            status_code=404,
            detail=f"Template {template_id} not found",
        )
    return await _apply_template_to_properties(
        template, request.property_ids, procedural, request.created_by,
    )


async def _apply_template_to_properties(
    template: dict[str, Any],
    property_ids: list[str],
    procedural: Any,
    created_by: str,
) -> TemplateApplyResponse:
    """Apply template rules to a list of properties.

    Args:
        template: Template dict with rules.
        property_ids: Properties to apply to.
        procedural: ProceduralMemory instance.
        created_by: Creator identifier.

    Returns:
        TemplateApplyResponse with stats.
    """
    rules_created = 0
    conflicts: list[str] = []

    for prop_id in property_ids:
        for rule_item in template.get("rules", []):
            category = rule_item.get("category", "")
            if hasattr(category, "value"):
                category = category.value
            try:
                await procedural.store_manual_rule(
                    property_id=prop_id,
                    category=category,
                    rule_text=rule_item.get("rule", ""),
                    confidence=1.0,
                    source="immutable" if rule_item.get("immutable") else "manual",
                    immutable=rule_item.get("immutable", False),
                    priority=rule_item.get("priority", "medium"),
                    tags=rule_item.get("tags", []),
                    created_by=created_by or f"template:{template['template_id']}",
                )
                rules_created += 1
            except Exception as exc:
                conflicts.append(
                    f"Property {prop_id}, category {category}: {exc}"
                )

    logger.info(
        "Template %s applied to %d properties: %d rules created",
        template["template_id"], len(property_ids), rules_created,
    )
    return TemplateApplyResponse(
        template_id=template["template_id"],
        applied_to=len(property_ids),
        rules_created=rules_created,
        conflicts=conflicts,
        status="success" if not conflicts else "partial",
    )


# ═══════════════════════════════════════════════════════════════════════
# ACTIVE PROCESSES API
# ═══════════════════════════════════════════════════════════════════════


def _process_to_response(proc: dict[str, Any]) -> ActiveProcessResponse:
    """Convert a process dict to ActiveProcessResponse.

    Args:
        proc: Process dict from ActiveProcessStore.

    Returns:
        Serializable ActiveProcessResponse.
    """
    return ActiveProcessResponse(
        process_id=proc.get("process_id", ""),
        type=proc.get("type", ""),
        property_id=proc.get("property_id", ""),
        status=proc.get("status", "active"),
        started_at=proc.get("started_at", ""),
        deadline=proc.get("deadline", ""),
        reason=proc.get("reason", ""),
        participants=[
            ProcessParticipant(**p)
            for p in proc.get("participants", [])
        ],
        pending_follow_ups=proc.get("pending_follow_ups", []),
        history=proc.get("history", []),
        context=proc.get("context", {}),
    )


@router.get(
    "/processes",
    response_model=ActiveProcessListResponse,
    tags=["Processes"],
    summary="List active processes",
    description="Returns active processes, filtered by property or type.",
)
async def list_processes(
    property_id: str | None = None,
    process_type: str | None = None,
) -> ActiveProcessListResponse:
    """List active processes with optional filters."""
    store = _deps["active_process_store"]
    processes = await store.get_active(
        property_id=property_id,
        process_type=process_type,
    )
    return ActiveProcessListResponse(
        processes=[_process_to_response(p) for p in processes],
        total=len(processes),
    )


@router.get(
    "/processes/{process_id}",
    response_model=ActiveProcessResponse,
    tags=["Processes"],
    summary="Get a single process",
)
async def get_process(process_id: str) -> ActiveProcessResponse:
    """Get a single active process by ID."""
    store = _deps["active_process_store"]
    proc = await store.get(process_id)
    if not proc:
        raise HTTPException(
            status_code=404,
            detail=f"Process {process_id} not found",
        )
    return _process_to_response(proc)


@router.post(
    "/process/reply",
    response_model=BrainEngineResponse,
    tags=["Processes"],
    summary="Handle reply from any process participant",
    description=(
        "Handles replies from any participant role: cleaner, vendor, "
        "guest, owner, insurance, legal, sales_team, support, custom.\n\n"
        "Brain Engine reads the process context and AI decides next action."
    ),
)
async def process_reply(
    request: ProcessReplyRequest,
) -> BrainEngineResponse:
    """Handle a reply from any participant in an active process."""
    start = time.monotonic()
    store = _deps["active_process_store"]

    proc = await store.get(request.process_id)
    if not proc:
        raise HTTPException(
            status_code=404,
            detail=f"Process {request.process_id} not found",
        )

    # Update participant status
    await store.update_participant(
        process_id=request.process_id,
        contact_id=request.contact_id,
        updates={
            "status": "replied",
            "last_message": request.message,
            "last_message_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc,
            ).isoformat(),
        },
    )

    # Build context for AI decision
    description = (
        f"Process {request.process_id}: {request.contact_type} "
        f"'{request.contact_id}' replied: {request.message}"
    )
    if request.photos:
        description += f" [+{len(request.photos)} photos]"
    if request.cost_amount:
        description += (
            f" [cost: {request.cost_amount} {request.cost_currency}]"
        )

    # Retrieve memory context
    context = await _deps["cognitive"].build_full_context(
        query=description,
        entity_ids=[request.property_id, request.process_id],
    )

    return BrainEngineResponse(
        reply_text=f"Received reply from {request.contact_id} in process {request.process_id}",
        confidence=0.8,
        cognitive_level="L2",
        active_process=_process_to_response(
            await store.get(request.process_id) or proc,
        ),
        reasoning_trace=(
            f"Process reply from {request.contact_type} "
            f"{request.contact_id}. Context retrieved. "
            f"AI will decide next action based on SOP + rules."
        ),
        latency_ms=_elapsed_ms(start),
    )


# ═══════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════════


async def _load_guest_context_direct(guest_id: str) -> str:
    """Load guest memory context for direct (non-durable) pipeline.

    Args:
        guest_id: Guest identifier.

    Returns:
        Guest context string for LLM, or empty.
    """
    store = _deps.get("guest_memory_store")
    if not store or not guest_id:
        return ""

    memory = await store.load(guest_id)
    if not memory.is_returning and memory.total_interactions == 0:
        return ""

    return memory.to_context_string()


async def _update_guest_memory_direct(
    guest_id: str,
    request: Any,
    classification: Any,
) -> None:
    """Update guest memory after direct pipeline processing.

    Args:
        guest_id: Guest identifier.
        request: Processed request.
        classification: Classification results.
    """
    store = _deps.get("guest_memory_store")
    if not store or not guest_id:
        return

    await store.record_interaction(
        guest_id=guest_id,
        property_id=request.property_id,
    )

    if classification.is_cleaning_issue:
        await store.add_incident(guest_id, "complaint", "Cleaning issue")
    if classification.is_noise_complaint:
        await store.add_incident(guest_id, "noise", "Noise complaint")
    if classification.is_maintenance_issue:
        await store.add_incident(guest_id, "complaint", "Maintenance issue")


async def _retrieve_memory(query: str, property_id: str) -> dict[str, Any]:
    """Retrieve memory context.

    Args:
        query: Search query.
        property_id: Property ID.

    Returns:
        Memory context dict.
    """
    return await _deps["cognitive"].remember(query=query, entity_ids=[property_id])


def _build_chat_messages(
    system: str, history: list[dict[str, str]], user_msg: str,
) -> list[dict[str, str]]:
    """Build LLM messages.

    Args:
        system: System prompt.
        history: Conversation history.
        user_msg: Current message.

    Returns:
        Messages in OpenAI format.
    """
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": user_msg}]


def _elapsed_ms(start: float) -> int:
    """Calculate elapsed milliseconds.

    Args:
        start: Start time.

    Returns:
        Elapsed ms.
    """
    return int((time.monotonic() - start) * 1000)
