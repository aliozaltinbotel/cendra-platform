"""Gap Night Offer — generates discount offers for empty calendar gaps.

Identifies gap nights between bookings and generates personalized
discount offers to fill them, maximizing occupancy.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import litellm
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.3


class GapNightRequest(BaseModel):
    """Input for gap night offer generation."""

    customer_id: str
    property_id: str
    gap_start: str = Field(..., description="Gap start date YYYY-MM-DD")
    gap_end: str = Field(..., description="Gap end date YYYY-MM-DD")
    regular_price: float = 0.0
    currency: str = "USD"
    guest_name: str = ""
    guest_language: str = "en"
    discount_percent: float = Field(default=15.0, ge=0, le=50)
    min_nights: int = Field(default=1, ge=1)


class GapNightResponse(BaseModel):
    """Output of gap night offer generation."""

    status: bool = True
    offer_message: str = ""
    discounted_price: float = 0.0
    gap_nights: int = 0
    total_savings: float = 0.0
    error: str | None = None


async def generate_gap_night_offer(
    request: GapNightRequest,
) -> GapNightResponse:
    """Generate a personalized gap night discount offer.

    Args:
        request: Gap night offer parameters.

    Returns:
        Offer with message, pricing, and savings calculation.
    """
    gap_nights = _calculate_gap_nights(request.gap_start, request.gap_end)
    if gap_nights < request.min_nights:
        return GapNightResponse(
            status=False,
            error=f"Gap ({gap_nights} nights) is below minimum ({request.min_nights})",
        )

    discounted = round(
        request.regular_price * (1 - request.discount_percent / 100), 2,
    )
    total_savings = round(
        (request.regular_price - discounted) * gap_nights, 2,
    )

    try:
        offer_msg = await _generate_offer_message(request, gap_nights, discounted)
        return GapNightResponse(
            offer_message=offer_msg,
            discounted_price=discounted,
            gap_nights=gap_nights,
            total_savings=total_savings,
        )
    except Exception as exc:
        logger.error("Gap night offer generation failed: %s", exc)
        return GapNightResponse(status=False, error=str(exc))


def _calculate_gap_nights(start: str, end: str) -> int:
    """Calculate number of nights in the gap.

    Args:
        start: Gap start date.
        end: Gap end date.

    Returns:
        Number of nights.
    """
    try:
        d_start = date.fromisoformat(start)
        d_end = date.fromisoformat(end)
        return max(0, (d_end - d_start).days)
    except ValueError:
        return 0


async def _generate_offer_message(
    request: GapNightRequest,
    gap_nights: int,
    discounted_price: float,
) -> str:
    """Generate the offer message text via LLM.

    Args:
        request: Offer parameters.
        gap_nights: Number of gap nights.
        discounted_price: Price after discount.

    Returns:
        Personalized offer message.
    """
    prompt = (
        f"Property: {request.property_id}\n"
        f"Guest: {request.guest_name or 'Guest'}\n"
        f"Gap dates: {request.gap_start} to {request.gap_end} ({gap_nights} nights)\n"
        f"Regular price: {request.regular_price} {request.currency}/night\n"
        f"Discount: {request.discount_percent}%\n"
        f"Discounted price: {discounted_price} {request.currency}/night\n"
        f"Language: {request.guest_language}\n\n"
        "Generate a warm, personalized offer message."
    )

    response = await litellm.acompletion(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=_TEMPERATURE,
        max_tokens=400,
    )

    return response.choices[0].message.content or ""


_SYSTEM_PROMPT = """Generate a warm gap night discount offer for a guest.

Rules:
- Include exact dates and pricing
- Mention the discount percentage and savings
- Be warm and inviting, not pushy
- Keep it concise (under 200 words)
- Respond in the guest's language
- Include a clear call to action
"""
