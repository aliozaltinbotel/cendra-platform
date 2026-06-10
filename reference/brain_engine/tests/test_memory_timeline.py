"""Tests for the unified memory timeline (Phase 1 step 1b).

Two layers:

* the :class:`MemoryTimeline` reader over fake sources — merge + chronological
  sort, since/until windowing, as_of upper-bound (+ as_of passed to sources),
  most-recent limit, best-effort source isolation, empty;
* the concrete adapters over fake tier clients — knowledge graph (as_of
  threaded through), real operations (bookings + incidents, guest- and
  property-scoped), customer events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from brain_engine.memory.memory_timeline import (
    MemoryTimeline,
    TimelineEntry,
    TimelineScope,
)
from brain_engine.memory.timeline_sources import (
    CustomerEventSource,
    GuestOperationsSource,
    KnowledgeGraphSource,
)


def _t(day: int) -> datetime:
    return datetime(2026, 5, day, 12, 0, tzinfo=UTC)


def _entry(at: datetime, kind: str = "fact") -> TimelineEntry:
    return TimelineEntry(
        at=at, tier="kg", kind=kind, entity_id="g1", content=kind,
        source="test",
    )


class _FixedSource:
    """A source returning fixed entries; records the as_of it was given."""

    def __init__(self, entries: list[TimelineEntry], *, boom: bool = False) -> None:
        self._entries = entries
        self._boom = boom
        self.seen_as_of: Any = "unset"

    async def fetch(
        self, scope: TimelineScope, *, as_of: datetime | None,
    ) -> list[TimelineEntry]:
        self.seen_as_of = as_of
        if self._boom:
            raise RuntimeError("tier down")
        return list(self._entries)


_SCOPE = TimelineScope(property_id="p1", guest_id="g1", customer_id="c1")


# ── reader: merge / sort / window / as_of / limit / isolation ───────


async def test_merges_and_sorts_ascending() -> None:
    src1 = _FixedSource([_entry(_t(5)), _entry(_t(1))])
    src2 = _FixedSource([_entry(_t(3))])
    timeline = MemoryTimeline([src1, src2])

    out = await timeline.read(_SCOPE)

    assert [e.at for e in out] == [_t(1), _t(3), _t(5)]


async def test_since_until_window() -> None:
    src = _FixedSource([_entry(_t(1)), _entry(_t(3)), _entry(_t(5))])
    out = await MemoryTimeline([src]).read(_SCOPE, since=_t(2), until=_t(4))
    assert [e.at for e in out] == [_t(3)]


async def test_as_of_drops_future_and_is_passed_to_sources() -> None:
    src = _FixedSource([_entry(_t(1)), _entry(_t(3)), _entry(_t(9))])
    out = await MemoryTimeline([src]).read(_SCOPE, as_of=_t(5))
    assert [e.at for e in out] == [_t(1), _t(3)]
    assert src.seen_as_of == _t(5)


async def test_limit_keeps_most_recent_ascending() -> None:
    src = _FixedSource([_entry(_t(d)) for d in (1, 2, 3, 4, 5)])
    out = await MemoryTimeline([src]).read(_SCOPE, limit=2)
    # The two newest, still oldest-first.
    assert [e.at for e in out] == [_t(4), _t(5)]


async def test_failing_source_is_skipped() -> None:
    good = _FixedSource([_entry(_t(2))])
    bad = _FixedSource([_entry(_t(1))], boom=True)
    out = await MemoryTimeline([bad, good]).read(_SCOPE)
    assert [e.at for e in out] == [_t(2)]


async def test_empty() -> None:
    assert await MemoryTimeline([]).read(_SCOPE) == []


# ── adapters ────────────────────────────────────────────────────────


class _FakeKG:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def get_entity_knowledge(
        self, entity_id: str, as_of: datetime | None = None,
    ) -> list[Any]:
        self.calls.append((entity_id, as_of))
        return [
            SimpleNamespace(
                node_id=f"n-{entity_id}",
                content=f"fact about {entity_id}",
                knowledge_type="fact",
                entity_id=entity_id,
                confidence=0.9,
                event_time=_t(3).isoformat(),
                record_time=_t(3).isoformat(),
                valid_from=_t(3).isoformat(),
                valid_until=None,
            ),
        ]


async def test_kg_source_maps_and_threads_as_of() -> None:
    kg = _FakeKG()
    out = await KnowledgeGraphSource(kg).fetch(_SCOPE, as_of=_t(5))

    # One entry per scoped entity (guest + property), as_of threaded down.
    assert {e.entity_id for e in out} == {"g1", "p1"}
    assert all(e.tier == "kg" and e.kind == "fact" for e in out)
    assert all(e.confidence == 0.9 for e in out)
    assert ("g1", _t(5)) in kg.calls and ("p1", _t(5)) in kg.calls


class _FakeGuestHistory:
    async def get_guest_bookings(self, guest_id: str) -> list[Any]:
        return [
            SimpleNamespace(
                booking_id="b1", guest_id=guest_id, property_id="p1",
                property_name="Villa", check_in="2026-05-02T15:00:00+00:00",
                check_out="2026-05-05T11:00:00+00:00", status="confirmed",
                num_guests=2, total_price=300.0, currency="EUR",
                booking_source="airbnb", payment_status="paid",
                created_at=_t(1).isoformat(),
            ),
        ]

    async def get_guest_incidents(self, guest_id: str) -> list[Any]:
        return [_incident(guest_id, "p1")]

    async def get_property_incidents(self, property_id: str) -> list[Any]:
        return [_incident("g1", property_id)]


def _incident(guest_id: str, property_id: str) -> Any:
    return SimpleNamespace(
        incident_id="i1", guest_id=guest_id, property_id=property_id,
        incident_type="damage", status="open", severity=3,
        created_at=_t(4).isoformat(), resolved_at=None,
        damage_detected=True, claim_status=None,
    )


async def test_guest_operations_source_guest_scope() -> None:
    out = await GuestOperationsSource(_FakeGuestHistory()).fetch(_SCOPE, as_of=None)
    kinds = sorted(e.kind for e in out)
    assert kinds == ["booking", "incident:damage"]
    assert all(e.tier == "operations" for e in out)
    booking = next(e for e in out if e.kind == "booking")
    assert booking.at == _t(1)
    assert booking.payload["total_price"] == 300.0


async def test_guest_operations_source_property_only() -> None:
    scope = TimelineScope(property_id="p1")  # no guest_id
    out = await GuestOperationsSource(_FakeGuestHistory()).fetch(scope, as_of=None)
    # Only property incidents, no bookings.
    assert [e.kind for e in out] == ["incident:damage"]


class _FakeCustomerMemory:
    async def recall_events(
        self, customer_id: str, *, property_id: str | None = None,
    ) -> list[Any]:
        return [
            SimpleNamespace(
                event_type="override", summary="PM granted late checkout",
                created_at=_t(2).isoformat(), property_id=property_id or "p1",
                outcome="override", revenue_impact=0.0, guest_name="John",
                reservation_id="r1",
            ),
        ]


async def test_customer_event_source() -> None:
    out = await CustomerEventSource(_FakeCustomerMemory()).fetch(_SCOPE, as_of=None)
    assert len(out) == 1
    assert out[0].tier == "memory"
    assert out[0].kind == "event:override"
    assert out[0].content == "PM granted late checkout"


async def test_customer_event_source_requires_customer_id() -> None:
    scope = TimelineScope(property_id="p1", guest_id="g1")  # no customer_id
    out = await CustomerEventSource(_FakeCustomerMemory()).fetch(scope, as_of=None)
    assert out == []
