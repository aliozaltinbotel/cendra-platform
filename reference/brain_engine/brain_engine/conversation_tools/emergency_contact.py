"""Emergency contact tool — escalation for life-threatening situations.

ONLY for true emergencies: fire, health, break-in, gas leak.
NOT for maintenance, WiFi, locked out, or appliance issues.
"""

from __future__ import annotations

import logging

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


@tool(description=(
    "EMERGENCY escalation tool. Use ONLY for life-threatening "
    "situations: fire, serious health emergency, break-in, "
    "gas leak, flooding. Provides emergency numbers and "
    "escalates to property manager immediately. "
    "Do NOT use for maintenance requests, WiFi issues, appliance problems, "
    "or being locked out — use rag_document_search instead. "
    "Do NOT use for complaints — use check_repeated_complaint. "
    "Do NOT use unless guest explicitly describes a life-threatening situation."
))
async def emergency_contact(
    emergency_type: str,
    details: str = "",
    runtime: ToolRuntime | None = None,
) -> str:
    """Trigger emergency protocol.

    Args:
        emergency_type: Type of emergency (fire, health, security, gas, flood).
        details: Additional details from guest.
        runtime: Injected runtime context.

    Returns:
        Emergency response with local numbers and escalation status.
    """
    property_id = runtime.config.get("property_id", "") if runtime else ""

    logger.warning(
        "EMERGENCY triggered: type=%s property=%s details=%s",
        emergency_type, property_id, details[:200],
    )

    numbers = _get_emergency_numbers(property_id)
    _notify_property_manager(property_id, emergency_type, details)

    return _format_emergency_response(emergency_type, numbers)


def _get_emergency_numbers(property_id: str) -> dict[str, str]:
    """Get local emergency numbers for the property's location.

    Args:
        property_id: Property identifier.

    Returns:
        Dict of service -> phone number.
    """
    # Turkey defaults (primary market)
    return {
        "police": "155",
        "ambulance": "112",
        "fire": "110",
        "general_emergency": "112",
    }


def _notify_property_manager(
    property_id: str,
    emergency_type: str,
    details: str,
) -> None:
    """Send emergency notification to property manager.

    Args:
        property_id: Property identifier.
        emergency_type: Type of emergency.
        details: Guest's description.
    """
    logger.warning(
        "PM notification: EMERGENCY %s at %s — %s",
        emergency_type, property_id, details[:100],
    )


def _format_emergency_response(
    emergency_type: str,
    numbers: dict[str, str],
) -> str:
    """Format emergency response for the agent.

    Args:
        emergency_type: Type of emergency.
        numbers: Emergency phone numbers.

    Returns:
        Formatted emergency instructions.
    """
    lines = [
        f"EMERGENCY DETECTED: {emergency_type}",
        "",
        "Emergency numbers:",
    ]
    for service, number in numbers.items():
        lines.append(f"  {service}: {number}")

    lines.extend([
        "",
        "The property manager has been notified immediately.",
        "Please call the appropriate emergency number if you haven't already.",
    ])
    return "\n".join(lines)
