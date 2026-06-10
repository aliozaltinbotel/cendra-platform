"""Unit tests for :mod:`brain_engine.patterns.stage_labels`.

The helpers back the Excel ``Stage`` / ``Stage Group`` projection
emitted by both :mod:`api_server.routers.foundation_audit` (the
``/api/admin/foundation/analyze`` response) and the
``/api/v1/patterns/rules`` listing.  The two surfaces must produce
identical strings, so the data + the formatter behaviour are pinned
here once instead of in each endpoint's test module.
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.stage_labels import (
    STAGE_SHORT_LABEL,
    format_stage_group,
    lookup_stage_short,
)

# ---------------------------------------------------------------------------
# STAGE_SHORT_LABEL — pinned data
# ---------------------------------------------------------------------------


def test_stage_short_label_covers_full_1_to_9_range() -> None:
    """All 9 booking-lifecycle stages are mapped to a short label."""
    assert set(STAGE_SHORT_LABEL) == set(range(1, 10))


def test_stage_short_label_values_are_non_empty_strings() -> None:
    """Each mapped label is a non-empty string ready for direct emit."""
    for stage_number, label in STAGE_SHORT_LABEL.items():
        assert isinstance(label, str)
        assert label, f"empty label for stage {stage_number}"


@pytest.mark.parametrize(
    ("stage_number", "expected"),
    [
        (1, "Pre-booking"),
        (2, "Booking confirmation"),
        (3, "Pre-arrival"),
        (4, "Check-in day"),
        (5, "During stay"),
        (6, "Upsell / revenue"),
        (7, "Check-out"),
        (8, "Post-stay"),
        (9, "Internal operations"),
    ],
)
def test_stage_short_label_matches_excel_workbook(
    stage_number: int,
    expected: str,
) -> None:
    """Pinned verbatim from FOUNDATION_469_SCENARIOS.xlsx ``Stage`` column."""
    assert STAGE_SHORT_LABEL[stage_number] == expected


# ---------------------------------------------------------------------------
# format_stage_group
# ---------------------------------------------------------------------------


def test_format_stage_group_renders_excel_long_form() -> None:
    """Both inputs present → ``"Stage N — <label>"``."""
    result = format_stage_group(2, "Booking confirmation")
    assert result == "Stage 2 — Booking confirmation"


def test_format_stage_group_falls_back_to_label_when_number_missing() -> None:
    """``None`` stage_number must not produce ``"Stage None — …"``."""
    assert format_stage_group(None, "Booking confirmation") == (
        "Booking confirmation"
    )


def test_format_stage_group_falls_back_when_number_is_zero() -> None:
    """A falsey stage_number (``0``) is treated the same as ``None``."""
    assert format_stage_group(0, "Booking confirmation") == (
        "Booking confirmation"
    )


def test_format_stage_group_empty_label_returns_empty_string() -> None:
    """Both inputs empty → empty string, never ``"Stage 2 — "``."""
    assert format_stage_group(2, "") == ""


# ---------------------------------------------------------------------------
# lookup_stage_short
# ---------------------------------------------------------------------------


def test_lookup_stage_short_returns_excel_label_for_known_stage() -> None:
    """Known stage_number resolves to the Excel short label."""
    assert lookup_stage_short(3) == "Pre-arrival"


def test_lookup_stage_short_uses_fallback_when_number_missing() -> None:
    """``None`` stage_number falls through to the supplied fallback."""
    assert lookup_stage_short(None, "fallback-label") == "fallback-label"


def test_lookup_stage_short_uses_fallback_for_out_of_range_number() -> None:
    """A stage_number outside 1-9 falls through to the fallback."""
    assert lookup_stage_short(42, "fallback-label") == "fallback-label"


def test_lookup_stage_short_default_fallback_is_empty_string() -> None:
    """The fallback defaults to ``""`` so callers can guard with truthy checks."""
    assert lookup_stage_short(None) == ""
    assert lookup_stage_short(99) == ""
