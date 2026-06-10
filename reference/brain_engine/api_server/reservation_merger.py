"""Merge UI-side and GraphQL-side reservation snapshots.

Closes Sandbox UI tests C3 (empty calendar), C4 (wrong check_in
timestamp) and C7 (missing total_price) discovered on 2026-05-19.
Each of those failures had the same shape: the UI displays a value
that comes from the ``unified_reservations`` / ``unified_rateplans``
ES indexes (visible in Kibana), but the AG-UI payload it ships to
brain either omits the field, ships it under a different key, or
ships a corrupted version of it.  Brain trusted the UI snapshot
because the GraphQL fallback only fired when the UI sent **nothing**
at all.

The fix promotes the GraphQL response to a co-equal source: brain
always fetches the GraphQL snapshot when a property is in scope,
then merges it field-by-field with whatever the UI sent.

The merge rule, intentionally simple, is:

  ``UI field is non-empty ⇒ UI wins (sandbox override).``
  ``Otherwise GraphQL fills.``

That keeps Sandbox testing on (operators can flip Reservation
Status, Booking Channel, Payment Status etc. and the dropdowns
still affect the next turn) while making every field that the UI
forgets / corrupts default to the authoritative ES value.

This module is intentionally side-effect free — no I/O, no global
state, no logging.  Pure functions over Pydantic dataclasses so
the merger is trivial to unit-test without standing up the AG-UI
handler.
"""

from __future__ import annotations

from typing import Final

from brain_engine.conversation.models import (
    CalendarDay,
    ReservationContext,
)

__all__ = [
    "merge_calendars",
    "merge_reservation_contexts",
]


# Fields treated as "unset" — UI value falling into this set lets
# the GraphQL value win.  ``0`` is included because ``num_guests``
# / ``num_children`` use it as the integer default; a real value
# of ``0`` is operationally indistinguishable from "missing".
_EMPTY_SENTINELS: Final[frozenset[object]] = frozenset(
    {None, "", 0},
)


def _is_empty(value: object) -> bool:
    """Whether ``value`` should be treated as "UI did not supply"."""
    return value in _EMPTY_SENTINELS


def merge_reservation_contexts(
    ui: ReservationContext | None,
    graphql: ReservationContext | None,
) -> ReservationContext | None:
    """Merge UI and GraphQL snapshots into a single context.

    Returns:
        ``None`` when both inputs are ``None`` — the caller then
        renders the no-data block.  Otherwise a fresh
        :class:`ReservationContext` whose fields follow the
        "UI overrides only when non-empty" rule.
    """
    if ui is None:
        return graphql
    if graphql is None:
        return ui

    merged_data: dict[str, object] = {}
    for field_name in ReservationContext.model_fields:
        ui_value = getattr(ui, field_name)
        gql_value = getattr(graphql, field_name)
        merged_data[field_name] = (
            ui_value if not _is_empty(ui_value) else gql_value
        )
    return ReservationContext(**merged_data)


def merge_calendars(
    ui: list[CalendarDay] | None,
    graphql: list[CalendarDay] | None,
) -> list[CalendarDay]:
    """Merge UI and GraphQL availability calendars.

    Calendar windows are flat lists keyed by ``date``.  The merge
    rule mirrors the reservation context: a UI-supplied day with
    real values (e.g. ``status`` other than the model default
    ``"unknown"``, or a non-empty ``price``) acts as an override;
    otherwise the GraphQL day wins.  GraphQL days for dates the UI
    omitted are added as-is.

    Returns:
        A new list of :class:`CalendarDay` in date order.  Empty
        list when both inputs are empty / ``None`` — the caller
        then falls back to the no-data calendar block.
    """
    ui_days = ui or []
    gql_days = graphql or []

    by_date: dict[str, CalendarDay] = {}
    for day in gql_days:
        if day.date:
            by_date[day.date] = day

    for day in ui_days:
        if not day.date:
            continue
        existing = by_date.get(day.date)
        if existing is None:
            by_date[day.date] = day
            continue
        # Per-field merge for the same date.  UI non-defaults win.
        by_date[day.date] = CalendarDay(
            date=day.date,
            status=(
                day.status
                if day.status and day.status != "unknown"
                else existing.status
            ),
            available_units=(
                day.available_units
                if day.available_units
                else existing.available_units
            ),
            stop_sell=(
                day.stop_sell or existing.stop_sell
            ),
            price=day.price or existing.price,
            currency=day.currency or existing.currency,
            note=day.note or existing.note,
        )

    return sorted(by_date.values(), key=lambda d: d.date)
