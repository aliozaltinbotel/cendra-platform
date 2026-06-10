"""Customer Tags Service — semantic message tagging.

Matches guest messages against customer-defined semantic tags.
Tags are used for PM dashboard categorization and filtering.
Exposed as a standalone endpoint and integrated into postprocessing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm
from pydantic import BaseModel, Field

from brain_engine.customer.models import CustomerTag

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.1


class CustomerTagsRequest(BaseModel):
    """Input to POST /api/v1/customer-tags."""

    customer_id: str
    org_id: str = ""
    message: str = ""
    ai_response: str = ""
    tags: list[CustomerTag] = Field(default_factory=list)


class MatchedTag(BaseModel):
    """A tag that matched the message."""

    title: str
    icon: str = ""
    confidence: float = 0.0
    reason: str = ""


class CustomerTagsResponse(BaseModel):
    """Output of customer tag matching."""

    status: bool = True
    matched_tags: list[MatchedTag] = Field(default_factory=list)
    error: str | None = None


async def match_customer_tags(
    request: CustomerTagsRequest,
) -> CustomerTagsResponse:
    """Match a guest message against customer-defined tags.

    Uses LLM semantic matching to determine which tags apply
    to the guest's message.

    Args:
        request: Message and available tags.

    Returns:
        List of matched tags with confidence.
    """
    if not request.tags:
        return CustomerTagsResponse(matched_tags=[])

    tags_desc = "\n".join(
        f"- {t.title}: {t.description}"
        + (f" (keywords: {', '.join(t.keywords)})" if t.keywords else "")
        for t in request.tags
    )

    prompt = (
        f"Guest message: {request.message}\n\n"
        f"AI response: {request.ai_response}\n\n"
        f"Available tags:\n{tags_desc}"
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=400,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        matched = _parse_matches(data.get("matched_tags", []), request.tags)
        return CustomerTagsResponse(matched_tags=matched)
    except Exception as exc:
        logger.error("Customer tag matching failed: %s", exc)
        return CustomerTagsResponse(status=False, error=str(exc))


def _parse_matches(
    raw: list[dict[str, Any]],
    available: list[CustomerTag],
) -> list[MatchedTag]:
    """Parse and validate tag matches against available tags.

    Args:
        raw: Raw match dicts from LLM.
        available: Customer's defined tags.

    Returns:
        Validated MatchedTag list.
    """
    available_titles = {t.title.lower(): t for t in available}
    result: list[MatchedTag] = []

    for item in raw[:10]:
        title = item.get("title", "")
        tag = available_titles.get(title.lower())
        if not tag:
            continue

        confidence = float(item.get("confidence", 0.5))
        if confidence < 0.3:
            continue

        result.append(MatchedTag(
            title=tag.title,
            icon=tag.icon,
            confidence=confidence,
            reason=item.get("reason", ""),
        ))

    return result


_SYSTEM_PROMPT = """Match a guest message against available semantic tags.

For each tag, determine if it applies to the guest's message.
Only match tags with confidence >= 0.3.

Return JSON:
{
    "matched_tags": [
        {"title": "Cleanliness Complaint", "confidence": 0.95, "reason": "Guest mentions dirty bathroom"},
        {"title": "Parking Question", "confidence": 0.8, "reason": "Guest asks about parking"}
    ]
}

Rules:
- Match based on semantic meaning, not just keyword overlap
- Consider both the guest message AND the AI response
- Multiple tags can match simultaneously
- Use confidence 0.0-1.0
"""
