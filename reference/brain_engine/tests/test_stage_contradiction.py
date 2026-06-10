"""Tests for the Foundation Layer stage contradiction module (Q5-C).

Pins the 2026-05-18 design:

* Calendar → ``BookingStage`` derivation uses 24h windows around
  check-in / check-out and a 7-day long-lead threshold.
* Catalog ``stage_number`` 1-9 maps to ``BookingStage`` via a
  fixed table; stage 9 (Internal / Ops) is stage-agnostic.
* Adjacent transition pairs (PRE_ARRIVAL ↔ CHECKIN etc.) count
  as compatible — Mümin's "guest at the door" 30 min early
  must not trip a false-positive.
* Missing calendar data ⇒ ``derive_stage_from_calendar``
  returns ``None`` and Q5-C silently no-ops.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain_engine.analysis.stage_contradiction import (
    compatible_stages,
    derive_stage_from_calendar,
    detect_stage_mismatch,
    scenario_stage_from_catalog,
)
from brain_engine.patterns.foundation_registry import FoundationScenario
from brain_engine.patterns.models import BookingStage

# ── derive_stage_from_calendar ─────────────────────────────── #


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # 30 days before check-in (>7d) → PRE_BOOKING
        ("2026-04-15T12:00:00Z", BookingStage.PRE_BOOKING),
        # 5 days before (within 7d, outside 24h) → PRE_ARRIVAL
        ("2026-05-10T12:00:00Z", BookingStage.PRE_ARRIVAL),
        # 30 min before check-in (within 24h window) → CHECKIN
        ("2026-05-15T13:30:00Z", BookingStage.CHECKIN),
        # On check-in day after the window → IN_STAY (cleared 24h)
        ("2026-05-16T15:00:00Z", BookingStage.IN_STAY),
        # 18h before check-out (within 24h) → CHECKOUT
        ("2026-05-19T18:00:00Z", BookingStage.CHECKOUT),
        # 3 days after check-out → POST_CHECKOUT
        ("2026-05-23T10:00:00Z", BookingStage.POST_CHECKOUT),
    ],
)
def test_derive_stage_six_buckets(now: str, expected: BookingStage) -> None:
    """All six BookingStage ranges resolve correctly."""
    stage = derive_stage_from_calendar(
        check_in="2026-05-15T14:00:00Z",
        check_out="2026-05-20T10:00:00Z",
        current_time=now,
    )
    assert stage == expected


def test_derive_stage_missing_check_in_returns_none() -> None:
    """No check_in ⇒ None (Q5-C silently no-ops)."""
    assert (
        derive_stage_from_calendar(
            check_in=None,
            check_out="2026-05-20T10:00:00Z",
            current_time="2026-05-16T12:00:00Z",
        )
        is None
    )


def test_derive_stage_missing_check_out_returns_none() -> None:
    """No check_out ⇒ None."""
    assert (
        derive_stage_from_calendar(
            check_in="2026-05-15T14:00:00Z",
            check_out=None,
            current_time="2026-05-16T12:00:00Z",
        )
        is None
    )


def test_derive_stage_empty_strings_return_none() -> None:
    """Empty / whitespace inputs ⇒ None (no crash)."""
    assert (
        derive_stage_from_calendar(
            check_in="",
            check_out="  ",
            current_time="2026-05-16T12:00:00Z",
        )
        is None
    )


def test_derive_stage_unparsable_input_returns_none() -> None:
    """Garbage timestamps ⇒ None (Q5-C never raises)."""
    assert (
        derive_stage_from_calendar(
            check_in="not-a-date",
            check_out="also-not-a-date",
            current_time="totally-junk",
        )
        is None
    )


def test_derive_stage_accepts_date_only() -> None:
    """Bare ``"YYYY-MM-DD"`` is treated as midnight UTC."""
    stage = derive_stage_from_calendar(
        check_in="2026-05-15",
        check_out="2026-05-20",
        current_time="2026-05-17T10:00:00Z",
    )
    assert stage == BookingStage.IN_STAY


def test_derive_stage_naive_datetime_assumed_utc() -> None:
    """Naive datetimes do not crash and resolve correctly."""
    stage = derive_stage_from_calendar(
        check_in="2026-05-15T14:00:00",
        check_out="2026-05-20T10:00:00",
        current_time="2026-05-17T12:00:00",
    )
    assert stage == BookingStage.IN_STAY


def test_derive_stage_uses_now_when_current_time_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``current_time=None`` falls back to ``datetime.now(UTC)``."""

    fake_now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz: object | None = None) -> datetime:
            del tz
            return fake_now

    monkeypatch.setattr(
        "brain_engine.analysis.stage_contradiction.datetime",
        _FakeDateTime,
    )
    stage = derive_stage_from_calendar(
        check_in="2026-05-15T14:00:00Z",
        check_out="2026-05-20T10:00:00Z",
        current_time=None,
    )
    assert stage == BookingStage.IN_STAY


# ── scenario_stage_from_catalog ───────────────────────────── #


