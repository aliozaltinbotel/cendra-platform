"""Post-processing — tags, sentiment, task creation after agent response.

Runs after the ReAct agent produces a response. Enriches the
pipeline state with message tags, sentiment analysis, and
auto-creates tasks when the AI cannot fully resolve a request.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from brain_engine.conversation.models import (
    AutoTask,
    MessageTag,
    PipelineState,
    ResponseFlags,
    SentimentCategory,
    SentimentResult,
    TaskLevel,
)

logger = logging.getLogger(__name__)

_POST_MODEL = "gpt-4o-mini"
_POST_TEMPERATURE = 0.1


async def run_postprocessing(state: PipelineState) -> PipelineState:
    """Run all post-processing steps on the pipeline state.

    Steps:
    1. Assign message tags based on flags + response content
    2. Analyze sentiment (1-10)
    3. Evaluate response quality (was_helpful, send_status)
    4. Auto-create tasks if needed

    Args:
        state: Current pipeline state with agent response.

    Returns:
        Updated pipeline state with post-processing results.
    """
    state.message_tags = _assign_tags(state)
    state.sentiment = await _analyze_sentiment(state)
    state.response_flags = await _evaluate_response(state)
    state.tasks = await _create_tasks_if_needed(state)
    return state


def _assign_tags(state: PipelineState) -> list[str]:
    """Assign message tags based on business flags and response.

    Args:
        state: Pipeline state with classification results.

    Returns:
        List of applicable tag names.
    """
    tags: list[str] = []
    flags = state.business_flags

    # Primary tags from flags
    tag_map: dict[str, str] = {
        "is_availability_related": MessageTag.AVAILABILITY_REQUEST.value,
        "is_reservation_related": MessageTag.BOOKING_MODIFICATION_REQUEST.value,
        "is_additional_services": MessageTag.EXTRA_SERVICE_REQUEST.value,
        "is_discount_request": MessageTag.DISCOUNT_REQUEST.value,
        "is_invoice_request": MessageTag.INVOICE_REQUEST.value,
        "is_property_related": MessageTag.PROPERTY_INFO_REQUEST.value,
        "is_check_in_out_related": MessageTag.UPSELL_REQUEST.value,
        "is_complaint": MessageTag.COMPLAINT.value,
        "is_emergency": MessageTag.IS_EMERGENCY.value,
    }

    flags_dict = flags.model_dump()
    for flag_field, tag_value in tag_map.items():
        if flags_dict.get(flag_field):
            tags.append(tag_value)

    # OPS tags
    if flags.is_cleaning_issue or flags.is_maintenance_issue:
        tags.append(MessageTag.OPERATIONAL_TASK.value)

    # Meta tags from response quality
    response = state.agent_response.lower()
    if any(phrase in response for phrase in [
        "let me check", "i'll find out", "get back to you",
    ]):
        tags.append(MessageTag.MISSING_INFO.value)
    else:
        tags.append(MessageTag.QUESTION_ANSWERED.value)

    return list(dict.fromkeys(tags))  # deduplicate preserving order


async def _analyze_sentiment(state: PipelineState) -> SentimentResult:
    """Analyze guest message sentiment via LLM.

    Args:
        state: Pipeline state with guest message.

    Returns:
        SentimentResult with score (1-10) and category.
    """
    try:
        response = await litellm.acompletion(
            model=_POST_MODEL,
            messages=[
                {"role": "system", "content": _SENTIMENT_SYSTEM},
                {"role": "user", "content": state.cleaned_message},
            ],
            temperature=_POST_TEMPERATURE,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        score = int(data.get("score", 5))
        score = max(1, min(10, score))

        category = SentimentCategory.NEUTRAL
        if score <= 3:
            category = SentimentCategory.NEGATIVE
        elif score >= 7:
            category = SentimentCategory.POSITIVE

        return SentimentResult(
            score=score,
            category=category,
            reasoning=data.get("reasoning", ""),
        )
    except Exception:
        logger.debug("Sentiment analysis failed", exc_info=True)
        return SentimentResult()


async def _evaluate_response(state: PipelineState) -> ResponseFlags:
    """Evaluate response quality and determine routing flags.

    Postprocessing must never *downgrade* a gate the orchestrator
    raised earlier in the pipeline.  The §10 priority chain
    (Branch 4) is authoritative: when it sets
    ``is_need_attention=True`` or ``send_status=False`` because of a
    block / approval verdict, postprocessing only fills in advisory
    quality fields (``was_helpful``, ``completeness``) and OR-merges
    the gate flags so the orchestrator decision survives.

    The historic behaviour replaced ``state.response_flags`` wholesale,
    which silently undid the enforcement gate — guests received the
    deterministic deny copy but consumers saw ``send_status=True``
    and auto-sent it before the PM panel could intervene.

    Args:
        state: Pipeline state with agent response.

    Returns:
        ResponseFlags with was_helpful, send_status, is_need_attention.
    """
    response = state.agent_response
    flags = state.business_flags

    # Auto-flag for attention from response/business signals.
    needs_attention = _needs_human_attention(response, flags)

    # Determine completeness from the draft.
    has_deferral = any(
        phrase in response.lower()
        for phrase in ["let me check", "get back to you", "i'll find out"]
    )
    completeness = "partial" if has_deferral else "full"

    existing = state.response_flags
    return ResponseFlags(
        was_helpful=0.5 if has_deferral else 1.0,
        # OR-merge: orchestrator-raised attention is preserved even
        # when the post-hoc text scan sees nothing notable.
        is_need_attention=needs_attention or existing.is_need_attention,
        # AND-merge: a single ``False`` from either authority holds the
        # send.  Orchestrator block / approval already set False; this
        # path only further restricts (never re-enables) auto-send.
        send_status=existing.send_status and (not needs_attention),
        # ``completeness == "none"`` is the orchestrator's deterministic
        # deny signal — never overwrite it with a text-derived label.
        completeness=(
            "none" if existing.completeness == "none" else completeness
        ),
    )


def _needs_human_attention(
    response: str,
    flags: Any,
) -> bool:
    """Determine if human review is needed.

    Args:
        response: Agent's response text.
        flags: Business flags.

    Returns:
        True if PM should review before sending.
    """
    if flags.is_emergency:
        return True
    if flags.is_complaint and flags.is_maintenance_issue:
        return True

    lower = response.lower()
    attention_phrases = [
        "i don't have access",
        "cannot confirm",
        "unable to",
        "i'm not sure",
        "refund",
        "compensation",
    ]
    return any(phrase in lower for phrase in attention_phrases)


async def _create_tasks_if_needed(
    state: PipelineState,
) -> list[AutoTask]:
    """Auto-create tasks when AI cannot fully resolve the request.

    Args:
        state: Pipeline state with flags and response quality.

    Returns:
        List of auto-created tasks (may be empty).
    """
    if state.response_flags.completeness == "full":
        return []

    if not state.response_flags.is_need_attention:
        return []

    flags = state.business_flags
    category, subcategory = _categorize_task(flags)

    level = TaskLevel.MEDIUM
    if flags.is_emergency:
        level = TaskLevel.URGENT
    elif flags.is_complaint:
        level = TaskLevel.HIGH

    return [
        AutoTask(
            task_level=level,
            description=f"Guest request needs follow-up: {state.cleaned_message[:200]}",
            main_category=category,
            sub_category=subcategory,
            tags=state.message_tags[:5],
        ),
    ]


def _categorize_task(flags: Any) -> tuple[str, str]:
    """Map business flags to task category/subcategory.

    Args:
        flags: Business flags.

    Returns:
        Tuple of (main_category, sub_category).
    """
    if flags.is_emergency:
        return "Emergency Situations", "Immediate Response"
    if flags.is_cleaning_issue:
        return "Cleaning and Hygiene", "Cleanliness Complaints"
    if flags.is_maintenance_issue:
        return "Maintenance Needs", "Repair Requests"
    if flags.is_noise_complaint:
        return "Guest Complaints", "Noise Complaints"
    if flags.is_security_issue:
        return "Emergency Situations", "Security Issues"
    if flags.is_complaint:
        return "Guest Complaints", "General Complaints"
    if flags.is_reservation_related:
        return "Reservation Issues", "Booking Modifications"
    if flags.is_invoice_request:
        return "Financial Matters", "Invoice Requests"
    return "Communication and Follow-Up", "General Follow-Up"


# ── Prompt templates ────────────────────────────────────────── #

_SENTIMENT_SYSTEM = (
    "Analyze the sentiment of this guest message on a scale of 1-10. "
    "1=very negative/angry, 5=neutral, 10=very positive/happy. "
    'Return JSON: {"score": 5, "reasoning": "brief explanation"}'
)
