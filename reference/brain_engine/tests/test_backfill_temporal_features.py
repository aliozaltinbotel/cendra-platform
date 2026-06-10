"""Tests for the Sprint 8 temporal-feature backfill script.

The script runs in two modes — dry-run (default) and ``--apply``.
Both share the same pure derivation pipeline:

* ``_parse_check_in`` — coerce a PMS ``check_in`` string into a
  timezone-aware UTC ``datetime``, returning ``None`` for missing,
  blank, or unparseable values.
* ``_hours_before_checkin`` — compute the temporal axis the
  synthesiser learns on, given a parsed ``check_in`` and the
  case's ``created_at``.
* ``_build_updated_snapshot`` — emit a new ``pms_snapshot`` dict
  with ``stage`` + (optionally) ``hours_before_checkin``
  appended; existing keys preserved.
* ``_snapshot_needs_update`` — gate predicate matching the SQL
  ``WHERE`` clause used at fetch time.

These functions are pure, so the entire correctness contract can
be pinned without a live database.  The DB-touching helpers
(``_fetch_rows``, ``_apply_updates``) are exercised manually on
dev with ``--dry-run``; an integration smoke harness is out of
scope for this commit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts.backfill_temporal_features import (
    _build_updated_snapshot,
    _extract_reservation_index_entries,
    _hours_before_checkin,
    _lead_time_hours,
    _parse_check_in,
    _snapshot_needs_update,
)

# ---------------------------------------------------------------------------
# _parse_check_in
# ---------------------------------------------------------------------------


def test_parse_check_in_date_only_anchors_midnight_utc() -> None:
    parsed = _parse_check_in("2026-05-12")
    assert parsed == datetime(2026, 5, 12, tzinfo=UTC)


def test_parse_check_in_iso_with_z_suffix() -> None:
    parsed = _parse_check_in("2026-05-12T15:30:00Z")
    assert parsed == datetime(2026, 5, 12, 15, 30, tzinfo=UTC)


def test_parse_check_in_iso_naive_assumed_utc() -> None:
    parsed = _parse_check_in("2026-05-12T15:30:00")
    assert parsed == datetime(2026, 5, 12, 15, 30, tzinfo=UTC)


def test_parse_check_in_iso_with_offset_normalised_to_utc() -> None:
    parsed = _parse_check_in("2026-05-12T15:30:00+03:00")
    assert parsed == datetime(2026, 5, 12, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", None, 12345, "not-a-date", "2026-13-99", "yesterday"],
)
def test_parse_check_in_returns_none_for_unparseable(raw: object) -> None:
    assert _parse_check_in(raw) is None


# ---------------------------------------------------------------------------
# _hours_before_checkin
# ---------------------------------------------------------------------------


def test_hours_before_checkin_positive_when_case_logged_before() -> None:
    created = datetime(2026, 5, 5, 0, 0, tzinfo=UTC)
    out = _hours_before_checkin(check_in="2026-05-12", created_at=created)
    # Exactly 7 days = 168 hours.
    assert out == pytest.approx(168.0)


def test_hours_before_checkin_negative_when_case_logged_after() -> None:
    created = datetime(2026, 5, 15, 0, 0, tzinfo=UTC)
    out = _hours_before_checkin(check_in="2026-05-12", created_at=created)
    # Logged 3 days after check-in → -72h.
    assert out == pytest.approx(-72.0)


def test_hours_before_checkin_returns_none_for_unparseable_check_in() -> None:
    created = datetime(2026, 5, 5, 0, 0, tzinfo=UTC)
    assert _hours_before_checkin(check_in=None, created_at=created) is None
    assert (
        _hours_before_checkin(check_in="garbage", created_at=created)
        is None
    )


def test_hours_before_checkin_handles_naive_created_at() -> None:
    created = datetime(2026, 5, 5, 0, 0)  # naive — assumed UTC
    out = _hours_before_checkin(check_in="2026-05-12", created_at=created)
    assert out == pytest.approx(168.0)


def test_hours_before_checkin_rounds_to_four_decimals() -> None:
    created = datetime(2026, 5, 5, 12, 17, 23, tzinfo=UTC)
    out = _hours_before_checkin(check_in="2026-05-12", created_at=created)
    # 6 days, 11h, 42min, 37s → 155.7103 hours.
    assert out == pytest.approx(155.7103, abs=1e-4)


# ---------------------------------------------------------------------------
# _build_updated_snapshot
# ---------------------------------------------------------------------------


def test_build_updated_snapshot_appends_stage_and_hours() -> None:
    pms = {"adults": 2, "source": "bookingcom"}
    out = _build_updated_snapshot(
        pms_snapshot=pms,
        stage="pre_arrival",
        hours_before=120.0,
    )
    assert out == {
        "adults": 2,
        "source": "bookingcom",
        "stage": "pre_arrival",
        "hours_before_checkin": 120.0,
    }


def test_build_updated_snapshot_does_not_mutate_input() -> None:
    pms = {"adults": 2}
    _build_updated_snapshot(
        pms_snapshot=pms,
        stage="in_stay",
        hours_before=-12.0,
    )
    assert pms == {"adults": 2}  # input untouched


def test_build_updated_snapshot_preserves_existing_keys() -> None:
    pms = {"stage": "post_stay", "adults": 1}
    out = _build_updated_snapshot(
        pms_snapshot=pms,
        stage="pre_arrival",
        hours_before=24.0,
    )
    # ``setdefault`` keeps the original ``stage`` value — the
    # script never overwrites a key the live ingestion already
    # populated.
    assert out["stage"] == "post_stay"
    assert out["hours_before_checkin"] == 24.0
    assert out["adults"] == 1


def test_build_updated_snapshot_omits_hours_when_unparseable() -> None:
    pms = {"adults": 2}
    out = _build_updated_snapshot(
        pms_snapshot=pms,
        stage="pre_arrival",
        hours_before=None,
    )
    assert "hours_before_checkin" not in out
    assert out["stage"] == "pre_arrival"


# ---------------------------------------------------------------------------
# _snapshot_needs_update
# ---------------------------------------------------------------------------


def test_needs_update_true_when_stage_missing() -> None:
    assert (
        _snapshot_needs_update(
            pms_snapshot={"adults": 2},
            stage_only=False,
        )
        is True
    )
    assert (
        _snapshot_needs_update(
            pms_snapshot={"adults": 2},
            stage_only=True,
        )
        is True
    )


def test_needs_update_true_when_hours_missing_and_not_stage_only() -> None:
    assert (
        _snapshot_needs_update(
            pms_snapshot={"stage": "pre_arrival"},
            stage_only=False,
        )
        is True
    )


def test_needs_update_false_when_stage_only_and_stage_present() -> None:
    assert (
        _snapshot_needs_update(
            pms_snapshot={"stage": "in_stay"},
            stage_only=True,
        )
        is False
    )


def test_needs_update_false_when_both_keys_present() -> None:
    snapshot = {"stage": "pre_arrival", "hours_before_checkin": 48.0}
    assert (
        _snapshot_needs_update(
            pms_snapshot=snapshot,
            stage_only=False,
        )
        is False
    )


# ---------------------------------------------------------------------------
# Idempotency end-to-end (purely functional pipeline)
# ---------------------------------------------------------------------------


def test_pipeline_idempotent_after_first_pass() -> None:
    """Running the derivation twice produces the same final dict."""
    pms = {"adults": 2, "check_in": "2026-05-12", "source": "bookingcom"}
    created = datetime(2026, 5, 5, 0, 0, tzinfo=UTC)
    hours = _hours_before_checkin(
        check_in=pms.get("check_in"),
        created_at=created,
    )
    first = _build_updated_snapshot(
        pms_snapshot=pms,
        stage="pre_arrival",
        hours_before=hours,
    )
    # Second pass on the result of the first should be a no-op
    # (snapshot already complete; the gate predicate returns False).
    assert (
        _snapshot_needs_update(
            pms_snapshot=first,
            stage_only=False,
        )
        is False
    )
    second = _build_updated_snapshot(
        pms_snapshot=first,
        stage="pre_arrival",
        hours_before=hours,
    )
    assert second == first


# ---------------------------------------------------------------------------
# Sprint 8 ext — _lead_time_hours
# ---------------------------------------------------------------------------


def test_lead_time_positive_typical_booking() -> None:
    created = datetime(2026, 5, 5, 0, 0, tzinfo=UTC)
    out = _lead_time_hours(
        check_in="2026-05-12",
        reservation_created_at=created,
    )
    assert out == pytest.approx(168.0)


def test_lead_time_clamped_at_zero_for_post_checkin_booking() -> None:
    """Booking created after arrival is physically impossible.

    Negative lead times would teach the synthesiser a bogus split
    so we clamp to 0.0 — the rule will simply not fire for the
    pre-Sprint cases that suffered from time-travel writes.
    """
    created = datetime(2026, 5, 20, 0, 0, tzinfo=UTC)
    out = _lead_time_hours(
        check_in="2026-05-12",
        reservation_created_at=created,
    )
    assert out == 0.0


def test_lead_time_returns_none_when_reservation_created_missing() -> None:
    out = _lead_time_hours(
        check_in="2026-05-12",
        reservation_created_at=None,
    )
    assert out is None


def test_lead_time_returns_none_when_check_in_unparseable() -> None:
    created = datetime(2026, 5, 5, 0, 0, tzinfo=UTC)
    out = _lead_time_hours(
        check_in="garbage",
        reservation_created_at=created,
    )
    assert out is None


# ---------------------------------------------------------------------------
# Sprint 8 ext — _extract_reservation_index_entries
# ---------------------------------------------------------------------------


def test_extract_index_emits_one_entry_per_id_field() -> None:
    page = {
        "reservations": [
            {
                "id": "internal-1",
                "channelEntityId": "chan-1",
                "customerChannelId": "cust-1",
                "pmsId": "PMS-1",
                "data": {
                    "pmsId": "PMS-1-inner",
                    "createdAt": "2026-05-01T10:00:00Z",
                },
            },
        ],
    }
    entries = _extract_reservation_index_entries(page)
    keys = sorted(entry[0] for entry in entries)
    # Five identifier fields → five index entries (so any of them
    # used by ``decision_cases.reservation_id`` matches).
    assert keys == [
        "PMS-1",
        "PMS-1-inner",
        "chan-1",
        "cust-1",
        "internal-1",
    ]
    timestamps = {entry[1] for entry in entries}
    assert timestamps == {
        datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    }


def test_extract_index_skips_rows_without_created_at() -> None:
    page = {
        "reservations": [
            {"id": "no-created", "data": {"pmsId": "x"}},
            {
                "id": "good",
                "data": {"createdAt": "2026-04-30T00:00:00Z"},
            },
        ],
    }
    entries = _extract_reservation_index_entries(page)
    keys = {entry[0] for entry in entries}
    assert keys == {"good"}


def test_extract_index_returns_empty_for_malformed_payload() -> None:
    assert _extract_reservation_index_entries({}) == []
    assert _extract_reservation_index_entries({"reservations": None}) == []
    assert _extract_reservation_index_entries(
        {"reservations": "not-a-list"},  # type: ignore[arg-type]
    ) == []


# ---------------------------------------------------------------------------
# Sprint 8 ext — _snapshot_needs_update with --with-lead-time
# ---------------------------------------------------------------------------


def test_needs_update_with_lead_time_admits_complete_row() -> None:
    snapshot = {"stage": "pre_arrival", "hours_before_checkin": 48.0}
    assert (
        _snapshot_needs_update(
            pms_snapshot=snapshot,
            stage_only=False,
            with_lead_time=True,
        )
        is True
    )


def test_needs_update_with_lead_time_skips_when_lead_present() -> None:
    snapshot = {
        "stage": "pre_arrival",
        "hours_before_checkin": 48.0,
        "lead_time_hours": 200.0,
    }
    assert (
        _snapshot_needs_update(
            pms_snapshot=snapshot,
            stage_only=False,
            with_lead_time=True,
        )
        is False
    )


def test_build_snapshot_appends_lead_time_when_supplied() -> None:
    out = _build_updated_snapshot(
        pms_snapshot={"stage": "pre_arrival", "hours_before_checkin": 48.0},
        stage="pre_arrival",
        hours_before=48.0,
        lead_time=240.0,
    )
    assert out["lead_time_hours"] == 240.0


def test_build_snapshot_omits_lead_time_when_none() -> None:
    out = _build_updated_snapshot(
        pms_snapshot={"adults": 1},
        stage="pre_arrival",
        hours_before=48.0,
        lead_time=None,
    )
    assert "lead_time_hours" not in out
