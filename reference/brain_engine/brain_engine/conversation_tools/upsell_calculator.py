"""Upsell calculator tool — early/late check-in/out pricing.

Wraps the existing UpsellEngine to provide pricing for
upsell requests within the conversation agent.
"""

from __future__ import annotations

import logging

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


@tool(description=(
    "Calculate pricing for early check-in, late check-out, "
    "or late check-in requests. Use when guest asks to arrive "
    "early, leave late, or modify check-in/out times. "
    "Returns pricing and clarification questions if needed. "
    "Do NOT use for general availability or new booking dates — use availability_checker. "
    "Do NOT use for property info or amenities — use rag_document_search. "
    "Do NOT use when guest just asks what time check-in is (that is rag_document_search)."
))
async def upsell_calculator(
    upsell_type: str,
    requested_time: str = "",
    runtime: ToolRuntime | None = None,
) -> str:
    """Calculate upsell pricing.

    Args:
        upsell_type: One of 'EarlyCheckIn', 'LateCheckOut', 'LateCheckIn'.
        requested_time: Requested time (e.g. '10:00', '18:00').
        runtime: Injected runtime context.

    Returns:
        Pricing info or clarification question.
    """
    property_id = runtime.config.get("property_id", "") if runtime else ""
    reservation_id = runtime.config.get("reservation_id", "") if runtime else ""

    if not requested_time:
        return _ask_clarification(upsell_type)

    try:
        from brain_engine.smart_engine.upsell_engine import UpsellEngine
        engine = UpsellEngine()
        result = await engine.calculate(
            property_id=property_id,
            reservation_id=reservation_id,
            upsell_type=upsell_type,
            requested_time=requested_time,
        )
        return _format_upsell(result, upsell_type)
    except Exception as exc:
        logger.error("Upsell calculation failed: %s", exc)
        return "Let me check the pricing and get back to you shortly."


def _ask_clarification(upsell_type: str) -> str:
    """Generate clarification question when time not specified.

    Args:
        upsell_type: Type of upsell request.

    Returns:
        Clarification question text.
    """
    questions = {
        "EarlyCheckIn": "What time would you like to check in?",
        "LateCheckOut": "What time would you like to check out?",
        "LateCheckIn": "What time will you be arriving?",
    }
    return questions.get(upsell_type, "What time would you prefer?")


def _format_upsell(result: dict, upsell_type: str) -> str:
    """Format upsell result for the agent.

    Args:
        result: Pricing result dict.
        upsell_type: Type of upsell.

    Returns:
        Formatted pricing string.
    """
    price = result.get("price", 0)
    currency = result.get("currency", "")
    available = result.get("available", False)

    if not available:
        return f"Unfortunately, {upsell_type} is not available for this booking."

    return f"{upsell_type} is available for {price} {currency}."
