"""Durable Guest Message Pipeline — checkpointed, parallel, retriable.

Wraps the guest message processing pipeline with durability features:
- Each step is checkpointed to Redis (resume on crash)
- Memory retrieval runs in parallel with KB search
- LLM calls use retry with exponential backoff
- Escalation can interrupt pipeline for PM approval

This module provides `process_guest_message_durable()` which is called
by the main API endpoint when durability is enabled.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from brain_engine.api.conversation_memory import (
    load_conversation_history,
    save_conversation_turn,
)
from brain_engine.api.models import (
    BrainEngineResponse,
    ClassificationFlags,
    GuestMessageRequest,
)
from brain_engine.durability.parallel import ParallelStep, parallel
from brain_engine.durability.pipeline import DurablePipeline, PipelineContext
from brain_engine.durability.retry import LLM_RETRY

logger = logging.getLogger(__name__)

# Dependencies injected from cendra_adapter._deps
_deps: dict[str, Any] = {}


def set_deps(deps: dict[str, Any]) -> None:
    """Inject dependencies from cendra_adapter.

    Args:
        deps: Dependency dict from configure_dependencies().
    """
    _deps.update(deps)


async def process_guest_message_durable(
    request: GuestMessageRequest,
    pipeline: DurablePipeline,
) -> BrainEngineResponse:
    """Process guest message with full durability.

    7-step pipeline with checkpointing after each step:
      1. classify   — 13 business flags (LLM + fallback)
      2. route      — complexity level L1-L4
      3. context    — memory + KB search in PARALLEL
      4. generate   — LLM reply with retry
      5. validate   — guardrail pipeline
      6. escalate   — apply escalation rules
      7. record     — store interaction

    Args:
        request: Guest message request from API.
        pipeline: DurablePipeline instance with Redis checkpointer.

    Returns:
        BrainEngineResponse with full pipeline results.
    """
    thread_id = f"guest:{request.property_id}:{request.reservation_id or 'no_res'}"
    start = time.monotonic()
    guest_id = (
        (request.guest_profile.guest_id if request.guest_profile else "")
        or request.reservation_id
        or ""
    )

    # Load guest memory + conversation history BEFORE pipeline
    guest_ctx = await _load_guest_context(guest_id)
    conv_history = await load_conversation_history(
        redis=_deps.get("redis"),
        guest_id=guest_id,
        property_id=request.property_id,
    )

    # Inject loaded conversation history into request if client didn't send it
    if not request.conversation_history and conv_history:
        request.conversation_history = conv_history

    async with pipeline.run(thread_id, total_steps=7) as ctx:
        flags = await ctx.step("classify", _step_classify, request)
        classification = ClassificationFlags(**flags)

        route = await ctx.step("route", _step_route, request, classification)
        level = route["level"]

        context = await ctx.step("context", _step_context, request, guest_ctx)

        gen = await ctx.step(
            "generate", _step_generate,
            request, classification, level, context,
            retry=LLM_RETRY,
        )

        validated = await ctx.step(
            "validate", _step_validate, gen, request,
        )

        escalated = await ctx.step(
            "escalate", _step_escalate, validated, request, classification,
        )

        await ctx.step("record", _step_record, request, escalated, level, classification)

    # Save conversation turn to Redis (message + reply)
    reply_text = escalated.get("reply_text", "")
    await save_conversation_turn(
        redis=_deps.get("redis"),
        guest_id=guest_id,
        property_id=request.property_id,
        user_message=request.message,
        assistant_reply=reply_text,
    )

    # Update guest memory AFTER pipeline (learn from this interaction)
    await _update_guest_memory(guest_id, request, classification)

    response = _build_response(escalated, classification, level, start)

    # Create active process for trackable events
    from brain_engine.api.cendra_adapter import (
        _build_process_ref,
        _create_guest_process,
    )
    process = await _create_guest_process(request, classification, response)
    if process:
        response.active_process = _build_process_ref(process)

    return response


async def _step_classify(
    request: GuestMessageRequest,
) -> dict[str, Any]:
    """Step 1: Classify message into 13 business flags.

    Args:
        request: Guest message request.

    Returns:
        Dict of classification flags.
    """
    classifier = _deps.get("classifier")
    if not classifier:
        return ClassificationFlags().model_dump()

    labels = request.guest_profile.labels if request.guest_profile else []
    result = await classifier.classify(
        message=request.message,
        conversation_history=request.conversation_history,
        guest_labels=labels,
    )

    return {
        f.lower(): result.flags.get(f, False)
        for f in [
            "IS_EMERGENCY", "IS_PROPERTY_RELATED", "IS_AVAILABILITY_RELATED",
            "IS_RESERVATION_RELATED", "IS_COMPLAINT", "IS_CHECK_IN_OUT_RELATED",
            "IS_NAVIGATION_QUERY", "IS_DISCOUNT_REQUEST", "IS_INVOICE_REQUEST",
            "IS_CLEANING_ISSUE", "IS_MAINTENANCE_ISSUE", "IS_NOISE_COMPLAINT",
            "IS_SECURITY_ISSUE",
        ]
    }


async def _step_route(
    request: GuestMessageRequest,
    classification: ClassificationFlags,
) -> dict[str, Any]:
    """Step 2: Route to cognitive complexity level.

    Args:
        request: Guest message request.
        classification: Business flags.

    Returns:
        Dict with 'level' key.
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
    return {"level": level.value}


