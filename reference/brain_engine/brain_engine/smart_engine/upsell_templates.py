"""Upsell Templates — generate approved upsell messages.

Templates and LLM generation for early check-in, late check-out,
and other upsell offers after PM approval.
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


class UpsellTemplateRequest(BaseModel):
    """Input for upsell message generation."""

    upsell_type: str = Field(
        ..., description="EarlyCheckIn, LateCheckOut, LateCheckIn",
    )
    new_check_in: str = Field(default="", description="New check-in time HH:MM")
    new_check_out: str = Field(default="", description="New check-out time HH:MM")
    price: float = 0.0
    currency: str = "USD"
    approved: bool = True
    guest_name: str = ""
    guest_language: str = "en"
    property_name: str = ""


class UpsellTemplateResponse(BaseModel):
    """Output of upsell template generation."""

    status: bool = True
    message: str = ""
    error: str | None = None


async def generate_upsell_message(
    request: UpsellTemplateRequest,
) -> UpsellTemplateResponse:
    """Generate an upsell offer or confirmation message.

    If approved=True, generates enthusiastic confirmation.
    If approved=False, generates regret + alternatives.

    Args:
        request: Upsell template parameters.

    Returns:
        Generated upsell message.
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
            max_tokens=400,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return UpsellTemplateResponse(message=data.get("upsell_response", ""))
    except Exception as exc:
        logger.error("Upsell template generation failed: %s", exc)
        return UpsellTemplateResponse(status=False, error=str(exc))


def _build_prompt(request: UpsellTemplateRequest) -> str:
    """Build the generation prompt.

    Args:
        request: Upsell parameters.

    Returns:
        Formatted prompt.
    """
    time_info = ""
    if request.upsell_type == "EarlyCheckIn" and request.new_check_in:
        time_info = f"New check-in time: {request.new_check_in}"
    elif request.upsell_type in ("LateCheckOut", "LateCheckIn"):
        time_val = request.new_check_out or request.new_check_in
        time_info = f"New time: {time_val}"

    return (
        f"Upsell type: {request.upsell_type}\n"
        f"Approved: {request.approved}\n"
        f"{time_info}\n"
        f"Price: {request.price} {request.currency}\n"
        f"Guest: {request.guest_name or 'Guest'}\n"
        f"Property: {request.property_name}\n"
        f"Language: {request.guest_language}"
    )


_SYSTEM_PROMPT = """Generate an upsell response message for a guest.

If APPROVED (approved=true):
- Enthusiastic confirmation
- Include exact time and price
- Thank them for choosing the upgrade
- No signatures or sign-offs

If NOT APPROVED (approved=false):
- Express regret professionally
- Offer alternative if possible
- Maintain warm tone
- No apologies that sound like it's our fault

Rules:
- NEVER invent info not provided
- Include exact pricing
- Respond in the guest's language
- Keep under 100 words

Return JSON:
{"upsell_response": "Your message here"}
"""
