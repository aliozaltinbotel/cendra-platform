"""Tests for the deterministic past+present fusion (Phase 2).

Two layers:

* :func:`classify_operation` — the pure operational-phase classifier over a
  single timeline entry (bookings by stay window, incidents by status,
  non-operations / void / unparseable → ``None``);
* :func:`build_temporal_context` over a fake timeline — partition of the
  history into ``live`` / ``upcoming``, soonest-first upcoming order,
  history preserved, ``as_of`` default + passthrough, empty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from brain_engine.memory.memory_timeline import TimelineEntry, TimelineScope
from brain_engine.memory.temporal_context import OperationPhase
from brain_engine.memory.temporal_fusion import (
    build_temporal_context,
    classify_operation,
)


def _t(day: int) -> datetime:
    return datetime(2026, 5, day, 12, 0, tzinfo=UTC)


_ANCHOR = _t(15)
_SCOPE = TimelineScope(property_id="p1", guest_id="g1", customer_id="c1")


def _booking(
    *,
    at: datetime,
    check_in: int,
    check_out: int,
    status: str = "confirmed",
) -> TimelineEntry:
    return TimelineEntry(
        at=at,
        tier="operations",
        kind="booking",
        entity_id="g1",
        content="booking",
        source="guest_history",
        payload={
            "check_in": _t(check_in).isoformat(),
            "check_out": _t(check_out).isoformat(),
            "status": status,
        },
    )


def _incident(
    *,
    at: datetime,
    status: str = "open",
    resolved_at: str | None = None,
) -> TimelineEntry:
    return TimelineEntry(
        at=at,
        tier="operations",
        kind="incident:damage",
        entity_id="g1",
        content="incident",
        source="guest_history",
        payload={"status": status, "resolved_at": resolved_at},
    )


def _fact(at: datetime) -> TimelineEntry:
    return TimelineEntry(
        at=at,
        tier="kg",
        kind="fact",
        entity_id="g1",
        content="fact",
        source="knowledge_graph",
    )


class _FakeTimeline:
    """Returns fixed entries; records the read arguments it was given."""

    def __init__(self, entries: list[TimelineEntry]) -> None:
        self._entries = entries
        self.seen: dict[str, Any] = {}

    async def read(
        self,
        scope: TimelineScope,
        *,
        as_of: datetime | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[TimelineEntry]:
        self.seen = {
            "scope": scope,
            "as_of": as_of,
            "since": since,
            "limit": limit,
        }
        return list(self._entries)


# ── classify_operation ──────────────────────────────────────────────


def test_non_operation_entry_is_unclassified() -> None:
    assert classify_operation(_fact(_t(3)), _ANCHOR) is None


def test_booking_live_mid_stay() -> None:
    entry = _booking(at=_t(1), check_in=14, check_out=17)
    assert classify_operation(entry, _ANCHOR) is OperationPhase.LIVE


def test_booking_upcoming_future_arrival() -> None:
    entry = _booking(at=_t(2), check_in=20, check_out=23)
    assert classify_operation(entry, _ANCHOR) is OperationPhase.UPCOMING


def test_booking_past_after_checkout() -> None:
    entry = _booking(at=_t(1), check_in=2, check_out=5)
    assert classify_operation(entry, _ANCHOR) is OperationPhase.PAST


def test_cancelled_booking_is_unclassified_even_if_future() -> None:
    entry = _booking(at=_t(2), check_in=20, check_out=23, status="cancelled")
    assert classify_operation(entry, _ANCHOR) is None


def test_canceled_american_spelling_is_unclassified() -> None:
    entry = _booking(at=_t(2), check_in=20, check_out=23, status="Canceled")
    assert classify_operation(entry, _ANCHOR) is None


def test_booking_with_unparseable_dates_is_unclassified() -> None:
    entry = TimelineEntry(
        at=_t(2),
        tier="operations",
        kind="booking",
        entity_id="g1",
        content="booking",
        source="guest_history",
        payload={"check_in": "", "check_out": "n/a", "status": "confirmed"},
    )
    assert classify_operation(entry, _ANCHOR) is None


def test_open_incident_is_live() -> None:
    assert (
        classify_operation(
            _incident(at=_t(6), status="open"),
            _ANCHOR,
        )
        is OperationPhase.LIVE
    )


def test_resolved_incident_is_past() -> None:
    assert (
        classify_operation(
            _incident(at=_t(6), status="resolved"),
            _ANCHOR,
        )
        is OperationPhase.PAST
    )


def test_incident_with_resolved_at_is_past() -> None:
    entry = _incident(at=_t(6), status="open", resolved_at=_t(7).isoformat())
    assert classify_operation(entry, _ANCHOR) is OperationPhase.PAST


def test_unknown_operation_kind_is_unclassified() -> None:
    entry = TimelineEntry(
        at=_t(2),
        tier="operations",
        kind="payment",
        entity_id="g1",
        content="payment",
        source="guest_history",
    )
    assert classify_operation(entry, _ANCHOR) is None


# ── build_temporal_context ──────────────────────────────────────────


async def test_partitions_history_into_live_and_upcoming() -> None:
    live_booking = _booking(at=_t(1), check_in=14, check_out=17)
    open_incident = _incident(at=_t(8), status="open")
    upcoming_far = _booking(at=_t(2), check_in=20, check_out=23)
    upcoming_soon = _booking(at=_t(3), check_in=16, check_out=18)
    past_booking = _booking(at=_t(1), check_in=2, check_out=5)
    cancelled = _booking(
        at=_t(4),
        check_in=20,
        check_out=23,
        status="cancelled",
    )
    fact = _fact(_t(9))
    history = [
        past_booking,
        live_booking,
        open_incident,
        upcoming_far,
        upcoming_soon,
        cancelled,
        fact,
    ]
    timeline = _FakeTimeline(history)

    ctx = await build_temporal_context(timeline, _SCOPE, as_of=_ANCHOR)

    # History is preserved verbatim.
    assert ctx.history == history
    # Live = the two in-progress operations, in history order.
    assert ctx.live == [live_booking, open_incident]
    # Upcoming = future arrivals only, sorted soonest-first (16 before 20).
    assert ctx.upcoming == [upcoming_soon, upcoming_far]
    assert ctx.as_of == _ANCHOR
    assert ctx.scope == _SCOPE


async def test_anchor_and_window_passed_to_timeline() -> None:
    timeline = _FakeTimeline([])
    await build_temporal_context(
        timeline,
        _SCOPE,
        as_of=_ANCHOR,
        since=_t(10),
        limit=50,
    )
    assert timeline.seen == {
        "scope": _SCOPE,
        "as_of": _ANCHOR,
        "since": _t(10),
        "limit": 50,
    }


async def test_as_of_defaults_to_now() -> None:
    timeline = _FakeTimeline([])
    ctx = await build_temporal_context(timeline, _SCOPE)
    # Resolved to an aware "now" and threaded to the timeline unchanged.
    assert ctx.as_of.tzinfo is not None
    assert timeline.seen["as_of"] == ctx.as_of


async def test_empty_timeline_yields_empty_context() -> None:
    ctx = await build_temporal_context(
        _FakeTimeline([]), _SCOPE, as_of=_ANCHOR
    )
    assert ctx.history == []
    assert ctx.live == []
    assert ctx.upcoming == []