async def _step_context(
    request: GuestMessageRequest,
    guest_ctx: str = "",
) -> dict[str, Any]:
    """Step 3: Retrieve memory + KB context in PARALLEL.

    Memory retrieval and cognitive context build run simultaneously.
    Guest context (from GuestMemoryStore) is injected into the result.

    Args:
        request: Guest message request.
        guest_ctx: Guest memory context string.

    Returns:
        Dict with 'memory' and 'cognitive' context.
    """
    async def _get_memory() -> dict[str, Any]:
        return await _deps["cognitive"].remember(
            query=request.message, entity_ids=[request.property_id],
        )

    async def _get_cognitive() -> dict[str, Any]:
        text = await _deps["cognitive"].build_full_context(
            query=request.message, entity_ids=[request.property_id],
        )
        return {"context_text": text}

    result = await parallel(
        ParallelStep(name="memory", func=_get_memory),
        ParallelStep(name="cognitive", func=_get_cognitive),
    )

    context_text = result.results.get("cognitive", {}).get("context_text", "")
    if guest_ctx:
        context_text = f"GUEST HISTORY:\n{guest_ctx}\n\n{context_text}"

    return {
        "memory": result.results.get("memory", {}),
        "context_text": context_text,
    }


async def _step_generate(
    request: GuestMessageRequest,
    classification: ClassificationFlags,
    level: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Step 4: Generate LLM reply with retry.

    Args:
        request: Guest message request.
        classification: Business flags.
        level: Cognitive level.
        context: Memory + cognitive context.

    Returns:
        Dict with reply_text, model_used, confidence.
    """
    from brain_engine.api.cendra_adapter import (
        _build_chat_messages,
        _build_guest_system_prompt,
    )
    from brain_engine.reasoning.complexity_router import CognitiveLevel

    config = _deps["complexity"].get_model_config(CognitiveLevel(level))
    system_prompt = _build_guest_system_prompt(
        request, classification, context.get("context_text", ""),
    )
    messages = _build_chat_messages(
        system_prompt, request.conversation_history, request.message,
    )
    llm_response = await _deps["llm"].call(messages, config)

    return {
        "reply_text": llm_response.content,
        "model_used": llm_response.model_used,
        "confidence": 0.85,
    }


async def _step_validate(
    gen: dict[str, Any],
    request: GuestMessageRequest,
) -> dict[str, Any]:
    """Step 5: Run guardrail pipeline on response.

    Args:
        gen: Generated response data.
        request: Original request.

    Returns:
        Validated response data.
    """
    result = _deps["guardrails"].validate_response(
        gen["reply_text"],
        context={"property_id": request.property_id},
        knowledge_base=request.property_summary,
    )

    validated = dict(gen)
    if result.cleaned_response != gen["reply_text"]:
        validated["reply_text"] = result.cleaned_response
    validated["guardrails_passed"] = result.passed
    validated["needs_attention"] = not result.passed

    return validated


async def _step_escalate(
    validated: dict[str, Any],
    request: GuestMessageRequest,
    classification: ClassificationFlags,
) -> dict[str, Any]:
    """Step 6: Apply escalation rules from agent config.

    Args:
        validated: Validated response data.
        request: Original request.
        classification: Business flags.

    Returns:
        Response data with escalation flags.
    """
    escalated = dict(validated)
    active_tags = _get_active_flags(classification)
    escalated["message_tags"] = active_tags

    if not request.agent_config or not request.agent_config.escalations:
        return escalated

    for rule in request.agent_config.escalations:
        if rule.tag not in active_tags:
            continue
        if rule.behavior == "escalate":
            escalated["needs_attention"] = True
            escalated["send_status"] = False
        elif rule.behavior == "answer_and_escalate":
            escalated["needs_attention"] = True

    return escalated


async def _step_record(
    request: GuestMessageRequest,
    response_data: dict[str, Any],
    level: str,
    classification: ClassificationFlags,
) -> dict[str, Any]:
    """Step 7: Record interaction for skill evolution.

    Args:
        request: Original request.
        response_data: Final response data.
        level: Cognitive level used.
        classification: Business flags.

    Returns:
        Empty dict (no output needed).
    """
    from brain_engine.continual_learning.interaction_recorder import (
        BrainEngineInteraction,
    )

    await _deps["recorder"].record(BrainEngineInteraction(
        event_type="guest_message",
        input_message=request.message,
        context={
            "property_id": request.property_id,
            "channel": request.channel,
            "flags": classification.model_dump(),
        },
        output_response=response_data.get("reply_text", ""),
        output_actions=[],
        confidence=response_data.get("confidence", 0.5),
        cognitive_level=level,
        property_id=request.property_id,
    ))

    return {}


# ── Helpers ──────────────────────────────────────────────────────── #


def _get_active_flags(classification: ClassificationFlags) -> list[str]:
    """Convert active flags to tag strings.

    Args:
        classification: Business flags.

    Returns:
        List of active flag name strings.
    """
    flags = classification.model_dump()
    return [k.upper() for k, v in flags.items() if v]


async def _load_guest_context(guest_id: str) -> str:
    """Load guest memory and format as LLM context.

    Combines GuestMemoryStore data with Redis-stored guest profile.

    Args:
        guest_id: Guest identifier.

    Returns:
        Guest context string for LLM, or empty if no memory.
    """
    parts: list[str] = []

    # Load from GuestMemoryStore (preferences, incidents, patterns)
    store = _deps.get("guest_memory_store")
    if store and guest_id:
        memory = await store.load(guest_id)
        if memory.is_returning or memory.total_interactions > 0:
            parts.append(memory.to_context_string())

    # Load guest profile from Redis (name, language, last interaction)
    redis = _deps.get("redis")
    if redis and guest_id:
        try:
            profile = await redis.hgetall(f"guest_profile:{guest_id}")
            if profile:
                name = profile.get("name", "")
                if name:
                    parts.append(f"Guest name: {name}")
                lang = profile.get("language", "")
                if lang:
                    parts.append(f"Preferred language: {lang}")
        except Exception:
            pass

    return "\n".join(parts)


async def _update_guest_memory(
    guest_id: str,
    request: GuestMessageRequest,
    classification: ClassificationFlags,
) -> None:
    """Update guest memory after processing a message.

    Records interaction, detects language, adds incidents if applicable.

    Args:
        guest_id: Guest identifier.
        request: Processed request.
        classification: Classification results.
    """
    store = _deps.get("guest_memory_store")
    if not store or not guest_id:
        return

    # Record the interaction with full details
    lang = ""
    classifier = _deps.get("classifier")
    if classifier:
        lang = getattr(classifier, "_last_response_language", "")

    await store.record_interaction(
        guest_id=guest_id,
        property_id=request.property_id,
        language=lang,
    )

    # Store guest name if provided (for future recall)
    guest_name = ""
    if request.guest_profile and request.guest_profile.name:
        guest_name = request.guest_profile.name
    if guest_name:
        redis = _deps.get("redis")
        if redis:
            try:
                key = f"guest_profile:{guest_id}"
                import json as _json
                await redis.hset(key, mapping={
                    "name": guest_name,
                    "language": lang or request.language or "en",
                    "property_id": request.property_id,
                    "last_message": request.message[:200],
                })
                await redis.expire(key, 365 * 86400)  # 1 year
            except Exception:
                logger.warning("Failed to save guest profile for %s", guest_id)

    # Record incidents from classification
    if classification.is_cleaning_issue:
        await store.add_incident(guest_id, "complaint", "Cleaning issue reported")
    if classification.is_noise_complaint:
        await store.add_incident(guest_id, "noise", "Noise complaint reported")
    if classification.is_maintenance_issue:
        await store.add_incident(guest_id, "complaint", "Maintenance issue reported")

    # Detect patterns
    queue = _deps.get("task_queue")
    if queue and classification.is_navigation_query:
        await store.add_pattern(guest_id, "needs navigation help on arrival")


def _build_response(
    data: dict[str, Any],
    classification: ClassificationFlags,
    level: str,
    start: float,
) -> BrainEngineResponse:
    """Build final API response from pipeline data.

    Args:
        data: Accumulated pipeline data.
        classification: Business flags.
        level: Cognitive level.
        start: Pipeline start time.

    Returns:
        BrainEngineResponse.
    """
    return BrainEngineResponse(
        reply_text=data.get("reply_text", ""),
        confidence=data.get("confidence", 0.5),
        cognitive_level=level.upper(),
        classification=classification,
        model_used=data.get("model_used", ""),
        send_status=data.get("send_status", True),
        is_need_attention=data.get("needs_attention", False),
        message_tags=data.get("message_tags", []),
        latency_ms=int((time.monotonic() - start) * 1000),
    )
