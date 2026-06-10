"""Tests for ISO check_in/check_out timestamp normalisation (R11 / C4).

Sandbox UI test C4 (2026-05-19): UI panel displayed
"Check-in Date: May 18, 2026" + "Check-in Time: 14:00" as two
separate inputs.  But the AG-UI payload shipped
``check_in="2026-05-18T06:47:47.758Z"`` — a raw PMS ``createdAt``
timestamp leaked into the check-in slot.  ``_format_reservation_context``
echoed the value verbatim per the authoritative-snapshot contract,
so the guest saw ``"...06:47:47.758Z"`` in the reply.

This module pins the brain-side defence:
:func:`_iso_date_only` strips the time portion when the value
matches a full ``YYYY-MM-DD T…`` pattern.  ``check_in_time`` /
``check_out_time`` continue to carry the wall-clock value verbatim
on the separate fields the UI ships.
"""

from __future__ import annotations

import pytest

from api_server.server import (
    _iso_date_only,
    _reservation_context_from_state,
)

# ── _iso_date_only: helper contract ─────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", ""),
        ("2026-05-18", "2026-05-18"),
        ("2026-05-18T14:00:00", "2026-05-18"),
        ("2026-05-18T14:00:00Z", "2026-05-18"),
        ("2026-05-18T14:00:00+03:00", "2026-05-18"),
        ("2026-05-18T06:47:47.758Z", "2026-05-18"),  # the C4 case
        ("2026-12-31T23:59:59.999999Z", "2026-12-31"),
    ],
)
def test_iso_date_only_strips_time_portion(value: str, expected: str) -> None:
    """ISO-shaped inputs collapse to ``YYYY-MM-DD``.  The C4 PMS
    timestamp leak is the marquee case; the parametrise covers
    every legitimate ISO variant the wild ships."""
    assert _iso_date_only(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "May 18, 2026",
        "18/05/2026",
        "18.05.2026",
        "2026/05/18",
        "next Friday",
        "tomorrow",
        " ",  # whitespace, no T
    ],
)
def test_iso_date_only_passes_through_non_iso(value: str) -> None:
    """Non-ISO inputs must NOT be touched — free-text dates and
    weekday labels keep flowing through unchanged so a future
    caller that legitimately ships a localised string is
    unaffected."""
    assert _iso_date_only(value) == value


def test_iso_date_only_does_not_match_date_without_t() -> None:
    """The pattern requires a literal ``T`` after the date.  A
    bare ``YYYY-MM-DD`` flows through unchanged (already the
    desired shape — no work to do)."""
    assert _iso_date_only("2026-05-18") == "2026-05-18"


# ── _reservation_context_from_state: end-to-end C4 regression ──


def test_c4_regression_check_in_with_garbage_iso_normalised() -> None:
    """The exact 2026-05-19 C4 failure: UI ships
    ``"2026-05-18T06:47:47.758Z"`` for ``check_in`` while
    showing ``May 18, 2026`` + ``14:00`` in its panel.  After
    R11 the brain stores ``check_in="2026-05-18"`` and
    ``check_in_time="14:00"`` — the wall-clock value stays on
    its dedicated field."""
    ctx = _reservation_context_from_state(
        {
            "status": "Currently Hosting",
            "check_in": "2026-05-18T06:47:47.758Z",
            "check_in_time": "14:00",
            "check_out": "2026-05-20T10:00:00",
            "check_out_time": "10:00",
        },
    )
    assert ctx is not None
    assert ctx.check_in == "2026-05-18"
    assert ctx.check_in_time == "14:00"
    assert ctx.check_out == "2026-05-20"
    assert ctx.check_out_time == "10:00"


def test_date_only_check_in_unchanged() -> None:
    """When the UI ships a clean date-only check_in the parser
    must not corrupt it — the regex falls through and the value
    passes verbatim."""
    ctx = _reservation_context_from_state(
        {
            "status": "Confirmed",
            "check_in": "2026-05-18",
            "check_out": "2026-05-20",
        },
    )
    assert ctx is not None
    assert ctx.check_in == "2026-05-18"
    assert ctx.check_out == "2026-05-20"


def test_check_in_aliases_also_normalised() -> None:
    """The parser accepts ``check_in_date`` / ``checkIn`` aliases.
    Each of them must run through the same normaliser — a future
    client that ships an alias with a corrupted timestamp must
    be defended too."""
    ctx_alias_date = _reservation_context_from_state(
        {
            "status": "Confirmed",
            "check_in_date": "2026-05-18T06:47:47.758Z",
        },
    )
    ctx_alias_camel = _reservation_context_from_state(
        {
            "status": "Confirmed",
            "checkIn": "2026-05-18T06:47:47.758Z",
        },
    )
    assert ctx_alias_date is not None
    assert ctx_alias_date.check_in == "2026-05-18"
    assert ctx_alias_camel is not None
    assert ctx_alias_camel.check_in == "2026-05-18"


def test_nested_reservation_context_also_normalised() -> None:
    """A nested ``state.reservation_context`` payload must apply
    the same normalisation — older clients that wrap the snapshot
    in a nested key cannot escape the fix."""
    ctx = _reservation_context_from_state(
        {
            "reservation_context": {
                "status": "Confirmed",
                "check_in": "2026-05-18T06:47:47.758Z",
                "check_in_time": "14:00",
            },
        },
    )
    assert ctx is not None
    assert ctx.check_in == "2026-05-18"
    assert ctx.check_in_time == "14:00"


def test_missing_check_in_returns_empty() -> None:
    """A request that ships no check_in stays at the empty
    default — normalisation must not invent a date."""
    ctx = _reservation_context_from_state(
        {
            "status": "Inquiry",
        },
    )
    assert ctx is not None
    assert ctx.check_in == ""
    assert ctx.check_out == ""
