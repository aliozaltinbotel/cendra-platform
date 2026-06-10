"""OPS reply parser — extracts structured data from vendor/cleaner replies.

Parses natural language replies to extract confirmation status,
arrival time, cost, availability issues, and suggests next actions.
"""

from __future__ import annotations

import json
import logging

import litellm

from brain_engine.ops.models import (
    OpsParseReplyData,
    OpsParseReplyRequest,
    OpsParseReplyResponse,
)

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.1


async def parse_ops_reply(
    request: OpsParseReplyRequest,
) -> OpsParseReplyResponse:
    """Parse a vendor or cleaner reply into structured data.

    Args:
        request: Reply parsing request with context.

    Returns:
        Structured data extracted from the reply.
    """
    prompt = _build_prompt(request)

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        parsed = OpsParseReplyData(
            confirmed=data.get("confirmed", False),
            arrival_time=data.get("arrival_time"),
            cost_mentioned=data.get("cost_mentioned"),
            cost_exceeds_threshold=_check_cost_threshold(
                data.get("cost_mentioned"), request.cost_threshold,
            ),
            availability_issue=data.get("availability_issue"),
            additional_notes=data.get("additional_notes", ""),
            needs_followup=data.get("needs_followup", False),
            suggested_actions=data.get("suggested_actions", []),
        )
        return OpsParseReplyResponse(data=parsed)
    except Exception as exc:
        logger.error("Reply parsing failed: %s", exc)
        return OpsParseReplyResponse(status=False, error=str(exc))


def _build_prompt(request: OpsParseReplyRequest) -> str:
    """Build parsing prompt from request data.

    Args:
        request: Reply parsing request.

    Returns:
        Formatted prompt string.
    """
    context_str = json.dumps(
        request.original_context, indent=2, ensure_ascii=False,
    )
    threshold = (
        f"\nCost threshold: {request.cost_threshold}"
        if request.cost_threshold
        else ""
    )

    return (
        f"Original message type: {request.original_message_type}\n"
        f"Original context:\n{context_str}\n"
        f"Reply text: {request.reply_text}\n"
        f"{threshold}"
    )


def _check_cost_threshold(
    cost: float | None,
    threshold: float | None,
) -> bool:
    """Check if mentioned cost exceeds threshold.

    Args:
        cost: Cost mentioned in reply.
        threshold: Customer's cost threshold.

    Returns:
        True if cost exceeds threshold.
    """
    if cost is None or threshold is None:
        return False
    return cost > threshold


_SYSTEM_PROMPT = """Extract structured data from a vendor/cleaner reply.

Return JSON with:
{
    "confirmed": true/false,
    "arrival_time": "HH:MM or null",
    "cost_mentioned": 150.0 or null,
    "availability_issue": "reason or null",
    "additional_notes": "any extra info",
    "needs_followup": true/false,
    "suggested_actions": ["update_guest_eta", "notify_owner_cost_change", ...]
}

Valid suggested_actions:
- update_guest_eta
- notify_owner_cost_change
- try_backup_contact
- send_clarifying_question
- mark_task_complete
- no_action_needed

Extract ONLY explicit information. Never assume or invent.
"""
