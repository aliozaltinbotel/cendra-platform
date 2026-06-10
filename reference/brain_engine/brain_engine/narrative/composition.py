"""Timeline composer.

The composer is the read-side fan-out that turns N :class:`TimelineSource`
adapters into one deduplicated, time-sorted list of events for a given
property window.

Design choices:

- Source fetches run in parallel via ``asyncio.gather(...,
  return_exceptions=True)``.  A flaky source contributes an empty list
  instead of killing the whole composition — the narrative layer
  prefers *partial* answers over *no* answer.
- Dedupe runs on the stable :meth:`TimelineEvent.dedupe_key` so the
  same underlying row reaching us via two adapters (e.g. an ops
  decision surfaced both by ``decision_case`` and ``customer_memory``)
  only appears once.
- Sort is ascending by ``occurred_at`` so downstream renderers can
  simply iterate to produce chronological prose.
"""

from __future__ import annotations

import asyncio
from typing import Iterable, Sequence

import structlog

from brain_engine.narrative.models import EventKind, TimelineEvent, TimelineRange
from brain_engine.narrative.sources import TimelineSource

__all__ = ["TimelineComposer"]


logger = structlog.get_logger(__name__)


class TimelineComposer:
    """Parallel fetch + merge + dedupe + time-sort pipeline."""

    def __init__(
        self,
        sources: Sequence[TimelineSource],
        *,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._sources = tuple(sources)
        self._logger = logger or globals()["logger"]

    @property
    def sources(self) -> tuple[TimelineSource, ...]:
        return self._sources

    async def compose(
        self,
        *,
        property_id: str,
        range: TimelineRange,
        customer_id: str | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        per_source_limit: int = 200,
        include_ops: bool = True,
    ) -> tuple[TimelineEvent, ...]:
        """Run every configured source in parallel and return the merged list.

        Arguments:
            property_id: Property whose timeline is being composed.
            range: Window used to clip events.  Sources may pre-filter
                but final clipping happens here.
            customer_id: Optional owning customer, propagated to
                adapters that need it (currently only the customer
                memory source).
            reservation_id: Optional reservation scope — adapters will
                narrow their result to a single booking when provided.
            guest_id: Optional guest scope — adapters will narrow their
                result to a single guest when provided.
            per_source_limit: Max events requested from each source.
            include_ops: When ``False`` events with ``EventKind.OPS``
                are dropped from the merged result.
        """
        if not self._sources:
            return ()

        fetches = [
            source.fetch(
                property_id=property_id,
                range=range,
                customer_id=customer_id,
                reservation_id=reservation_id,
                guest_id=guest_id,
                limit=per_source_limit,
            )
            for source in self._sources
        ]
        results = await asyncio.gather(*fetches, return_exceptions=True)

        merged: list[TimelineEvent] = []
        for source, result in zip(self._sources, results, strict=True):
            if isinstance(result, BaseException):
                self._logger.warning(
                    "narrative.source_failed",
                    source=source.name,
                    error=str(result),
                    error_type=type(result).__name__,
                )
                continue
            merged.extend(self._clip(result, range))

        filtered = (
            merged if include_ops else [e for e in merged if e.kind != EventKind.OPS]
        )
        unique = _dedupe(filtered)
        unique.sort(key=lambda e: e.occurred_at)
        return tuple(unique)

    @staticmethod
    def _clip(
        events: Iterable[TimelineEvent],
        range: TimelineRange,
    ) -> list[TimelineEvent]:
        """Keep only events whose ``occurred_at`` falls inside ``range``."""
        return [event for event in events if range.contains(event.occurred_at)]


def _dedupe(events: Iterable[TimelineEvent]) -> list[TimelineEvent]:
    """Drop duplicates while preserving insertion order."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[TimelineEvent] = []
    for event in events:
        key = event.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique
