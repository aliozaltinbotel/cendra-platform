"""Concrete :class:`TimelineSource` adapters over the memory tiers.

Each adapter wraps one store and renders its records as
:class:`~brain_engine.memory.memory_timeline.TimelineEntry`s, covering the
three pillars of the temporal substrate:

* :class:`KnowledgeGraphSource` — the **knowledge graph** (bi-temporal facts
  / beliefs about the client), reconstructed as-of when requested.
* :class:`GuestOperationsSource` — the **real operations** (bookings and
  incidents — the live operational history).
* :class:`CustomerEventSource` — the **memory timeline** of recorded
  customer-operational events.

The clients are duck-typed (only the read methods are called), so tests
inject fakes and the adapters stay decoupled from store construction.
Additional tiers (episodic, temporal) are further adapters and plug into
:class:`MemoryTimeline` without changing it; they are intentionally not
wired here yet.

``as_of`` is honoured for reconstruction only where a tier is bi-temporal
(the knowledge graph); for event-time-only tiers the reader upper-bounds the
merged timeline by ``as_of``, so those adapters do not re-filter on it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from brain_engine.memory.memory_timeline import TimelineEntry, TimelineScope

if TYPE_CHECKING:
    from datetime import datetime as _dt

__all__ = [
    "CustomerEventSource",
    "GuestOperationsSource",
    "KnowledgeGraphSource",
]


class KnowledgeGraphSource:
    """Knowledge-graph facts/beliefs about the scoped entities, as-of T."""

    def __init__(self, knowledge_graph: Any) -> None:
        self._kg = knowledge_graph

    async def fetch(
        self,
        scope: TimelineScope,
        *,
        as_of: _dt | None,
    ) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []
        for entity_id in _unique(scope.guest_id, scope.property_id):
            nodes = await self._kg.get_entity_knowledge(entity_id, as_of=as_of)
            for node in nodes:
                at = _parse_iso(node.event_time) or _parse_iso(node.record_time)
                if at is None:
                    continue
                entries.append(
                    TimelineEntry(
                        at=at,
                        tier="kg",
                        kind=str(node.knowledge_type),
                        entity_id=node.entity_id or entity_id,
                        content=node.content,
                        source="knowledge_graph",
                        confidence=node.confidence,
                        payload={
                            "node_id": node.node_id,
                            "valid_from": node.valid_from,
                            "valid_until": node.valid_until,
                        },
                    ),
                )
        return entries


class GuestOperationsSource:
    """Real operations — bookings + incidents — for the scoped guest/property."""

    def __init__(self, guest_history: Any) -> None:
        self._gh = guest_history

    async def fetch(
        self,
        scope: TimelineScope,
        *,
        as_of: _dt | None,
    ) -> list[TimelineEntry]:
        del as_of  # event-time only; the reader upper-bounds the timeline.
        entries: list[TimelineEntry] = []

        if scope.guest_id:
            for booking in await self._gh.get_guest_bookings(scope.guest_id):
                entry = _booking_entry(booking)
                if entry is not None:
                    entries.append(entry)

        if scope.guest_id:
            incidents = await self._gh.get_guest_incidents(scope.guest_id)
        elif scope.property_id:
            incidents = await self._gh.get_property_incidents(scope.property_id)
        else:
            incidents = []
        for incident in incidents:
            entry = _incident_entry(incident)
            if entry is not None:
                entries.append(entry)

        return entries


class CustomerEventSource:
    """Recorded customer-operational events (the memory-timeline pillar)."""

    def __init__(self, customer_memory: Any) -> None:
        self._cm = customer_memory

    async def fetch(
        self,
        scope: TimelineScope,
        *,
        as_of: _dt | None,
    ) -> list[TimelineEntry]:
        del as_of  # event-time only; the reader upper-bounds the timeline.
        if not scope.customer_id:
            return []
        events = await self._cm.recall_events(
            scope.customer_id,
            property_id=scope.property_id or None,
        )
        entries: list[TimelineEntry] = []
        for event in events:
            at = _parse_iso(event.created_at)
            if at is None:
                continue
            entries.append(
                TimelineEntry(
                    at=at,
                    tier="memory",
                    kind=f"event:{event.event_type}",
                    entity_id=event.property_id or scope.customer_id,
                    content=event.summary,
                    source="customer_memory",
                    payload={
                        "outcome": event.outcome,
                        "revenue_impact": event.revenue_impact,
                        "guest_name": event.guest_name,
                        "reservation_id": event.reservation_id,
                        "property_id": event.property_id,
                    },
                ),
            )
        return entries


def _booking_entry(booking: Any) -> TimelineEntry | None:
    # ``created_at`` (when the reservation entered history) anchors the
    # timeline; ``check_in`` is the fallback and lives in the payload too.
    at = _parse_iso(booking.created_at) or _parse_iso(booking.check_in)
    if at is None:
        return None
    where = booking.property_name or booking.property_id
    return TimelineEntry(
        at=at,
        tier="operations",
        kind="booking",
        entity_id=booking.guest_id,
        content=(
            f"Booking {booking.status}: {where} "
            f"{booking.check_in}→{booking.check_out}"
        ),
        source="guest_history",
        payload={
            "booking_id": booking.booking_id,
            "property_id": booking.property_id,
            "check_in": booking.check_in,
            "check_out": booking.check_out,
            "status": booking.status,
            "num_guests": booking.num_guests,
            "total_price": booking.total_price,
            "currency": booking.currency,
            "booking_source": booking.booking_source,
            "payment_status": booking.payment_status,
        },
    )


def _incident_entry(incident: Any) -> TimelineEntry | None:
    at = _parse_iso(incident.created_at)
    if at is None:
        return None
    kind = f"incident:{incident.incident_type or 'unknown'}"
    return TimelineEntry(
        at=at,
        tier="operations",
        kind=kind,
        entity_id=incident.guest_id,
        content=(
            f"Incident {incident.incident_type or 'unknown'} "
            f"(severity {incident.severity}, {incident.status})"
        ),
        source="guest_history",
        payload={
            "incident_id": incident.incident_id,
            "property_id": incident.property_id,
            "status": incident.status,
            "severity": incident.severity,
            "resolved_at": incident.resolved_at,
            "damage_detected": incident.damage_detected,
            "claim_status": incident.claim_status,
        },
    )


def _unique(*ids: str) -> list[str]:
    """Non-empty ids, de-duplicated, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for value in ids:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
