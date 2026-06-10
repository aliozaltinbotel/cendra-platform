"""Tests for the GraphQL-primary reservation merger (R10).

Closes Sandbox UI tests C3 (empty calendar), C4 (wrong check_in
timestamp) and C7 (missing total_price) discovered on 2026-05-19.

The fix promotes the GraphQL ``unified_reservations`` /
``unified_rateplans`` response to a co-equal source: brain
always fetches the GraphQL snapshot when a property is in scope,
then merges it field-by-field with whatever the UI sent.  The
merge rule pinned in this module:

* UI field non-empty ⇒ UI wins (sandbox dropdown override).
* UI field empty / missing ⇒ GraphQL fills.

These tests pin the per-field behaviour without standing up the
AG-UI handler — the merger is pure-function over Pydantic models.
"""

from __future__ import annotations

from api_server.reservation_merger import (
    merge_calendars,
    merge_reservation_contexts,
)
from brain_engine.conversation.models import (
    CalendarDay,
    ReservationContext,
)

# ── reservation merge ──────────────────────────────────────────


def test_merge_both_none_returns_none() -> None:
    """When neither source has a snapshot the merge collapses to
    ``None`` so the AG-UI handler renders the no-data block."""
    assert merge_reservation_contexts(None, None) is None


def test_merge_ui_none_returns_graphql() -> None:
    """No UI snapshot ⇒ the GraphQL value passes through verbatim.
    This is the production path where the client ships nothing
    in ``state`` and brain relies on ES for the snapshot."""
    gql = ReservationContext(status="Confirmed", check_in="2026-05-18")
    merged = merge_reservation_contexts(None, gql)
    assert merged == gql


def test_merge_graphql_none_returns_ui() -> None:
    """No GraphQL snapshot ⇒ UI passes through.  This is the
    fallback when the unified-data client is offline / errored."""
    ui = ReservationContext(status="Inquiry")
    merged = merge_reservation_contexts(ui, None)
    assert merged == ui


def test_ui_override_for_status_wins_over_graphql() -> None:
    """Sandbox testing scenario: operator flips Reservation Status
    dropdown from Confirmed to Inquiry.  UI ships ``status="Inquiry"``
    so the merger must keep that override — otherwise the dropdown
    has no effect on brain's behaviour."""
    ui = ReservationContext(status="Inquiry")
    gql = ReservationContext(
        status="Confirmed",
        check_in="2026-05-18",
        check_in_time="14:00",
        total_price="2000",
        currency="EUR",
    )
    merged = merge_reservation_contexts(ui, gql)

    assert merged is not None
    assert merged.status == "Inquiry"  # UI override
    assert merged.check_in == "2026-05-18"  # GraphQL fills
    assert merged.check_in_time == "14:00"  # GraphQL fills
    assert merged.total_price == "2000"  # GraphQL fills
    assert merged.currency == "EUR"  # GraphQL fills


def test_empty_ui_fields_fall_back_to_graphql() -> None:
    """The C3 / C4 / C7 regression guard.  UI ships partial state
    (only ``status``, no dates, no price, no calendar); the
    merger must NOT overwrite GraphQL's authoritative dates and
    price with the UI's empty defaults."""
    ui = ReservationContext(status="Confirmed")
    gql = ReservationContext(
        status="Confirmed",
        check_in="2026-05-18",
        check_in_time="14:00",
        check_out="2026-05-20",
        check_out_time="10:00",
        total_price="2000",
        currency="EUR",
        num_guests=2,
        num_children=1,
        guest_name="Alice",
        property_name="Vibe IZY",
        booking_channel="airbnb",
    )
    merged = merge_reservation_contexts(ui, gql)

    assert merged is not None
    assert merged.check_in == "2026-05-18"
    assert merged.check_in_time == "14:00"
    assert merged.total_price == "2000"
    assert merged.num_guests == 2
    assert merged.guest_name == "Alice"


def test_zero_numeric_falls_back_to_graphql() -> None:
    """``num_guests=0`` is the Pydantic default, indistinguishable
    from "missing".  Merge treats it as empty so the GraphQL
    integer fills.  Otherwise a UI that forgets the field would
    silently zero out the guest count."""
    ui = ReservationContext(status="Confirmed")  # num_guests=0 default
    gql = ReservationContext(num_guests=4)
    merged = merge_reservation_contexts(ui, gql)
    assert merged is not None
    assert merged.num_guests == 4


