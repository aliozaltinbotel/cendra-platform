"""OPS Parse PM Instruction — extracts action from PM's terse messages.

Parses informal PM messages like "send Ahmet" or "approved" into
structured actions: assign_contact, approve, reject, memory_note, etc.
Standalone endpoint separate from the full PM Agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from brain_engine.ops.models import (
    OpsParsePmInstructionData,
    OpsParsePmInstructionRequest,
    OpsParsePmInstructionResponse,
    PmAction,
)

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TEMPERATURE = 0.1


async def parse_pm_instruction(
    request: OpsParsePmInstructionRequest,
) -> OpsParsePmInstructionResponse:
    """Parse a PM's informal instruction into a structured action.

    Handles terse messages like:
    - "send Ahmet" → assign_contact
    - "approved" → approve
    - "no" → reject
    - "remember: guest allergic to cats" → memory_note

    Args:
        request: PM instruction with context.

    Returns:
        Parsed action with confidence and extracted details.
    """
    context_str = json.dumps(
        request.current_context, indent=2, ensure_ascii=False,
    )

    prompt = (
        f"PM message: {request.pm_message}\n\n"
        f"Property: {request.property_name} ({request.property_id})\n\n"
        f"Current context:\n{context_str}"
    )

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
        parsed = _build_result(data)
        return OpsParsePmInstructionResponse(data=parsed)
    except Exception as exc:
        logger.error("PM instruction parsing failed: %s", exc)
        return OpsParsePmInstructionResponse(
            status=False, error=str(exc),
        )


def _build_result(data: dict[str, Any]) -> OpsParsePmInstructionData:
    """Build structured result from LLM output.

    Args:
        data: Parsed JSON from LLM.

    Returns:
        Validated OpsParsePmInstructionData.
    """
    try:
        action = PmAction(data.get("action", "unknown"))
    except ValueError:
        action = PmAction.UNKNOWN

    confidence = float(data.get("confidence", 0.5))

    result = OpsParsePmInstructionData(
        action=action,
        confidence=confidence,
    )

    if action == PmAction.ASSIGN_CONTACT:
        result.contact_name = data.get("contact_name")
        result.contact_phone = data.get("contact_phone")
        result.contact_role = data.get("contact_role")
        result.vendor_category = data.get("vendor_category")

    elif action == PmAction.APPROVE:
        result.approved = True

    elif action == PmAction.REJECT:
        result.approved = False

    elif action == PmAction.MEMORY_NOTE:
        result.memory_content = data.get("memory_content")

    elif action == PmAction.ASK_FOLLOWUP:
        result.follow_up_question = data.get("follow_up_question")

    if confidence < 0.7:
        result.clarification_message = data.get(
            "clarification_message",
            "Could you clarify what you'd like me to do?",
        )

    return result


_SYSTEM_PROMPT = """Parse a property manager's informal instruction into a structured action.

Action types:
- assign_contact: PM wants to assign a cleaner/vendor (extract name, phone, role)
- approve: PM approves a pending action
- reject: PM rejects a pending action
- memory_note: PM wants to save a note (extract content)
- ask_followup: PM's intent unclear, need clarification
- unknown: Cannot determine intent

Vendor categories (for assign_contact):
hvac, plumbing, electrical, locksmith, appliance_repair, pest_control,
general_maintenance, cleaning

Return JSON:
{
    "action": "assign_contact",
    "confidence": 0.9,
    "contact_name": "Ahmet",
    "contact_phone": null,
    "contact_role": "cleaner",
    "vendor_category": "cleaning",
    "is_permanent": false,
    "priority": "normal",
    "approved": null,
    "memory_content": null,
    "follow_up_question": null,
    "clarification_message": null
}

Rules:
- Extract ONLY explicit info — never invent names or phone numbers
- If confidence < 0.7, include a clarification_message
- For ambiguous short messages like "ok" or "yes", use approve
"""
