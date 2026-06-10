"""Reservation info retriever tool — projects the GraphQL snapshot.

The conversation pipeline resolves the active reservation through the
onboarding-api GraphQL layer and attaches the snapshot to the agent
runtime state.  This tool is a pure projection over that snapshot — it
never reads from the PMS API and never falls back to mockups, so the
agent cannot serve a fabricated date when the GraphQL lookup did not
return a row.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


_DEFER_MESSAGE = (
    "No reservation snapshot is attached. Tell the guest you will "
    "check their booking details and get back to them."
)


@tool(description=(
    "Retrieve reservation/booking details (dates, guest count, "
    "channel, total) from the GraphQL-sourced snapshot. "
    "Use when guest asks about their existing booking. "
    "Do NOT use for new availability or pricing — use availability_checker. "
    "Do NOT use for property rules — use rag_document_search."
))
async def reservation_info_retriever(
    runtime: ToolRuntime | None = None,
) -> str:
    """Project the prefetched reservation snapshot to a flat block.

    Args:
        runtime: Injected runtime; carries ``reservation_context``.

    Returns:
        Multiline string the agent can quote back to the guest, or a
        deferral instruction when no snapshot is available.
    """
    if runtime is None:
        return _DEFER_MESSAGE

    snapshot = runtime.state.get("reservation_context")
    if not isinstance(snapshot, dict):
        return _DEFER_MESSAGE

    field_labels: list[tuple[str, str]] = [
        ("status", "Status"),
        ("check_in", "Check-in"),
        ("check_in_time", "Check-in time"),
        ("check_out", "Check-out"),
        ("check_out_time", "Check-out time"),
        ("guest_name", "Guest"),
        ("num_guests", "Adults"),
        ("num_children", "Children"),
        ("property_name", "Property"),
        ("booking_channel", "Channel"),
        ("total_price", "Total"),
        ("currency", "Currency"),
    ]

    lines: list[str] = []
    for key, label in field_labels:
        if not _has_value(snapshot.get(key)):
            continue
        lines.append(f"{label}: {snapshot[key]}")

    if not lines:
        return _DEFER_MESSAGE
    return "\n".join(lines)


def _has_value(value: Any) -> bool:
    """Return ``True`` when ``value`` is a meaningful non-default."""
    if value in (None, "", 0):
        return False
    return True
