"""Repeated complaint checker tool.

Detects if a guest has raised the same complaint before
and previous responses were only deferrals, not resolutions.
"""

from __future__ import annotations

import logging

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)

_DEFERRAL_PHRASES = [
    "get back to you",
    "will check",
    "looking into",
    "follow up",
    "we'll check",
    "let me check",
    "i'll find out",
]


@tool(description=(
    "Check if this complaint has been raised before in the "
    "conversation. Detects repeated complaints where previous "
    "responses were only deferrals, not actual resolutions. "
    "Use ONLY when guest message expresses dissatisfaction or a problem. "
    "Do NOT use for general questions, requests, or positive messages. "
    "Do NOT use for emergencies — use emergency_contact. "
    "Do NOT use for thank-you messages."
))
async def check_repeated_complaint(
    complaint_topic: str,
    runtime: ToolRuntime | None = None,
) -> str:
    """Check for repeated complaint patterns.

    Args:
        complaint_topic: Topic of the current complaint.
        runtime: Injected runtime with conversation history.

    Returns:
        Analysis of whether this is a repeated complaint.
    """
    history = _get_conversation_history(runtime)
    if not history:
        return "First mention of this topic — no prior complaints found."

    repeated, prior_deferrals = _analyze_history(complaint_topic, history)

    if repeated and prior_deferrals:
        return (
            f"REPEATED COMPLAINT: Guest raised '{complaint_topic}' before "
            f"and received only deferrals ({prior_deferrals} times). "
            "This needs a concrete resolution, not another deferral."
        )

    if repeated:
        return f"Guest mentioned '{complaint_topic}' before but received a response."

    return "First mention of this topic."


def _get_conversation_history(
    runtime: ToolRuntime | None,
) -> list[dict[str, str]]:
    """Extract conversation history from runtime.

    Args:
        runtime: Tool runtime context.

    Returns:
        List of message dicts with 'role' and 'content'.
    """
    if not runtime:
        return []
    return runtime.state.get("messages", [])


def _analyze_history(
    topic: str,
    history: list[dict[str, str]],
) -> tuple[bool, int]:
    """Analyze conversation history for repeated complaints.

    Args:
        topic: Complaint topic to search for.
        history: Conversation messages.

    Returns:
        Tuple of (is_repeated, deferral_count).
    """
    topic_lower = topic.lower()
    mentioned_before = False
    deferral_count = 0

    for msg in history[:-1]:  # exclude current message
        content = msg.get("content", "").lower()
        role = msg.get("role", "")

        if role == "user" and topic_lower in content:
            mentioned_before = True

        if role == "assistant" and mentioned_before:
            if any(phrase in content for phrase in _DEFERRAL_PHRASES):
                deferral_count += 1

    return mentioned_before, deferral_count
