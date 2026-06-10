"""Availability Date Extractor — context-aware date parsing from conversations.

Extracts check-in/check-out dates, guest count, and booking intent
from natural language messages. Handles relative dates ("next week"),
multi-language input, and ambiguous date expressions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

import litellm

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o"
_TEMPERATURE = 0.2


@dataclass
class DateExtractionResult:
    """Extracted booking dates and parameters.

    Attributes:
        check_in: Extracted check-in date (YYYY-MM-DD) or empty.
        check_out: Extracted check-out date (YYYY-MM-DD) or empty.
        is_interval_clear: True if both dates are unambiguous.
        guests: Number of guests (0 = not mentioned).
        is_general_policy_question: True if asking about rules, not dates.
        is_next_available_date_question: True if asking "when is next free".
        raw_expression: Original date expression from message.
    """

    check_in: str = ""
    check_out: str = ""
    is_interval_clear: bool = False
    guests: int = 0
    is_general_policy_question: bool = False
    is_next_available_date_question: bool = False
    raw_expression: str = ""


async def extract_dates(
    message: str,
    conversation_history: list[dict[str, str]] | None = None,
    current_date: str = "",
    existing_booking: dict | None = None,
) -> DateExtractionResult:
    """Extract availability dates from a guest message.

    Uses LLM with context-aware date resolution. Handles:
    - Explicit dates: "March 15-20"
    - Relative dates: "next week", "this weekend"
    - Vague expressions: "a few nights", "around Easter"
    - Modifications: "can we extend by 2 nights"
    - Multi-language dates

    Args:
        message: Guest message text.
        conversation_history: Previous messages for context.
        current_date: Today's date (YYYY-MM-DD) for relative resolution.
        existing_booking: Current booking data if modifying.

    Returns:
        DateExtractionResult with parsed dates.
    """
    if not current_date:
        current_date = date.today().isoformat()

    prompt = _build_prompt(
        message, conversation_history, current_date, existing_booking,
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return _parse_result(data)
    except Exception:
        logger.error("Date extraction failed", exc_info=True)
        return DateExtractionResult()


def _build_prompt(
    message: str,
    history: list[dict[str, str]] | None,
    current_date: str,
    existing_booking: dict | None,
) -> str:
    """Build the extraction prompt with full context.

    Args:
        message: Guest message.
        history: Conversation history.
        current_date: Today's date.
        existing_booking: Existing booking info.

    Returns:
        Formatted prompt string.
    """
    parts = [f"Current date: {current_date}", f"Guest message: {message}"]

    if history:
        recent = history[-5:]
        history_text = "\n".join(
            f"  [{m.get('role', '?')}]: {m.get('content', '')[:200]}"
            for m in recent
        )
        parts.append(f"Recent conversation:\n{history_text}")

    if existing_booking:
        parts.append(
            f"Existing booking: check-in={existing_booking.get('check_in', '')}, "
            f"check-out={existing_booking.get('check_out', '')}, "
            f"guests={existing_booking.get('guests', '')}"
        )

    return "\n\n".join(parts)


def _parse_result(data: dict) -> DateExtractionResult:
    """Parse LLM JSON response into DateExtractionResult.

    Args:
        data: Parsed JSON dict from LLM.

    Returns:
        Validated DateExtractionResult.
    """
    check_in = data.get("availability_start_date", "") or ""
    check_out = data.get("availability_end_date", "") or ""

    is_clear = bool(check_in and check_out)
    if data.get("is_availability_interval_clear") is not None:
        is_clear = data["is_availability_interval_clear"]

    guests = 0
    raw_guests = data.get("guests")
    if raw_guests is not None:
        try:
            guests = int(raw_guests)
        except (ValueError, TypeError):
            guests = 0

    return DateExtractionResult(
        check_in=check_in,
        check_out=check_out,
        is_interval_clear=is_clear,
        guests=guests,
        is_general_policy_question=data.get("is_general_policy_question", False),
        is_next_available_date_question=data.get("is_next_available_date_question", False),
        raw_expression=data.get("raw_date_expression", ""),
    )


_SYSTEM_PROMPT = """Extract booking dates from a guest message.

You MUST resolve relative dates using the provided current date:
- "next week" → Monday-Sunday of next week
- "this weekend" → upcoming Saturday-Sunday
- "tomorrow" → current_date + 1 day
- "in 2 weeks" → current_date + 14 days
- "around Easter" → approximate dates
- "extend by 2 nights" → existing check_out + 2 days

Date priority rules:
- If guest mentions new dates AND has existing booking, new dates take priority
- If guest asks to "extend", calculate from existing check_out
- If guest asks for "late checkout", that's NOT a date change (it's upsell)
- If guest asks for "early checkin", that's NOT a date change (it's upsell)

Return JSON:
{
    "availability_start_date": "2026-04-15",
    "availability_end_date": "2026-04-20",
    "is_availability_interval_clear": true,
    "guests": 2,
    "is_general_policy_question": false,
    "is_next_available_date_question": false,
    "raw_date_expression": "April 15 to 20"
}

If dates cannot be determined, return empty strings.
If asking about policy (not specific dates), set is_general_policy_question=true.
If asking "when is next available", set is_next_available_date_question=true.
"""
