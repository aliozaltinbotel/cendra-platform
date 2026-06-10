"""OPS message verifier — checks for fabricated information.

Verifies that a generated message contains ONLY information
from the provided context and hasn't invented any details.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from brain_engine.ops.models import OpsVerifyRequest, OpsVerifyResponse

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.0


async def verify_ops_message(
    request: OpsVerifyRequest,
) -> OpsVerifyResponse:
    """Verify a generated message for fabricated content.

    Args:
        request: Verification request with message and context.

    Returns:
        Verification result with safety flag and issues.
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
        return OpsVerifyResponse(
            is_safe=data.get("is_safe", True),
            issues=data.get("issues", []),
        )
    except Exception as exc:
        logger.error("Message verification failed: %s", exc)
        return OpsVerifyResponse(
            status=False,
            is_safe=False,
            issues=[f"Verification failed: {exc}"],
            error=str(exc),
        )


def _build_prompt(request: OpsVerifyRequest) -> str:
    """Build verification prompt.

    Args:
        request: Verification request.

    Returns:
        Formatted prompt.
    """
    context_str = json.dumps(
        request.provided_context, indent=2, ensure_ascii=False,
    )

    return (
        f"Recipient type: {request.recipient_type.value}\n\n"
        f"Generated message:\n{request.generated_message}\n\n"
        f"Provided context:\n{context_str}"
    )


_SYSTEM_PROMPT = """Verify that a generated operations message is safe to send.

Check for:
1. Invented information not present in the provided context
2. Unsupported promises or commitments
3. Incorrect details (wrong names, numbers, dates)
4. Assumptions presented as facts
5. Sensitive information exposure

Return JSON:
{"is_safe": true, "issues": []}

If problems found:
{"is_safe": false, "issues": ["Invented phone number not in context", ...]}
"""
