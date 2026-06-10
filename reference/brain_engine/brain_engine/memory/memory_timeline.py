"""Unified, as-of-capable memory timeline (Phase 1, step 1b).

A single chronological view of everything that happened for one client,
merged from the heterogeneous memory tiers (knowledge graph, real
operations, customer events, …).  Each tier already stores time-stamped,
per-entity records; this module does **not** add storage — it normalises
those records into one :class:`TimelineEntry` stream, merges, time-windows,
and sorts them.

Design:

* **Decoupled core.** This module holds only the normalised value objects
  and the reader; it imports no memory tier.  Tiers are reached through the
  :class:`TimelineSource` Protocol, so the reader is unit-testable with fake
  sources and new tiers plug in without touching it.  The concrete adapters
  live in :mod:`brain_engine.memory.timeline_sources`.
* **As-of.** ``read(..., as_of=T)`` reconstructs the timeline as the system
  knew it at ``T``: the knowledge-graph source reconstructs each node's
  value at ``T`` (transaction-time, see :mod:`kg_as_of`), and the reader
  additionally drops every entry whose event time is after ``T`` — future
  events were not yet known.
* **Best-effort.** A source that raises is logged and skipped; one tier
  failing never empties the whole timeline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "MemoryTimeline",
    "TimelineEntry",
    "TimelineScope",
    "TimelineSource",
]


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TimelineScope:
    """Who the timeline is for.

    Tiers scope differently, so a single scope carries every identifier a
    source might key on; each adapter uses the ones it understands (the
    common case is a guest at a property — ``property_id`` + ``guest_id``,
    mirroring the live ``conv:{property_id}:{guest_id}`` conversation key).
    """

    property_id: str = ""
    guest_id: str = ""
    customer_id: str = ""
    workspace_id: str = ""


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One normalised event on the unified timeline.

    Attributes:
        at: When the event sits on the timeline (aware UTC).  For records
            with both a recorded-at and an operational date, ``at`` is the
            recorded-at (when it entered history); the operational date(s)
            live in ``payload``.
        tier: Which memory tier produced it (``kg`` / ``operations`` /
            ``memory`` / …).
        kind: Tier-specific event kind (``fact`` / ``booking`` /
            ``incident:damage`` / ``event:override`` …).
        entity_id: The entity the event is about (guest / property id).
        content: Human-readable one-line summary.
        source: The originating store (for provenance / debugging).
        confidence: Confidence in ``[0, 1]`` when the tier carries one
            (knowledge graph), else ``None``.
        payload: Tier-specific extra fields (dates, amounts, status …).
    """

    at: datetime
    tier: str
    kind: str
    entity_id: str
    content: str
    source: str
    confidence: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class TimelineSource(Protocol):
    """A memory tier rendered as timeline entries."""

    async def fetch(
        self,
        scope: TimelineScope,
        *,
        as_of: datetime | None,
    ) -> list[TimelineEntry]:
        """Return this tier's entries for ``scope``.

        ``as_of`` lets a bi-temporal source reconstruct its values as they
        stood at that instant; event-time-only sources may ignore it (the
        reader still upper-bounds the merged timeline by ``as_of``).
        """
        ...


class MemoryTimeline:
    """Merge several :class:`TimelineSource`s into one chronological view."""

    def __init__(self, sources: Sequence[TimelineSource]) -> None:
        self._sources = list(sources)

    async def read(
        self,
        scope: TimelineScope,
        *,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[TimelineEntry]:
        """Build the merged, windowed, chronologically-sorted timeline.

        Args:
            scope: Who the timeline is for.
            as_of: Reconstruct as the system knew it at ``T`` — sources
                reconstruct their values and entries after ``T`` are dropped.
            since: Drop entries before this instant.
            until: Drop entries after this instant.
            limit: Keep at most this many entries — the **most recent**
                ones — still returned oldest-first.  A truncation is logged
                so a capped read is never mistaken for full coverage.

        Returns:
            Entries sorted oldest-first.
        """

        gathered = await asyncio.gather(
            *(self._fetch_one(src, scope, as_of) for src in self._sources),
        )
        entries: list[TimelineEntry] = [e for batch in gathered for e in batch]

        upper = _earliest(until, as_of)
        entries = [
            e
            for e in entries
            if (since is None or e.at >= since)
            and (upper is None or e.at <= upper)
        ]
        entries.sort(key=lambda e: e.at)

        if limit is not None and len(entries) > limit:
            logger.info(
                "memory_timeline.truncated",
                kept=limit,
                dropped=len(entries) - limit,
            )
            entries = entries[-limit:]
        return entries

    async def _fetch_one(
        self,
        source: TimelineSource,
        scope: TimelineScope,
        as_of: datetime | None,
    ) -> list[TimelineEntry]:
        """Fetch one source, swallowing its failure into an empty batch."""
        try:
            return await source.fetch(scope, as_of=as_of)
        except Exception as exc:
            logger.warning(
                "memory_timeline.source_failed",
                source=type(source).__name__,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []


def _earliest(*values: datetime | None) -> datetime | None:
    """Return the earliest non-None datetime (the tightest upper bound)."""
    present = [v for v in values if v is not None]
    return min(present) if present else None
