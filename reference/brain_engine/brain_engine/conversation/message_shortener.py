"""Message Shortener — compress messages for WhatsApp 1550 char limit.

Uses LLM to intelligently shorten messages while preserving
all critical information (dates, times, numbers, codes).
"""

from __future__ import annotations

import json
import logging

import litellm

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.1
_WHATSAPP_LIMIT = 1550


async def shorten_for_whatsapp(message: str) -> str:
    """Shorten a message to fit WhatsApp's character limit.

    If the message is already under the limit, returns it unchanged.
    Otherwise, uses LLM to compress while preserving key details.

    Args:
        message: Original message text.

    Returns:
        Shortened message (≤1550 chars) or original if already short.
    """
    if len(message) <= _WHATSAPP_LIMIT:
        return message

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            temperature=_TEMPERATURE,
            max_tokens=600,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        shortened = data.get("shortened_message", message)

        if len(shortened) > _WHATSAPP_LIMIT:
            shortened = shortened[:_WHATSAPP_LIMIT - 3] + "..."

        return shortened
    except Exception:
        logger.warning("Message shortening failed, truncating", exc_info=True)
        return message[:_WHATSAPP_LIMIT - 3] + "..."


_SYSTEM_PROMPT = f"""Shorten the message to under {_WHATSAPP_LIMIT} characters for WhatsApp.

Rules:
- NEVER remove or change dates, times, numbers, codes, addresses, or prices
- Maintain the same tone and meaning
- Remove redundant phrasing, filler words, and excessive politeness
- Combine sentences where possible
- If already under {_WHATSAPP_LIMIT} chars, return unchanged

Return JSON:
{{"shortened_message": "..."}}
"""
