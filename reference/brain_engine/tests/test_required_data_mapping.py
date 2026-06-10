"""Tests for the required_data label → snapshot mapping (Q5-B).

Pins the 2026-05-18 mapping audit produced by
``foundation_469_oneri_senaryo_numaralari.txt`` work-stream:
40 mappable labels across the 76 unique catalog labels.  The
mapping is conservative on purpose — labels we cannot route to
one of the four typed snapshot buckets fall to :data:`UNMAPPED`
so the orchestrator's Q5-B gate fails open rather than fabricates
a false positive.
"""

from __future__ import annotations

import pytest

from brain_engine.analysis.required_data import (
    REQUIRED_DATA_SNAPSHOTS,
    UNMAPPED,
    classify_required_data_check,
    find_missing_required_data,
)

# ── classify_required_data_check ──────────────────────────── #


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        # pms_snapshot
        ("PMS reservation", "pms_snapshot"),
        ("channel policy", "pms_snapshot"),
        ("Reservation Status", "pms_snapshot"),
        ("amenity list", "pms_snapshot"),
        ("currency", "pms_snapshot"),
        # calendar_snapshot
        ("arrival time", "calendar_snapshot"),
        ("Stay Dates", "calendar_snapshot"),
        ("lead time", "calendar_snapshot"),
        ("turnover window", "calendar_snapshot"),
        # ops_snapshot
        ("cleaning schedule", "ops_snapshot"),
        ("vendor availability", "ops_snapshot"),
        ("housekeeping readiness", "ops_snapshot"),
        ("smart lock status", "ops_snapshot"),
        # guest_snapshot
        ("guest history", "guest_snapshot"),
        ("Guest Profile", "guest_snapshot"),
        ("guest verification", "guest_snapshot"),
    ],
)
def test_classify_known_labels(label: str, expected: str) -> None:
    """Every mapped label resolves to the right snapshot bucket."""
    assert classify_required_data_check(label) == expected


def test_classify_is_case_insensitive() -> None:
    """Capitalisation in the catalog must not change the bucket."""
    assert classify_required_data_check("PMS RESERVATION") == "pms_snapshot"
    assert classify_required_data_check("Pms Reservation") == "pms_snapshot"


def test_classify_strips_whitespace() -> None:
    """Padded spacing in the catalog must not break the lookup."""
    assert (
        classify_required_data_check("  cleaning schedule  ") == "ops_snapshot"
    )


@pytest.mark.parametrize(
    "label",
    [
        # Knowledge / policy categories — Q5-B.2 candidates.
        "house rules",
        "property facts",
        "property sop",
        "property knowledge",
        "compensation policy",
        "owner preferences",
        "approval thresholds",
        "id/rental agreement",
        "photo/video evidence",
        # Truly nonsensical labels.
        "completely_unknown_label",
        "",
        "   ",
    ],
)
def test_classify_unknown_or_unmapped_returns_unmapped(label: str) -> None:
    """Unmapped / blank / unknown labels collapse to UNMAPPED."""
    assert classify_required_data_check(label) == UNMAPPED


def test_required_data_snapshots_constant_pins_four_buckets() -> None:
    """The public constant matches the AnalysisEvent snapshot fields."""
    assert REQUIRED_DATA_SNAPSHOTS == (
        "pms_snapshot",
        "calendar_snapshot",
        "ops_snapshot",
        "guest_snapshot",
    )


# ── find_missing_required_data ───────────────────────────── #


def test_find_missing_empty_required_returns_empty() -> None:
    """No required checks ⇒ nothing missing, regardless of snapshots."""
    assert find_missing_required_data((), {}) == ()
    assert (
        find_missing_required_data(
            (),
            {"pms_snapshot": {"reservation_id": "r1"}},
        )
        == ()
    )


def test_find_missing_skips_unmapped_labels() -> None:
    """Knowledge-category labels are observed elsewhere, not gated."""
    # All 3 labels are UNMAPPED — even with empty snapshots, none
    # contribute to the missing list (Q5-B.1 design choice).
    assert (
        find_missing_required_data(
            ("house rules", "property sop", "compensation policy"),
            {},
        )
        == ()
    )


def test_find_missing_reports_empty_snapshot_for_mapped_label() -> None:
    """A mapped label with an empty target snapshot is reported."""
    result = find_missing_required_data(
        ("pms reservation", "arrival time"),
        {},
    )
    assert "pms reservation" in result
    assert "arrival time" in result


def test_find_missing_skips_satisfied_label() -> None:
    """A mapped label with a non-empty snapshot is satisfied."""
    snapshots = {
        "pms_snapshot": {"reservation_id": "r1"},
        "calendar_snapshot": {"arrival_time": "15:00"},
    }
    result = find_missing_required_data(
        ("pms reservation", "arrival time"),
        snapshots,
    )
    assert result == ()


def test_find_missing_preserves_order_and_dedups() -> None:
    """Verbatim labels in input order; case-insensitive dedup."""
    result = find_missing_required_data(
        (
            "arrival time",
            "PMS reservation",
            "Arrival Time",  # dup, different case
            "cleaning schedule",
        ),
        {},
    )
    # Order: first occurrence wins; "Arrival Time" dedupped.
    assert result == (
        "arrival time",
        "PMS reservation",
        "cleaning schedule",
    )


def test_find_missing_treats_empty_snapshot_as_missing() -> None:
    """An empty-dict snapshot counts as missing data."""
    snapshots = {
        "pms_snapshot": {},  # explicitly empty
        "calendar_snapshot": {"arrival_time": "15:00"},
    }
    result = find_missing_required_data(
        ("pms reservation", "arrival time"),
        snapshots,
    )
    assert result == ("pms reservation",)


def test_find_missing_handles_missing_snapshot_key() -> None:
    """Snapshot key absent ⇒ treated as empty (gate-safe default)."""
    # Snapshots dict has guest_snapshot only; pms_snapshot key
    # is absent entirely.  Should be reported as missing.
    result = find_missing_required_data(
        ("PMS reservation",),
        {"guest_snapshot": {"guest_id": "g1"}},
    )
    assert result == ("PMS reservation",)


def test_find_missing_drops_blank_check_entries() -> None:
    """Blank / whitespace required_data entries are ignored."""
    result = find_missing_required_data(
        ("", "  ", "arrival time"),
        {},
    )
    assert result == ("arrival time",)
