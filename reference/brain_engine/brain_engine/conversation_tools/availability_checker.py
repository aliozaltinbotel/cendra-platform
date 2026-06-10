"""Availability checker tool — answers from the GraphQL calendar.

The conversation pipeline pre-fetches a per-day availability window
from ``unified_rateplans`` (via the onboarding-api GraphQL layer) and
attaches it to the agent runtime state.  This tool is a pure projection
over that window — it never calls the PMS API and never falls back to
mockups, so the agent cannot accidentally tell a guest a blocked night
is "müsait" because of a missing PMS adapter.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


_DEFER_MESSAGE = (
    "Cannot verify availability for those dates right now. "
    "Tell the guest you will check the calendar and get back to them."
)


@tool(description=(
    "Check availability for specific dates against the unified rate-plan "
    "calendar (ES `unified_rateplans`). "
    "Use when guest asks about availability or stay extension. "
    "Requires check_in_date and check_out_date in YYYY-MM-DD format. "
    "Returns 'Available' only when EVERY night in [check_in, check_out) "
    "is open with units > 0. Any blocked / unknown / off-window date "
    "yields a 'not available, please defer' answer — never improvise."
))
async def availability_checker(
    check_in_date: str,
    check_out_date: str,
    guests: int = 1,
    runtime: ToolRuntime | None = None,
) -> str:
    """Project the prefetched calendar window to a yes/no answer.

    Args:
        check_in_date: Stay start, ISO ``YYYY-MM-DD`` (inclusive).
        check_out_date: Stay end, ISO ``YYYY-MM-DD`` (exclusive — the
            night ``check_out_date - 1`` is the last one billed).
        guests: Reserved for future occupancy filtering; currently
            unused because the calendar reports unit-level availability.
        runtime: Injected runtime; carries ``availability_calendar``.

    Returns:
        Human-readable string the agent can quote back to the guest.
    """
    del guests  # not yet used — calendar reports unit availability

    if runtime is None:
        return _DEFER_MESSAGE

    nights = _enumerate_nights(check_in_date, check_out_date)
    if not nights:
        return (
            "Invalid date range. Ask the guest to confirm exact "
            "check-in and check-out dates."
        )

    calendar_state = runtime.state.get("availability_calendar") or []
    if not isinstance(calendar_state, list) or not calendar_state:
        return _DEFER_MESSAGE

    by_date: dict[str, dict[str, Any]] = {}
    for entry in calendar_state:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("date") or "")[:10]
        if key:
            by_date[key] = entry

    blocked: list[str] = []
    missing: list[str] = []
    available_rows: list[dict[str, Any]] = []
    for night in nights:
        row = by_date.get(night)
        if row is None:
            missing.append(night)
            continue
        status = str(row.get("status") or "unknown")
        units = row.get("available_units")
        try:
            units_int = int(units) if units is not None else 0
        except (TypeError, ValueError):
            units_int = 0
        if (
            status != "available"
            or units_int <= 0
            or bool(row.get("stop_sell"))
        ):
            blocked.append(night)
            continue
        available_rows.append(row)

    if blocked:
        return (
            "NOT available. Blocked nights in the requested range: "
            f"{', '.join(blocked)}. Refuse the booking / extension and "
            "offer to look for other options."
        )
    if missing:
        return (
            "Cannot confirm — these nights are outside the calendar "
            f"snapshot: {', '.join(missing)}. Tell the guest you will "
            "check and get back to them."
        )

    pricing = _summarise_pricing(available_rows)
    summary = f"Available. All nights {nights[0]}..{nights[-1]} are open."
    if pricing:
        summary = f"{summary} {pricing}"
    return summary


def _summarise_pricing(rows: list[dict[str, Any]]) -> str:
    """Return a one-line price summary or an empty string.

    Sums per-night ``price`` values when every row carries one in a
    consistent currency.  When the channel did not publish prices for
    every night, returns an empty string so the caller can stay silent
    on totals — the prompt block forbids the model from inventing one.
    """
    totals: list[float] = []
    currencies: set[str] = set()
    for row in rows:
        price = row.get("price")
        if price in (None, ""):
            return ""
        try:
            totals.append(float(price))
        except (TypeError, ValueError):
            return ""
        currency = str(row.get("currency") or "").strip()
        if currency:
            currencies.add(currency)
    if not totals:
        return ""
    if len(currencies) > 1:
        return ""
    currency_str = next(iter(currencies)) if currencies else ""
    total = sum(totals)
    avg = total / len(totals)
    formatted_total = f"{total:g}"
    formatted_avg = f"{avg:g}"
    if currency_str:
        return (
            f"Per-night avg: {formatted_avg} {currency_str}. "
            f"Total for {len(totals)} night(s): "
            f"{formatted_total} {currency_str}."
        )
    return (
        f"Per-night avg: {formatted_avg}. "
        f"Total for {len(totals)} night(s): {formatted_total}."
    )


def _enumerate_nights(
    check_in_date: str,
    check_out_date: str,
) -> list[str]:
    """Return every booked night between check-in and check-out.

    Hospitality convention: ``check_out_date`` is exclusive — the guest
    pays for nights ``[check_in_date, check_out_date)``.  The helper
    silently returns an empty list when either input fails to parse so
    the caller can surface a clean "invalid date range" message instead
    of crashing the tool call.
    """
    start = _parse_iso_date(check_in_date)
    end = _parse_iso_date(check_out_date)
    if start is None or end is None or end <= start:
        return []
    nights: list[str] = []
    cursor = start
    while cursor < end:
        nights.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return nights


def _parse_iso_date(value: str) -> date | None:
    """Parse a ``YYYY-MM-DD`` slice, tolerating timestamp suffixes."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None