def _make_scenario(stage_number: int) -> FoundationScenario:
    return FoundationScenario(
        scenario_id=f"s{stage_number}_1_test_scenario_title",
        title="Test scenario",
        stage_number=stage_number,
        stage_label="Test stage",
        trigger="trigger body",
    )


@pytest.mark.parametrize(
    ("stage_number", "expected"),
    [
        (1, BookingStage.PRE_BOOKING),
        (2, BookingStage.BOOKING_REVIEW),
        (3, BookingStage.PRE_ARRIVAL),
        (4, BookingStage.CHECKIN),
        (5, BookingStage.IN_STAY),
        (6, BookingStage.MODIFICATION),
        (7, BookingStage.CHECKOUT),
        (8, BookingStage.POST_CHECKOUT),
        (9, None),  # Ops / Internal — stage-agnostic
    ],
)
def test_scenario_stage_maps_all_nine_catalog_stages(
    stage_number: int,
    expected: BookingStage | None,
) -> None:
    """Every catalog stage_number resolves to the expected enum."""
    scenario = _make_scenario(stage_number)
    assert scenario_stage_from_catalog(scenario) == expected


def test_scenario_stage_none_input_returns_none() -> None:
    """Passing ``None`` short-circuits to ``None``."""
    assert scenario_stage_from_catalog(None) is None


# ── compatible_stages ──────────────────────────────────────── #


def test_compatible_stages_exact_match_compatible() -> None:
    """Exact stage equality is always compatible."""
    for stage in (
        BookingStage.PRE_BOOKING,
        BookingStage.IN_STAY,
        BookingStage.POST_CHECKOUT,
    ):
        assert compatible_stages(stage, stage) is True


@pytest.mark.parametrize(
    ("stage_a", "stage_b"),
    [
        (BookingStage.PRE_ARRIVAL, BookingStage.CHECKIN),
        (BookingStage.CHECKIN, BookingStage.IN_STAY),
        (BookingStage.IN_STAY, BookingStage.MODIFICATION),
        (BookingStage.IN_STAY, BookingStage.CHECKOUT),
        (BookingStage.CHECKOUT, BookingStage.POST_CHECKOUT),
        (BookingStage.PRE_BOOKING, BookingStage.BOOKING_REVIEW),
        (BookingStage.BOOKING_REVIEW, BookingStage.PRE_ARRIVAL),
    ],
)
def test_compatible_stages_documented_pairs(
    stage_a: BookingStage,
    stage_b: BookingStage,
) -> None:
    """Documented adjacent pairs count as compatible (both directions)."""
    assert compatible_stages(stage_a, stage_b) is True
    assert compatible_stages(stage_b, stage_a) is True


@pytest.mark.parametrize(
    ("stage_a", "stage_b"),
    [
        (BookingStage.PRE_BOOKING, BookingStage.IN_STAY),
        (BookingStage.PRE_BOOKING, BookingStage.POST_CHECKOUT),
        (BookingStage.PRE_ARRIVAL, BookingStage.POST_CHECKOUT),
        (BookingStage.IN_STAY, BookingStage.POST_CHECKOUT),
    ],
)
def test_compatible_stages_distant_pairs_incompatible(
    stage_a: BookingStage,
    stage_b: BookingStage,
) -> None:
    """Distant / non-adjacent stages are hard mismatches."""
    assert compatible_stages(stage_a, stage_b) is False
    assert compatible_stages(stage_b, stage_a) is False


# ── detect_stage_mismatch ──────────────────────────────────── #


def test_detect_mismatch_none_inputs_skip() -> None:
    """Missing inputs ⇒ None (no contradiction to report)."""
    assert detect_stage_mismatch(None, BookingStage.IN_STAY) is None
    assert detect_stage_mismatch(BookingStage.IN_STAY, None) is None
    assert detect_stage_mismatch(None, None) is None


def test_detect_mismatch_exact_match_no_detail() -> None:
    """Exact stage match returns None."""
    assert (
        detect_stage_mismatch(
            BookingStage.IN_STAY,
            BookingStage.IN_STAY,
        )
        is None
    )


def test_detect_mismatch_adjacent_pair_no_detail() -> None:
    """Adjacent compatible pairs return None (no false positive)."""
    assert (
        detect_stage_mismatch(
            BookingStage.PRE_ARRIVAL,
            BookingStage.CHECKIN,
        )
        is None
    )


def test_detect_mismatch_hard_mismatch_returns_detail() -> None:
    """Distant pairs return a stable-format detail string.

    Mümin's classic adversarial test: guest message implies a
    pre-arrival info question, calendar says guest left 5 days
    ago.  The detail must be log-grep stable.
    """
    detail = detect_stage_mismatch(
        BookingStage.POST_CHECKOUT,
        BookingStage.PRE_ARRIVAL,
    )
    assert detail == "calendar=post_checkout scenario=pre_arrival"


def test_detect_mismatch_detail_format_stable() -> None:
    """``"calendar=X scenario=Y"`` format pinned for log-grep."""
    detail = detect_stage_mismatch(
        BookingStage.IN_STAY,
        BookingStage.PRE_BOOKING,
    )
    assert detail is not None
    assert detail.startswith("calendar=")
    assert " scenario=" in detail
