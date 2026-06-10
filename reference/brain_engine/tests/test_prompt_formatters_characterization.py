"""Characterization tests for the prompt-formatter helpers (R8 step 2).

Per the 6-step refactor workflow in ``python_master_guide_2026_may.md``
section "Рефакторинг и code review", any behaviour-preserving move
must FIRST be pinned by tests that capture the current output of
the unit under refactor.  These tests must:

1. Pass against the **unchanged** source (proves the spec is correct).
2. Still pass after the extraction (proves no drift).

Scope: only ``_format_availability_calendar`` and
``_format_reservation_context`` (plus the two no-data block
constants they fall back to).  Other helpers in ``service.py`` are
out of scope for R8.

The tests assert on **complete substring presence** rather than
exact-string equality so that whitespace tweaks the formatter might
make in the future (a single trailing newline, for example) do not
mask real behavioural regressions.  Each assertion targets one
contract: a strict rule, a quoted field, a guard against guessing.
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.models import (
    CalendarDay,
    ReservationContext,
)
from brain_engine.conversation.service import (
    _CALENDAR_NO_DATA_BLOCK,
    _RESERVATION_NO_DATA_BLOCK,
    _format_availability_calendar,
    _format_reservation_context,
)

# ── _format_availability_calendar ──────────────────────────────────────


class TestFormatAvailabilityCalendar:
    """The calendar formatter renders per-day rows and pins strict
    no-guessing rules.  When the snapshot is empty it falls back to
    :data:`_CALENDAR_NO_DATA_BLOCK`.
    """

    def test_none_returns_no_data_block(self) -> None:
        """``None`` snapshot is treated as "no data attached"."""
        assert _format_availability_calendar(None) == _CALENDAR_NO_DATA_BLOCK

    def test_empty_list_returns_no_data_block(self) -> None:
        """An empty iterable must also fall back — same UX shape."""
        assert _format_availability_calendar([]) == _CALENDAR_NO_DATA_BLOCK

    def test_no_data_block_contains_strict_rules(self) -> None:
        """The fallback block must carry the no-guessing directive
        so the LLM defers on availability questions when no data is
        attached.  This is the 2026-04-28 demo-bug regression guard.
        """
        block = _CALENDAR_NO_DATA_BLOCK
        assert "[CALENDAR AVAILABILITY]" in block
        assert "STRICT RULES" in block
        assert "Do NOT claim a date is available" in block
        assert "müsait" in block  # TR deferral pattern
        assert "döneceğim" in block  # TR deferral pattern

    def test_rows_only_emit_when_date_present(self) -> None:
        """A day with an empty ``date`` is silently skipped; if every
        day is empty the fallback block fires."""
        day = CalendarDay(date="", status="available", available_units=3)
        assert _format_availability_calendar([day]) == _CALENDAR_NO_DATA_BLOCK

    def test_single_available_day_renders_full_row(self) -> None:
        """A populated day must surface status, units, and price."""
        day = CalendarDay(
            date="2026-06-01",
            status="available",
            available_units=4,
            stop_sell=False,
            price="120.00",
            currency="EUR",
        )
        rendered = _format_availability_calendar([day])

        assert "[CALENDAR AVAILABILITY]" in rendered
        assert "- 2026-06-01: status=available, units=4" in rendered
        assert "price=120.00 EUR" in rendered
        # The strict-rule footer must always follow the rows.
        assert "STRICT RULES:" in rendered
        assert "Never guess" in rendered

    def test_stop_sell_day_includes_stopsell_flag(self) -> None:
        """A stopSell day must surface the flag so the LLM blocks
        the booking."""
        day = CalendarDay(
            date="2026-06-02",
            status="blocked",
            stop_sell=True,
        )
        rendered = _format_availability_calendar([day])

        assert "stopSell=true" in rendered
        assert "status=blocked" in rendered

    def test_price_without_currency_omits_currency_token(self) -> None:
        """``price=120.00`` without a currency must render WITHOUT a
        trailing space (``f"price={price} {currency}".strip()`` path).
        """
        day = CalendarDay(
            date="2026-06-03",
            status="available",
            price="120.00",
            currency="",
        )
        rendered = _format_availability_calendar([day])

        # ``price=120.00`` with no currency suffix.
        assert "price=120.00" in rendered
        # No stray double-space before the next flag.
        assert "price=120.00  " not in rendered

    def test_day_without_price_still_renders(self) -> None:
        """A day with no price must still appear — but no ``price=``
        token surfaces."""
        day = CalendarDay(date="2026-06-04", status="unknown")
        rendered = _format_availability_calendar([day])

        assert "- 2026-06-04: status=unknown, units=0" in rendered
        assert "price=" not in rendered

    def test_day_with_note_surfaces_note(self) -> None:
        """A channel-side note (handover, cleaning) must be quoted
        verbatim so the LLM can mention it."""
        day = CalendarDay(date="2026-06-05", status="blocked", note="handover")
        rendered = _format_availability_calendar([day])
        assert "note=handover" in rendered

    def test_multiple_days_preserve_order(self) -> None:
        """Rows must appear in the order they were supplied — the LLM
        scans them top-down."""
        days = [
            CalendarDay(date="2026-06-01", status="available"),
            CalendarDay(date="2026-06-02", status="blocked"),
            CalendarDay(date="2026-06-03", status="unknown"),
        ]
        rendered = _format_availability_calendar(days)

        idx_a = rendered.index("2026-06-01")
        idx_b = rendered.index("2026-06-02")
        idx_c = rendered.index("2026-06-03")
        assert idx_a < idx_b < idx_c

    def test_block_starts_with_header(self) -> None:
        """The non-empty block always begins with the
        ``[CALENDAR AVAILABILITY]`` header so downstream parsers
        anchor on a stable string."""
        day = CalendarDay(date="2026-06-01", status="available")
        rendered = _format_availability_calendar([day])
        assert rendered.startswith("[CALENDAR AVAILABILITY]\n")


# ── _format_reservation_context ────────────────────────────────────────


class TestFormatReservationContext:
    """The reservation formatter quotes every populated field on the
    snapshot and pins anti-fabrication rules.  ``None`` and empty
    snapshots fall back to :data:`_RESERVATION_NO_DATA_BLOCK`.
    """

    def test_none_returns_no_data_block(self) -> None:
        """``None`` snapshot ⇒ fallback block."""
        assert _format_reservation_context(None) == _RESERVATION_NO_DATA_BLOCK

    def test_empty_context_returns_no_data_block(self) -> None:
        """An empty ``ReservationContext`` has no populated fields
        and must trigger the fallback (the ``_add`` helper skips
        empty values, so ``fields`` is empty)."""
        ctx = ReservationContext()
        assert _format_reservation_context(ctx) == _RESERVATION_NO_DATA_BLOCK

    def test_no_data_block_contains_strict_rules(self) -> None:
        """The fallback must carry the deferral directive."""
        block = _RESERVATION_NO_DATA_BLOCK
        assert "[RESERVATION FACTS]" in block
        assert "Never invent or guess" in block
        assert "döneceğim" in block

    def test_status_only_renders_status_row(self) -> None:
        """A snapshot with only ``status`` set surfaces that field
        — and nothing else (no empty rows)."""
        ctx = ReservationContext(status="Inquiry")
        rendered = _format_reservation_context(ctx)

        assert "- Status: Inquiry" in rendered
        assert "Check-in date" not in rendered
        assert "Adults" not in rendered

    def test_full_context_renders_every_populated_field(self) -> None:
        """A populated snapshot quotes every non-empty field."""
        ctx = ReservationContext(
            status="Confirmed",
            check_in="2026-06-01",
            check_in_time="15:00",
            check_out="2026-06-04",
            check_out_time="11:00",
            guest_name="Alice Smith",
            num_guests=2,
            num_children=1,
            property_name="Vibe IZY",
            booking_channel="airbnb",
            total_price="540.00",
            currency="EUR",
            current_time="2026-05-19T10:30:00Z",
        )
        rendered = _format_reservation_context(ctx)

        assert "- Status: Confirmed" in rendered
        assert "- Check-in date: 2026-06-01" in rendered
        assert "- Check-in time: 15:00" in rendered
        assert "- Check-out date: 2026-06-04" in rendered
        assert "- Check-out time: 11:00" in rendered
        assert "- Guest name: Alice Smith" in rendered
        assert "- Adults: 2" in rendered
        assert "- Children: 1" in rendered
        assert "- Property name: Vibe IZY" in rendered
        assert "- Booking channel: airbnb" in rendered
        assert "- Total price: 540.00" in rendered
        assert "- Currency: EUR" in rendered
        assert "- Message sent at: 2026-05-19T10:30:00Z" in rendered

    def test_zero_numeric_fields_are_skipped(self) -> None:
        """``num_guests=0`` / ``num_children=0`` must be treated as
        "not set" by the ``_add`` helper — the explicit ``value in
        (None, "", 0)`` skip clause."""
        ctx = ReservationContext(
            status="Inquiry",
            num_guests=0,
            num_children=0,
        )
        rendered = _format_reservation_context(ctx)

        assert "Adults" not in rendered
        assert "Children" not in rendered
        assert "- Status: Inquiry" in rendered

    def test_block_starts_with_header(self) -> None:
        """The non-empty block anchors on ``[RESERVATION FACTS]``."""
        ctx = ReservationContext(status="Confirmed")
        rendered = _format_reservation_context(ctx)
        assert rendered.startswith("[RESERVATION FACTS]\n")

    def test_strict_rules_footer_present(self) -> None:
        """Every populated block must include the anti-fabrication
        directive at the bottom."""
        ctx = ReservationContext(status="Confirmed", check_in="2026-06-01")
        rendered = _format_reservation_context(ctx)

        assert "STRICT RULES:" in rendered
        assert "Quote dates and times exactly as listed above." in rendered
        assert "do not invent a value" in rendered

    def test_strict_rules_disambiguate_check_in_out_times(self) -> None:
        """Pin the anti-swap rule that prevents LLM from quoting
        ``check_in_time`` as the standard check-out time (or vice
        versa).

        Foundation scenario #17 (Sandbox UI test 2026-05-19)
        failed: the LLM told the guest that the standard checkout
        time was 15:00 when the snapshot carried
        ``check_in_time=15:00`` and ``check_out_time=11:00`` -- the
        guest was misinformed that 15:00 was the standard departure
        time.  The block now spells out arrival vs departure
        explicitly so a future template refactor cannot drop the
        disambiguation without surfacing in this test.
        """
        ctx = ReservationContext(
            status="Inquiry",
            check_in_time="15:00",
            check_out_time="11:00",
        )
        rendered = _format_reservation_context(ctx)

        assert "- Check-in time: 15:00" in rendered
        assert "- Check-out time: 11:00" in rendered

        rules_section = rendered.split("STRICT RULES:", maxsplit=1)[1]
        rules_lower = rules_section.lower()
        assert "arrival" in rules_lower
        assert "departure" in rules_lower
        assert "never swap" in rules_lower
        assert "check-in time" in rules_lower
        assert "check-out time" in rules_lower

    def test_duck_typed_object_supported(self) -> None:
        """The function reads fields via ``getattr`` so a duck-typed
        object (not a ``ReservationContext``) must work too — this
        preserves the path used by tests / fixtures that build
        ad-hoc namespaces."""
        class _Snapshot:
            status = "Confirmed"
            check_in = "2026-06-01"
            check_in_time = ""
            check_out = ""
            check_out_time = ""
            guest_name = ""
            num_guests = 0
            num_children = 0
            property_name = ""
            booking_channel = ""
            total_price = ""
            currency = ""
            current_time = ""

        rendered = _format_reservation_context(_Snapshot())
        assert "- Status: Confirmed" in rendered
        assert "- Check-in date: 2026-06-01" in rendered


# ── Output shape stability across a representative scenario ────────────


@pytest.mark.parametrize(
    "calendar,expect_fallback",
    [
        (None, True),
        ([], True),
        ([CalendarDay()], True),  # empty date → silently skipped → fallback
    ],
)
def test_calendar_fallback_branches(
    calendar: object, expect_fallback: bool,
) -> None:
    """All "no usable rows" branches converge on the fallback block.
    Pins that the three converge points stay byte-identical."""
    rendered = _format_availability_calendar(calendar)
    if expect_fallback:
        assert rendered == _CALENDAR_NO_DATA_BLOCK


@pytest.mark.parametrize(
    "context,expect_fallback",
    [
        (None, True),
        (ReservationContext(), True),  # empty model → fallback
    ],
)
def test_reservation_fallback_branches(
    context: ReservationContext | None, expect_fallback: bool,
) -> None:
    """The two "nothing to render" branches converge."""
    rendered = _format_reservation_context(context)
    if expect_fallback:
        assert rendered == _RESERVATION_NO_DATA_BLOCK