# ── calendar merge ──────────────────────────────────────────────


def test_merge_calendars_both_empty_returns_empty() -> None:
    """Both inputs empty ⇒ empty list.  Caller falls back to the
    no-data calendar block."""
    assert merge_calendars([], []) == []
    assert merge_calendars(None, None) == []


def test_merge_calendars_ui_empty_uses_graphql() -> None:
    """The C3 regression: UI ships an empty calendar despite the
    panel showing colourful bars.  Merge must pick up GraphQL's
    days verbatim — otherwise availability questions defer
    forever even when ES has data."""
    gql_days = [
        CalendarDay(date="2026-05-24", status="available"),
        CalendarDay(date="2026-05-25", status="available"),
        CalendarDay(date="2026-05-28", status="blocked", stop_sell=True),
    ]
    merged = merge_calendars(ui=[], graphql=gql_days)
    assert [d.date for d in merged] == [
        "2026-05-24", "2026-05-25", "2026-05-28",
    ]
    assert merged[2].status == "blocked"
    assert merged[2].stop_sell is True


def test_merge_calendars_ui_override_for_specific_date() -> None:
    """Sandbox testing case: operator marks May 28 as blocked
    even though GraphQL says available.  UI override must win
    for that date, GraphQL fills the others."""
    ui_days = [
        CalendarDay(date="2026-05-28", status="blocked", stop_sell=True),
    ]
    gql_days = [
        CalendarDay(date="2026-05-24", status="available", available_units=1),
        CalendarDay(date="2026-05-28", status="available", available_units=2),
    ]
    merged = merge_calendars(ui=ui_days, graphql=gql_days)

    by_date = {d.date: d for d in merged}
    assert by_date["2026-05-24"].status == "available"  # GraphQL
    assert by_date["2026-05-24"].available_units == 1  # GraphQL
    assert by_date["2026-05-28"].status == "blocked"  # UI override
    assert by_date["2026-05-28"].stop_sell is True  # UI override


def test_merge_calendars_ui_only_adds_new_dates() -> None:
    """A date present in UI but not in GraphQL must still surface
    — operator-added sandbox days are honoured."""
    ui_days = [CalendarDay(date="2026-12-31", status="blocked")]
    gql_days = [CalendarDay(date="2026-05-24", status="available")]
    merged = merge_calendars(ui=ui_days, graphql=gql_days)

    by_date = {d.date: d for d in merged}
    assert set(by_date.keys()) == {"2026-05-24", "2026-12-31"}


def test_merge_calendars_skips_blank_dates() -> None:
    """A day record without a ``date`` is degenerate (cannot be
    keyed) — the merger drops it from both sources rather than
    leaking the malformed row."""
    ui_days = [CalendarDay(date="", status="available")]
    gql_days = [CalendarDay(date="", status="available")]
    merged = merge_calendars(ui=ui_days, graphql=gql_days)
    assert merged == []


def test_merge_calendars_returns_sorted_by_date() -> None:
    """Downstream renderers iterate the calendar in order — sort
    is part of the contract so the LLM scans deterministically."""
    ui_days = [
        CalendarDay(date="2026-05-25", status="available"),
        CalendarDay(date="2026-05-24", status="available"),
    ]
    gql_days = [
        CalendarDay(date="2026-05-26", status="available"),
    ]
    merged = merge_calendars(ui=ui_days, graphql=gql_days)
    assert [d.date for d in merged] == [
        "2026-05-24", "2026-05-25", "2026-05-26",
    ]


def test_merge_calendars_preserves_ui_price() -> None:
    """A UI day that carries an explicit ``price`` overrides the
    GraphQL day's price.  This is the per-night sandbox override
    case — testing what an operator-tuned rate does for the LLM."""
    ui_days = [
        CalendarDay(
            date="2026-05-24",
            status="available",
            price="200.00",
            currency="EUR",
        ),
    ]
    gql_days = [
        CalendarDay(
            date="2026-05-24",
            status="available",
            price="120.00",
            currency="EUR",
        ),
    ]
    merged = merge_calendars(ui=ui_days, graphql=gql_days)
    assert merged[0].price == "200.00"
