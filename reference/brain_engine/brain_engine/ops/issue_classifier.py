"""OPS issue classifier — categorizes maintenance issues by vendor type.

Classifies reported issues into vendor categories (plumbing,
electrical, etc.) with urgency levels for dispatch routing.
"""

from __future__ import annotations

import json
import logging

import litellm

from brain_engine.ops.models import (
    OpsClassifyRequest,
    OpsClassifyResponse,
    OpsUrgency,
    VendorCategory,
    VendorMatch,
)

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.1


async def classify_ops_issue(
    request: OpsClassifyRequest,
) -> OpsClassifyResponse:
    """Classify a maintenance issue into vendor categories.

    Args:
        request: Issue classification request.

    Returns:
        Classified vendor categories with urgency.
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
        categories = _parse_categories(data.get("vendor_categories", []))

        return OpsClassifyResponse(
            vendor_categories=categories,
            overall_urgency=OpsUrgency(data.get("overall_urgency", "normal")),
            reasoning=data.get("reasoning", ""),
        )
    except Exception as exc:
        logger.error("Issue classification failed: %s", exc)
        return OpsClassifyResponse(status=False, error=str(exc))


def _build_prompt(request: OpsClassifyRequest) -> str:
    """Build classification prompt from request.

    Args:
        request: Classification request.

    Returns:
        Formatted prompt.
    """
    guest_msgs = "\n".join(
        f"- {m}" for m in request.guest_messages
    ) if request.guest_messages else "None"

    return (
        f"Task description: {request.task_description}\n"
        f"Guest messages:\n{guest_msgs}\n"
        f"Main category: {request.main_category}\n"
        f"Sub category: {request.sub_category}"
    )


def _parse_categories(raw: list[dict]) -> list[VendorMatch]:
    """Parse raw category data into VendorMatch list.

    Args:
        raw: Raw category dicts from LLM.

    Returns:
        List of validated VendorMatch objects.
    """
    result: list[VendorMatch] = []
    for item in raw[:3]:
        try:
            result.append(VendorMatch(
                category=VendorCategory(item["category"]),
                urgency=OpsUrgency(item.get("urgency", "normal")),
                reason=item.get("reason", ""),
                confidence=float(item.get("confidence", 0.8)),
            ))
        except (ValueError, KeyError):
            continue
    return result


_SYSTEM_PROMPT = """Classify maintenance issues into vendor categories.

Available categories: hvac, plumbing, electrical, locksmith,
appliance_repair, pest_control, general_maintenance, cleaning.

Urgency levels: low, normal, urgent.

Return JSON:
{
    "vendor_categories": [
        {"category": "electrical", "urgency": "urgent", "reason": "...", "confidence": 0.9}
    ],
    "overall_urgency": "urgent",
    "reasoning": "brief explanation"
}

Multiple categories possible (e.g. water leak may need plumbing + cleaning).
"""
