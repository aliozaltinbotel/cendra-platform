"""Deterministic past+present fusion into a :class:`TemporalContext`.

Phase 2 of the temporal substrate — **no LLM, read-only**.  It combines
two temporal axes of the same client:

* the **record-time** past — the as-of memory timeline
  (:meth:`~brain_engine.memory.memory_timeline.MemoryTimeline.read`), the
  chronological story of what was known and when;
* the **operational-time** present — each real operation classified by its
  own dates / status against the anchor instant, so an in-progress stay or
  an open incident surfaces as *live now* even though it was recorded long
  ago.

Only ``operations``-tier entries carry an operational axis; knowledge-graph
facts and customer events live on the history axis alone.  Classification
keys off the canonical store vocabularies (``BookingRecord.status`` and
``IncidentRecord.status`` in :mod:`brain_engine.memory.guest_history`), not
any new ad-hoc list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from brain_engine.memory.temporal_context import (
    OperationPhase,
    TemporalContext,
)

if TYPE_CHECKING:
    from brain_engine.memory.memory_timeline import (
        MemoryTimeline,
        TimelineEntry,
        TimelineScope,
    )

__all__ = [
    "build_temporal_context",
    "classify_operation",
]

# A booking in one of these statuses is void — it never counts as live or
# upcoming, only as history.  Sourced from ``BookingRecord.status``
# ("confirmed, completed, cancelled", guest_history.py); both the British
# and American spellings are tolerated against real-world data.
_VOID_BOOKING_STATUSES = frozenset({"cancelled", "canceled"})

# The single terminal incident status; any other open value keeps the
# incident live.  Sourced from ``IncidentRecord.status`` ("open,
# in_progress, resolved, escalated", guest_history.py).
_CLOSED_INCIDENT_STATUS = "resolved"


async def build_temporal_context(
    timeline: MemoryTimeline,
    scope: TimelineScope,
    *,
    as_of: datetime | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> TemporalContext:
    """Fuse a client's past timeline with their present operations.

    Args:
        timeline: The unified memory timeline to read the past from.
        scope: Who the context is for.
        as_of: The anchor instant — the reconstruction instant *and* the
            operational "now".  ``None`` means the current time, so a live
            call needs no clock plumbing; pass an explicit value to analyse
            the client as of a past moment.
        since: Drop history before this instant (passed to the timeline).
        limit: Keep at most this many of the most-recent history entries
            (passed to the timeline); ``live`` / ``upcoming`` are derived
            from whatever history survives that cap.

    Returns:
        A :class:`TemporalContext` with the full ``history`` plus the
        ``live`` and ``upcoming`` operational views derived from it.
    """
    anchor = as_of if as_of is not None else datetime.now(UTC)
    history = await timeline.read(
        scope,
        as_of=anchor,
        since=since,
        limit=limit,
    )

    live: list[TimelineEntry] = []
    upcoming: list[TimelineEntry] = []
    for entry in history:
        phase = classify_operation(entry, anchor)
        if phase is OperationPhase.LIVE:
            live.append(entry)
        elif phase is OperationPhase.UPCOMING:
            upcoming.append(entry)
    upcoming.sort(key=_operation_start)

    return TemporalContext(
        scope=scope,
        as_of=anchor,
        history=history,
        live=live,
        upcoming=upcoming,
    )


def classify_operation(
    entry: TimelineEntry,
    as_of: datetime,
) -> OperationPhase | None:
    """Classify a real operation by its position at ``as_of``.

    Returns ``None`` for anything without an operational axis — non-
    ``operations`` entries (knowledge-graph facts, customer events), void
    or unparseable bookings, or unknown operation kinds — meaning the entry
    belongs to history only and never to ``live`` / ``upcoming``.
    """
    if entry.tier != "operations":
        return None
    if entry.kind == "booking":
        return _classify_booking(entry, as_of)
    if entry.kind.startswith("incident:"):
        return _classify_incident(entry)
    return None


def _classify_booking(
    entry: TimelineEntry,
    as_of: datetime,
) -> OperationPhase | None:
    """Phase of a booking from its stay window, or ``None`` if void."""
    status = str(entry.payload.get("status", "")).strip().lower()
    if status in _VOID_BOOKING_STATUSES:
        return None
    start = _parse_iso(entry.payload.get("check_in"))
    end = _parse_iso(entry.payload.get("check_out"))
    if start is None or end is None:
        return None
    if as_of < start:
        return OperationPhase.UPCOMING
    if as_of > end:
        return OperationPhase.PAST
    return OperationPhase.LIVE


def _classify_incident(entry: TimelineEntry) -> OperationPhase:
    """An incident is live until resolved; it is never upcoming."""
    status = str(entry.payload.get("status", "")).strip().lower()
    if status == _CLOSED_INCIDENT_STATUS or entry.payload.get("resolved_at"):
        return OperationPhase.PAST
    return OperationPhase.LIVE


def _operation_start(entry: TimelineEntry) -> datetime:
    """Sort key for ``upcoming`` — the stay's start, else its record-at."""
    return _parse_iso(entry.payload.get("check_in")) or entry.at


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
