"""The fused past+present view of a client — the *temporal context*.

Phase 2 of the temporal substrate.  Phase 1 produced a single, as-of
queryable :class:`~brain_engine.memory.memory_timeline.MemoryTimeline`
ordered on the **record-time** axis (when each event entered history).
That axis alone cannot answer *"what is operationally live right now?"* —
a reservation recorded a month ago may be an in-progress stay today, and a
booking recorded long ago may still be a future arrival.

This module holds the value objects of the fusion that separates those two
temporal axes.  :class:`TemporalContext` keeps the full record-time
``history`` (the past recall) alongside two **operational-time** views:
``live`` (operations in progress at the anchor) and ``upcoming``
(operations scheduled to start after it).  ``live`` and ``upcoming`` are
classifications of operations *already present* in ``history`` (held by
reference, not duplicated) — so a consumer never double-counts.

The fusion logic that builds this object lives in
:mod:`brain_engine.memory.temporal_fusion`; it is deterministic and uses no
LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from brain_engine.memory.memory_timeline import (
        TimelineEntry,
        TimelineScope,
    )

__all__ = [
    "OperationPhase",
    "TemporalContext",
]


class OperationPhase(Enum):
    """Where a real operation sits relative to the anchor instant.

    Determined from the operation's own dates / status (its operational
    axis), independent of when it was recorded:

    * ``PAST`` — already finished (checked out, or incident resolved).
    * ``LIVE`` — in progress at the anchor (mid-stay, open incident).
    * ``UPCOMING`` — scheduled to start after the anchor (future arrival).
    """

    PAST = "past"
    LIVE = "live"
    UPCOMING = "upcoming"


@dataclass(frozen=True, slots=True)
class TemporalContext:
    """A client's past and present, fused at one anchor instant.

    Attributes:
        scope: Who the context is for (mirrors the timeline scope).
        as_of: The anchor instant (aware UTC).  Both the knowledge
            reconstruction instant and the operational "now" used to
            classify ``live`` / ``upcoming`` — so a context built for a
            past ``as_of`` describes what was live *then*.
        history: The complete as-of timeline on the record-time axis,
            oldest-first — the full past recall.
        live: Operations in progress at ``as_of`` (subset of ``history``
            by reference), oldest-first.
        upcoming: Operations scheduled to start after ``as_of`` (subset of
            ``history`` by reference), soonest-first.
    """

    scope: TimelineScope
    as_of: datetime
    history: list[TimelineEntry] = field(default_factory=list)
    live: list[TimelineEntry] = field(default_factory=list)
    upcoming: list[TimelineEntry] = field(default_factory=list)
