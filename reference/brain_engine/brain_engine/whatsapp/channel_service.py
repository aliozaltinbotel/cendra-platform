"""WhatsApp Channel Service — booking assistant without property context.

Handles guest messages from WhatsApp when no property or booking
is known. Extracts booking parameters (city, dates, guests, budget)
through conversation, then searches for matching properties.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.2


# ── Models ───────────────────────────────────────────────────── #


class BookingParameters(BaseModel):
    """Extracted booking parameters from conversation."""

    city: str = ""
    guest_count: int = 0
    check_in_date: str = ""
    check_out_date: str = ""
    budget_min: float = 0.0
    budget_max: float = 0.0
    special_requests: str = ""

    def is_complete(self) -> bool:
        """Check if all required parameters are collected."""
        return bool(
            self.city
            and self.check_in_date
            and self.check_out_date
            and self.guest_count > 0
        )

    def get_missing(self) -> list[str]:
        """List parameters that still need to be collected."""
        missing: list[str] = []
        if not self.city:
            missing.append("city/destination")
        if not self.check_in_date:
            missing.append("check-in date")
        if not self.check_out_date:
            missing.append("check-out date")
        if self.guest_count <= 0:
            missing.append("number of guests")
        return missing


class WhatsAppRequest(BaseModel):
    """Input for WhatsApp channel processing."""

    customer_id: str
    chat_id: str = ""
    message: str = ""
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    current_parameters: BookingParameters = Field(
        default_factory=BookingParameters,
    )


class WhatsAppResponse(BaseModel):
    """Output of WhatsApp channel processing."""

    status: bool = True
    response_message: str = ""
    parameters: BookingParameters = Field(default_factory=BookingParameters)
    properties_found: list[dict[str, Any]] = Field(default_factory=list)
    is_booking_ready: bool = False
    error: str | None = None


# ── Service ──────────────────────────────────────────────────── #


async def process_whatsapp_message(
    request: WhatsAppRequest,
) -> WhatsAppResponse:
    """Process a WhatsApp message without property/booking context.

    Flow:
    1. Extract/update booking parameters from message
    2. If parameters complete → search properties
    3. If incomplete → generate follow-up question

    Args:
        request: WhatsApp channel request.

    Returns:
        Response with updated parameters and/or property results.
    """
    # Step 1: Extract parameters
    updated_params = await _extract_parameters(
        request.message,
        request.conversation_history,
        request.current_parameters,
    )

    # Step 2: Check completeness
    if updated_params.is_complete():
        properties = await _search_properties(request.customer_id, updated_params)
        response_msg = await _generate_results_message(
            updated_params, properties,
        )
        return WhatsAppResponse(
            response_message=response_msg,
            parameters=updated_params,
            properties_found=properties,
            is_booking_ready=len(properties) > 0,
        )

    # Step 3: Generate follow-up
    follow_up = await _generate_follow_up(
        request.message,
        request.conversation_history,
        updated_params,
    )
    return WhatsAppResponse(
        response_message=follow_up,
        parameters=updated_params,
    )


async def _extract_parameters(
    message: str,
    history: list[dict[str, str]],
    current: BookingParameters,
) -> BookingParameters:
    """Extract booking parameters from the latest message.

    Merges newly extracted parameters with existing ones.

    Args:
        message: Latest guest message.
        history: Conversation history.
        current: Already collected parameters.

    Returns:
        Updated BookingParameters.
    """
    current_json = current.model_dump_json()
    history_text = "\n".join(
        f"[{m.get('role', '?')}]: {m.get('content', '')[:200]}"
        for m in history[-5:]
    )

    prompt = (
        f"Current parameters: {current_json}\n\n"
        f"Conversation:\n{history_text}\n\n"
        f"Latest message: {message}\n\n"
        "Extract any NEW booking parameters from the latest message."
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return _merge_parameters(current, data)
    except Exception:
        logger.error("Parameter extraction failed", exc_info=True)
        return current


def _merge_parameters(
    current: BookingParameters,
    extracted: dict,
) -> BookingParameters:
    """Merge extracted parameters into current, preferring new values.

    Args:
        current: Existing parameters.
        extracted: Newly extracted dict.

    Returns:
        Merged BookingParameters.
    """
    return BookingParameters(
        city=extracted.get("city") or current.city,
        guest_count=extracted.get("guest_count") or current.guest_count,
        check_in_date=extracted.get("check_in_date") or current.check_in_date,
        check_out_date=extracted.get("check_out_date") or current.check_out_date,
        budget_min=extracted.get("budget_min") or current.budget_min,
        budget_max=extracted.get("budget_max") or current.budget_max,
        special_requests=extracted.get("special_requests") or current.special_requests,
    )


async def _search_properties(
    customer_id: str,
    params: BookingParameters,
) -> list[dict[str, Any]]:
    """Search for matching properties.

    Args:
        customer_id: Customer for property filtering.
        params: Complete booking parameters.

    Returns:
        List of matching property dicts.
    """
    # Multi-property availability search is not exposed by the unified
    # GraphQL layer yet; until it ships we return an empty list so
    # callers fall back to a deferral instead of a fabricated mockup.
    del customer_id, params  # unused while GraphQL surface is missing
    logger.debug(
        "whatsapp_search_properties_disabled — awaiting GraphQL search"
    )
    return []


async def _generate_follow_up(
    message: str,
    history: list[dict[str, str]],
    params: BookingParameters,
) -> str:
    """Generate a follow-up question for missing parameters.

    Args:
        message: Latest guest message.
        history: Conversation history.
        params: Current (incomplete) parameters.

    Returns:
        Natural follow-up question text.
    """
    missing = params.get_missing()
    prompt = (
        f"Guest said: {message}\n"
        f"Already collected: {params.model_dump_json()}\n"
        f"Still missing: {', '.join(missing)}\n\n"
        "Generate a natural follow-up question to collect the missing info."
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _FOLLOWUP_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=200,
        )
        return response.choices[0].message.content or ""
    except Exception:
        return f"Could you please provide: {', '.join(missing)}?"


async def _generate_results_message(
    params: BookingParameters,
    properties: list[dict[str, Any]],
) -> str:
    """Generate a message presenting search results.

    Args:
        params: Booking parameters used for search.
        properties: Found properties.

    Returns:
        Formatted results message.
    """
    if not properties:
        return (
            f"I searched for properties in {params.city} "
            f"({params.check_in_date} to {params.check_out_date}) "
            f"for {params.guest_count} guests, but nothing matched. "
            "Would you like to try different dates or a different location?"
        )

    lines = [
        f"Great news! I found {len(properties)} properties in {params.city}:",
        "",
    ]
    for i, prop in enumerate(properties[:5], 1):
        name = prop.get("name", "Property")
        price = prop.get("price_per_night", "N/A")
        currency = prop.get("currency", "")
        lines.append(f"{i}. {name} — {price} {currency}/night")

    lines.append("\nWould you like to book one of these?")
    return "\n".join(lines)


_EXTRACT_SYSTEM = """Extract booking parameters from a guest message.

Return JSON with ONLY the newly extracted fields:
{
    "city": "Istanbul",
    "guest_count": 2,
    "check_in_date": "2026-04-20",
    "check_out_date": "2026-04-25",
    "budget_min": 0,
    "budget_max": 200,
    "special_requests": "pet-friendly"
}

Only include fields that the guest EXPLICITLY mentioned.
Use null for fields not mentioned in this message.
Resolve relative dates using context.
"""

_FOLLOWUP_SYSTEM = """Generate a natural, conversational follow-up question.

Rules:
- Ask for ALL missing info at once, not one at a time
- Be warm and friendly
- Use sparse emojis (0-1)
- If greeting detected, welcome first then ask
- Keep it concise
"""
