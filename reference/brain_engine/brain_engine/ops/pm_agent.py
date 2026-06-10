"""PM Agent — autonomous agent for property manager operations.

Handles PM's informal requests by reading context via tools,
reasoning about intent, and building action plans. Supports
copilot (propose) and autopilot (execute safe reads) modes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from brain_engine.ops.models import (
    PmAgentRequest,
    PmAgentResponse,
    PlannedAction,
)

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o"
_TEMPERATURE = 0.1


async def run_pm_agent(
    request: PmAgentRequest,
) -> PmAgentResponse:
    """Process a PM's request through the operations agent.

    The agent reads context, reasons about PM's intent,
    and builds an action plan. In copilot mode, it proposes
    actions. In autopilot mode, it executes safe reads.

    Args:
        request: PM agent request with context.

    Returns:
        Agent response with action plan and message.
    """
    prompt = _build_prompt(request)

    try:
        response = await litellm.acompletion(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _build_system(request)},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content or "{}")
        actions = _parse_actions(data.get("action_plan", []))

        return PmAgentResponse(
            action_plan=actions,
            message_to_pm=data.get("message_to_pm", ""),
            needs_clarification=data.get("needs_clarification", False),
            reads_performed=data.get("reads_performed", []),
        )
    except Exception as exc:
        logger.error("PM agent failed: %s", exc)
        return PmAgentResponse(status=False, error=str(exc))


def _build_prompt(request: PmAgentRequest) -> str:
    """Build the agent prompt from PM's message and context.

    Args:
        request: PM agent request.

    Returns:
        Formatted prompt.
    """
    ctx = request.ops_context
    contacts = json.dumps(
        ctx.contacts_tried, indent=2, ensure_ascii=False,
    ) if ctx.contacts_tried else "None"

    return (
        f"PM says: {request.pm_message}\n\n"
        f"Property: {ctx.property_name} ({ctx.property_id})\n"
        f"Trigger: {ctx.trigger_type}\n"
        f"Escalation reason: {ctx.escalation_reason}\n"
        f"Task: {ctx.task_description}\n"
        f"Reservation: {ctx.reservation_id}\n"
        f"Contacts tried:\n{contacts}"
    )


def _build_system(request: PmAgentRequest) -> str:
    """Build system prompt with autonomy mode.

    Args:
        request: PM agent request.

    Returns:
        System prompt string.
    """
    mode = request.autonomy_mode
    mode_instruction = (
        "COPILOT: Propose actions, do NOT execute writes. "
        "Present your plan for PM approval."
        if mode == "copilot"
        else "AUTOPILOT: Execute safe reads autonomously. "
        "Propose writes for approval."
    )

    return f"{_BASE_SYSTEM}\n\nMode: {mode_instruction}"


def _parse_actions(raw: list[dict]) -> list[PlannedAction]:
    """Parse raw action plan into PlannedAction list.

    Args:
        raw: Raw action dicts from LLM.

    Returns:
        List of PlannedAction objects.
    """
    return [
        PlannedAction(
            tool=item.get("tool", ""),
            args=item.get("args", {}),
            tier=item.get("tier", "medium"),
            description=item.get("description", ""),
        )
        for item in raw[:10]
    ]


_BASE_SYSTEM = """You are an operations agent for property management.

Your process:
1. READ: Look up information before asking the PM
2. REASON: Figure out PM's intent from context
3. PLAN: Build a sequence of actions

Available tools (for action_plan):
- lookup_contacts: Get contacts by role for a property
- lookup_reservation: Get reservation details
- lookup_properties: Search properties by name
- create_task: Create a maintenance/follow-up task
- assign_contact: Assign a vendor/cleaner to a property
- send_message: Send WhatsApp/email to contact

Return JSON:
{
    "action_plan": [
        {"tool": "lookup_contacts", "args": {"role": "cleaner"}, "tier": "medium", "description": "Find available cleaners"}
    ],
    "message_to_pm": "Here's what I propose...",
    "needs_clarification": false,
    "reads_performed": ["Looked up property contacts"]
}

If PM's intent is unclear, set needs_clarification=true and ask in message_to_pm.
"""
