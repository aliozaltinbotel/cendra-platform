"""Customer Guardrails — per-tenant rule injection into prompts.

Selects applicable guardrails based on business flags and
customer settings, then formats them for system prompt injection.
Supports ALWAYS, CONTEXTUAL, and CONDITIONAL priorities.
"""

from __future__ import annotations

import logging
from typing import Protocol

from brain_engine.customer.models import (
    CustomerGuardrail,
    CustomerSettings,
    GuardrailPriority,
)

logger = logging.getLogger(__name__)


class GuardrailProvider(Protocol):
    """Protocol for loading customer guardrails."""

    async def get_settings(self, customer_id: str) -> CustomerSettings:
        """Load customer settings including guardrails."""
        ...


# ── System default guardrails ────────────────────────────────── #

_SYSTEM_GUARDRAILS: list[CustomerGuardrail] = [
    CustomerGuardrail(
        title="no_fabrication",
        guardrail=(
            "NEVER invent or assume information. "
            "If you don't have the answer from tools, knowledge base, "
            "or provided context, say you will check and get back."
        ),
        priority=GuardrailPriority.ALWAYS,
        is_default=True,
    ),
    CustomerGuardrail(
        title="no_availability_confirmation",
        guardrail=(
            "NEVER confirm availability or pricing without checking "
            "the availability tool first. Do not guess dates or prices."
        ),
        priority=GuardrailPriority.CONTEXTUAL,
        flags=["IS_AVAILABILITY_RELATED", "IS_PRICE_RELATED"],
        is_default=True,
    ),
    CustomerGuardrail(
        title="emergency_protocol",
        guardrail=(
            "For emergencies (fire, health, security): immediately provide "
            "local emergency numbers, then escalate to property manager. "
            "Do NOT try to solve emergencies yourself."
        ),
        priority=GuardrailPriority.CONTEXTUAL,
        flags=["IS_EMERGENCY"],
        is_default=True,
    ),
    CustomerGuardrail(
        title="complaint_empathy",
        guardrail=(
            "For complaints: acknowledge the issue with empathy first, "
            "then offer a concrete next step. Never dismiss or argue."
        ),
        priority=GuardrailPriority.CONTEXTUAL,
        flags=["IS_COMPLAINT", "IS_CLEANING_ISSUE", "IS_NOISE_COMPLAINT"],
        is_default=True,
    ),
    CustomerGuardrail(
        title="no_bold_formatting",
        guardrail="Avoid bold formatting (**text**) in responses.",
        priority=GuardrailPriority.ALWAYS,
        is_default=True,
    ),
    CustomerGuardrail(
        title="no_timestamps",
        guardrail=(
            "NEVER include timestamps or date markers like "
            '"[14 March 2025, 11:19]" in responses.'
        ),
        priority=GuardrailPriority.ALWAYS,
        is_default=True,
    ),
    CustomerGuardrail(
        title="first_person_perspective",
        guardrail=(
            "Speak as the property manager (first person). "
            'Never say "the host" or "the property manager".'
        ),
        priority=GuardrailPriority.ALWAYS,
        is_default=True,
    ),
    CustomerGuardrail(
        title="match_guest_language",
        guardrail=(
            "ALWAYS respond in the same language as the guest's message. "
            "If guest writes in Turkish, respond in Turkish. "
            "If guest writes in German, respond in German."
        ),
        priority=GuardrailPriority.ALWAYS,
        is_default=True,
    ),
]


def select_guardrails(
    settings: CustomerSettings,
    active_flags: list[str],
) -> list[CustomerGuardrail]:
    """Select applicable guardrails for this request.

    Combines system defaults + customer-defined guardrails,
    filtered by active business flags.

    Args:
        settings: Customer's AI settings.
        active_flags: Currently active business flag names.

    Returns:
        Deduplicated list of applicable guardrails.
    """
    seen: set[str] = set()
    result: list[CustomerGuardrail] = []

    all_guardrails = _SYSTEM_GUARDRAILS + settings.guardrails

    for guard in all_guardrails:
        if guard.title in seen:
            continue

        if _should_apply(guard, active_flags):
            seen.add(guard.title)
            result.append(guard)

    logger.debug(
        "Selected %d guardrails for customer=%s flags=%s",
        len(result), settings.customer_id, active_flags,
    )
    return result


def _should_apply(
    guard: CustomerGuardrail,
    active_flags: list[str],
) -> bool:
    """Check if a guardrail should be applied.

    Args:
        guard: The guardrail to check.
        active_flags: Active business flags.

    Returns:
        True if guardrail should be included.
    """
    if guard.priority == GuardrailPriority.ALWAYS:
        return True

    if guard.priority == GuardrailPriority.CONTEXTUAL:
        return bool(set(guard.flags) & set(active_flags))

    return False


def format_guardrails_for_prompt(
    guardrails: list[CustomerGuardrail],
) -> str:
    """Format selected guardrails into system prompt text.

    Args:
        guardrails: List of applicable guardrails.

    Returns:
        Formatted string for injection into system prompt.
    """
    if not guardrails:
        return ""

    lines = ["## Rules and Guardrails", ""]
    for i, guard in enumerate(guardrails, 1):
        lines.append(f"{i}. {guard.guardrail}")

    return "\n".join(lines)
