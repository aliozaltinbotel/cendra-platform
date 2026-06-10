"""Карта соответствия Intent → группы инструментов.

Статическое отображение определяет, какие инструменты релевантны
для каждого типа намерения гостя. Используется Dynamic Tool Filtering
для сокращения набора инструментов с 9 до 3-4 на запрос.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from brain_engine.intent_controller.intents import Intent


class ToolDomainGroup(StrEnum):
    """Доменные группы инструментов."""

    BOOKING = "booking_tools"
    GUEST = "guest_tools"
    PROPERTY = "property_tools"


# ── Группировка инструментов по доменам ──────────────────────── #

TOOL_DOMAIN_GROUPS: Final[dict[ToolDomainGroup, tuple[str, ...]]] = {
    ToolDomainGroup.BOOKING: (
        "availability_checker",
        "upsell_calculator",
        "reservation_info_retriever",
    ),
    ToolDomainGroup.GUEST: (
        "rag_document_search",
        "check_repeated_complaint",
        "thanks_response_generator",
    ),
    ToolDomainGroup.PROPERTY: (
        "location_search",
        "emergency_contact",
        "alternative_property_finder",
    ),
}

# Инструменты, доступные всегда (базовый RAG для любого запроса)
ALWAYS_ON_TOOLS: Final[frozenset[str]] = frozenset({
    "rag_document_search",
})


# ── Intent → группы инструментов ─────────────────────────────── #

INTENT_TOOL_MAP: Final[dict[Intent, tuple[ToolDomainGroup, ...]]] = {
    Intent.UNKNOWN: (
        ToolDomainGroup.BOOKING,
        ToolDomainGroup.GUEST,
        ToolDomainGroup.PROPERTY,
    ),
    Intent.GREETING: (
        ToolDomainGroup.GUEST,
    ),
    Intent.FAREWELL: (
        ToolDomainGroup.GUEST,
    ),
    Intent.COMPLAINT: (
        ToolDomainGroup.GUEST,
        ToolDomainGroup.PROPERTY,
    ),
    Intent.REQUEST: (
        ToolDomainGroup.BOOKING,
        ToolDomainGroup.GUEST,
    ),
    Intent.INFO: (
        ToolDomainGroup.GUEST,
        ToolDomainGroup.PROPERTY,
    ),
    Intent.ACTION: (
        ToolDomainGroup.BOOKING,
        ToolDomainGroup.PROPERTY,
    ),
    Intent.CONFIRMATION: (
        ToolDomainGroup.BOOKING,
        ToolDomainGroup.GUEST,
    ),
    Intent.CANCELLATION: (
        ToolDomainGroup.BOOKING,
    ),
    Intent.CLARIFICATION: (
        ToolDomainGroup.BOOKING,
        ToolDomainGroup.GUEST,
    ),
    Intent.FEEDBACK: (
        ToolDomainGroup.GUEST,
    ),
}


def get_tools_for_intent(intent: Intent) -> frozenset[str]:
    """Вернуть имена инструментов, релевантных для данного intent.

    Объединяет инструменты из соответствующих доменных групп
    с набором always-on инструментов.

    Args:
        intent: Классифицированное намерение гостя.

    Returns:
        Множество имён инструментов.
    """
    groups = INTENT_TOOL_MAP.get(intent, INTENT_TOOL_MAP[Intent.UNKNOWN])
    tools: set[str] = set(ALWAYS_ON_TOOLS)
    for group in groups:
        tools.update(TOOL_DOMAIN_GROUPS[group])
    return frozenset(tools)
