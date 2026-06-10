"""OPS message generator — creates messages for cleaners, vendors, owners, guests.

Generates context-appropriate messages using LLM with recipient-specific
tone and content rules. Supports 5 message types across 4 recipient types.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from brain_engine.ops.models import (
    OpsGenerateRequest,
    OpsGenerateResponse,
    OpsUrgency,
    RecipientType,
)

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.2


async def generate_ops_message(
    request: OpsGenerateRequest,
) -> OpsGenerateResponse:
    """Generate an operations message for the specified recipient.

    Args:
        request: Message generation request with context.

    Returns:
        Generated message with metadata.
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
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return OpsGenerateResponse(
            message=data.get("message", ""),
            requires_approval=data.get("requires_approval", False),
            suggested_urgency=OpsUrgency(
                data.get("urgency", "normal"),
            ),
            detected_language=data.get("language", request.language_override or "en"),
        )
    except Exception as exc:
        logger.error("OPS message generation failed: %s", exc)
        return OpsGenerateResponse(status=False, error=str(exc))


def _build_prompt(request: OpsGenerateRequest) -> str:
    """Build the generation prompt from request data.

    Args:
        request: Message generation request.

    Returns:
        Formatted prompt string.
    """
    context_str = json.dumps(request.context, indent=2, ensure_ascii=False)
    lang = request.language_override or "English"

    return (
        f"Generate a {request.message_type.value} message.\n\n"
        f"Recipient type: {request.recipient_type.value}\n"
        f"Language: {lang}\n\n"
        f"Context:\n{context_str}\n\n"
        "Return JSON:\n"
        '{"message": "...", "requires_approval": false, '
        '"urgency": "normal", "language": "en"}'
    )


_SYSTEM_PROMPT = """You generate professional operations messages for property management.

Tone by recipient:
- CLEANER: Direct, practical, include address and access codes
- VENDOR: Professional, include issue details and urgency
- OWNER: Business-relevant, include cost implications
- GUEST: Warm, reassuring, no internal details

Rules:
- Use ONLY information from the provided context
- Never invent names, phone numbers, or addresses
- Be concise and actionable
- Include all relevant details (dates, times, codes)
- Flag requires_approval=true for cost commitments or sensitive messages
"""
