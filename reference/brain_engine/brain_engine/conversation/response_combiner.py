"""Response Combiner — merges multiple flow outputs into one guest message.

When the pipeline runs multiple parallel flows (RAG, availability,
upsell, location, listing), this service combines their results
into a single coherent response for the guest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.2


@dataclass
class FlowResponses:
    """Outputs from individual conversation flows.

    Each field may be empty if that flow wasn't triggered.
    """

    listing_answer: str = ""
    reservation_answer: str = ""
    rag_answer: str = ""
    availability_answer: str = ""
    upsell_answer: str = ""
    location_answer: str = ""
    alternative_property_answer: str = ""
    emergency_answer: str = ""


@dataclass
class CombinedResult:
    """Result of combining multiple flow responses.

    Attributes:
        message: Final combined response text.
        source_flows: Which flows contributed to the response.
        was_helpful: Quality score 0.0-1.0.
        completeness: full, partial, or none.
        is_need_attention: Whether PM should review.
        send_status: Whether to auto-send.
        message_tags: Assigned message tags.
    """

    message: str = ""
    source_flows: list[str] = field(default_factory=list)
    was_helpful: float = 1.0
    completeness: str = "full"
    is_need_attention: bool = False
    send_status: bool = True
    message_tags: list[str] = field(default_factory=list)


async def combine_responses(
    guest_message: str,
    flows: FlowResponses,
    tone_instructions: str = "",
    language: str = "en",
) -> CombinedResult:
    """Combine multiple flow responses into one coherent message.

    Priority order:
    1. Emergency (always first)
    2. Upsell (pricing info preserved exactly)
    3. Availability
    4. Reservation + Listing
    5. RAG (property knowledge)
    6. Location
    7. Alternative properties

    Args:
        guest_message: Original guest question.
        flows: Outputs from all flows.
        tone_instructions: Tone style instructions.
        language: Response language.

    Returns:
        CombinedResult with merged message and metadata.
    """
    active_flows = _get_active_flows(flows)

    if not active_flows:
        return CombinedResult(
            message="",
            completeness="none",
            is_need_attention=True,
            send_status=False,
        )

    if len(active_flows) == 1:
        flow_name, flow_text = active_flows[0]
        return CombinedResult(
            message=flow_text,
            source_flows=[flow_name],
        )

    return await _combine_via_llm(guest_message, active_flows, tone_instructions, language)


def _get_active_flows(flows: FlowResponses) -> list[tuple[str, str]]:
    """Extract non-empty flows in priority order.

    Args:
        flows: All flow responses.

    Returns:
        List of (flow_name, flow_text) tuples, priority-ordered.
    """
    priority_order = [
        ("emergency", flows.emergency_answer),
        ("upsell", flows.upsell_answer),
        ("availability", flows.availability_answer),
        ("reservation", flows.reservation_answer),
        ("listing", flows.listing_answer),
        ("rag", flows.rag_answer),
        ("location", flows.location_answer),
        ("alternative_property", flows.alternative_property_answer),
    ]
    return [(name, text) for name, text in priority_order if text.strip()]


async def _combine_via_llm(
    guest_message: str,
    active_flows: list[tuple[str, str]],
    tone_instructions: str,
    language: str,
) -> CombinedResult:
    """Use LLM to combine multiple flow responses naturally.

    Args:
        guest_message: Guest's question.
        active_flows: Active (name, text) pairs.
        tone_instructions: Tone style.
        language: Response language.

    Returns:
        CombinedResult with LLM-merged message.
    """
    flow_sections = "\n\n".join(
        f"--- {name.upper()} FLOW ---\n{text}"
        for name, text in active_flows
    )

    prompt = (
        f"Guest message: {guest_message}\n\n"
        f"Flow responses to combine:\n{flow_sections}\n\n"
        f"Tone: {tone_instructions or 'friendly professional'}\n"
        f"Language: {language}"
    )

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _COMBINE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        return CombinedResult(
            message=data.get("combined_response", ""),
            source_flows=[name for name, _ in active_flows],
            was_helpful=float(data.get("was_helpful", 1.0)),
            completeness=data.get("completeness", "full"),
            is_need_attention=data.get("is_need_attention", False),
            send_status=data.get("send_status", True),
            message_tags=data.get("message_tags", []),
        )
    except Exception:
        logger.error("Response combination failed", exc_info=True)
        # Fallback: concatenate with line breaks
        combined = "\n\n".join(text for _, text in active_flows)
        return CombinedResult(
            message=combined,
            source_flows=[name for name, _ in active_flows],
        )


_COMBINE_SYSTEM = """Combine multiple AI response flows into one coherent guest message.

Priority rules:
1. UPSELL responses with pricing → preserve pricing EXACTLY, never modify numbers
2. AVAILABILITY → include dates and pricing accurately
3. PROPERTY INFO → use definitive info from listing/RAG
4. LOCATION → integrate naturally
5. ALTERNATIVES → mention only if relevant

Combination rules:
- Address ALL topics the guest asked about
- DO NOT repeat information across flows
- DO NOT fabricate details not in any flow
- Use natural transitions between topics
- Match the specified tone and language
- If any flow says "we'll check", keep that honesty

Return JSON:
{
    "combined_response": "Your merged message here",
    "was_helpful": 0.8,
    "completeness": "full",
    "is_need_attention": false,
    "send_status": true,
    "message_tags": ["PROPERTY_INFO_REQUEST", "QUESTION_ANSWERED"]
}

completeness: "full" if all questions answered, "partial" if some deferred, "none" if nothing answered.
"""
