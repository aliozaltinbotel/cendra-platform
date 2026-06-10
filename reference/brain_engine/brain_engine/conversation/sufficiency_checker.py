"""Answer Sufficiency Checker — validates response completeness.

Uses a lightweight LLM pass to determine if the AI response
actually answers the guest's question or just defers/deflects.
"""

from __future__ import annotations

import json
import logging

import litellm

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.1


async def check_sufficiency(
    guest_message: str,
    ai_response: str,
) -> SufficiencyResult:
    """Check if the AI response sufficiently answers the guest.

    Args:
        guest_message: The guest's original question.
        ai_response: The AI's generated response.

    Returns:
        SufficiencyResult with pass/fail and reasoning.
    """
    prompt = (
        f"Guest question:\n{guest_message}\n\n"
        f"AI response:\n{ai_response}\n\n"
        "Does the AI response sufficiently address the guest's question?"
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return SufficiencyResult(
            is_sufficient=data.get("sufficient_response", True),
            reasoning=data.get("reasoning", ""),
            missing_info=data.get("missing_information", ""),
            intervention_reason=data.get("intervention_reason", ""),
        )
    except Exception:
        logger.debug("Sufficiency check failed", exc_info=True)
        return SufficiencyResult(is_sufficient=True)


class SufficiencyResult:
    """Result of answer sufficiency check.

    Attributes:
        is_sufficient: True if response adequately answers the question.
        reasoning: Why the response is/isn't sufficient.
        missing_info: What information is still missing.
        intervention_reason: Why human help may be needed.
    """

    __slots__ = ("is_sufficient", "reasoning", "missing_info", "intervention_reason")

    def __init__(
        self,
        is_sufficient: bool = True,
        reasoning: str = "",
        missing_info: str = "",
        intervention_reason: str = "",
    ) -> None:
        self.is_sufficient = is_sufficient
        self.reasoning = reasoning
        self.missing_info = missing_info
        self.intervention_reason = intervention_reason


_SYSTEM_PROMPT = """Evaluate if the AI response sufficiently addresses the guest's question.

A response IS sufficient if it:
- Provides definitive information that directly answers the question
- Gives clear instructions or details the guest can act on
- Confirms unavailability with a concrete alternative

A response is NOT sufficient if it:
- Only says "we'll check" or "let me find out" without actual info
- Deflects or avoids the question
- Provides partial info while the core question is unanswered
- Makes promises to follow up without answering

Return JSON:
{
    "sufficient_response": true,
    "reasoning": "The response provides the WiFi password directly",
    "missing_information": "",
    "intervention_reason": ""
}

If NOT sufficient:
{
    "sufficient_response": false,
    "reasoning": "Response only promises to check, doesn't answer",
    "missing_information": "WiFi password not provided",
    "intervention_reason": "Guest needs WiFi password, AI doesn't have it"
}
"""
