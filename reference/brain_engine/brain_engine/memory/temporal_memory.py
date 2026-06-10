"""Temporal Memory — Zep-like temporal memory with time-decay and temporal queries.

Extends episodic memory with:
- Time-decay: memories fade over time (Ebbinghaus forgetting curve)
- Temporal queries: "what happened last week?", "events before checkout"
- Access-based reinforcement: accessed memories get strengthened
- Automatic pruning of decayed memories
- Temporal clustering: groups related events by time proximity

Inspired by:
- Zep (temporal context engine for LLM apps)
- MemoryBank (Ebbinghaus forgetting curve for AI memory)
- Titans (surprise-based memorization strength)
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from brain_engine.streaming.emit_helpers import emit_memory_retrieved

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TemporalEvent:
    """A time-stamped event with decay properties.

    Attributes:
        event_id: Unique identifier.
        event_type: Type of event (e.g. guest_checkin, damage_detected).
        content: Human-readable event description.
        metadata: Additional structured data.
        timestamp: When the event occurred.
        initial_strength: Initial memory strength (higher = remembered longer).
        access_count: Number of times this memory has been accessed.
        last_accessed: Last access timestamp.
        importance: Importance score (0-1), affects decay rate.
        entity_ids: Related entity IDs (guests, properties, etc.).
        tags: Categorization tags.
    """

    event_id: str = ""
    event_type: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    initial_strength: float = 1.0
    access_count: int = 0
    last_accessed: str = ""
    importance: float = 0.5
    entity_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def current_strength(self, now: datetime | None = None) -> float:
        """Compute current memory strength with Ebbinghaus decay.

        Uses the forgetting curve formula:
            R = e^(-t/S)
        where:
            R = retention (0-1)
            t = time since last access (hours)
            S = stability (based on initial_strength, access_count, importance)

        Returns:
            Current strength (0-1). Below threshold = forgotten.
        """
        now = now or datetime.now(timezone.utc)
        last = self._parse_dt(self.last_accessed or self.timestamp)
        if not last:
            return self.initial_strength

        hours_elapsed = max((now - last).total_seconds() / 3600, 0)

        # Stability factor: strengthened by access and importance
        stability = (
            self.initial_strength * 24
            + self.access_count * 12
            + self.importance * 48
        )
        stability = max(stability, 1.0)

        retention = math.exp(-hours_elapsed / stability)
        return retention

    def reinforce(self) -> None:
        """Reinforce this memory (resets decay timer)."""
        self.access_count += 1
        self.last_accessed = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_dt(value: str) -> datetime | None:
        """Parse ISO datetime string."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None


@dataclass(slots=True)
class TemporalCluster:
    """A group of temporally related events.

    Attributes:
        cluster_id: Unique identifier.
        events: Events in this cluster.
        start_time: Earliest event timestamp.
        end_time: Latest event timestamp.
        summary: Auto-generated summary of the cluster.
        entity_ids: Union of all entity IDs in cluster.
    """

    cluster_id: str = ""
    events: list[TemporalEvent] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""
    summary: str = ""
    entity_ids: list[str] = field(default_factory=list)


class TemporalMemory:
    """Zep-inspired temporal memory with time-decay and temporal queries.

    Stores events with timestamps and computes memory strength using
    Ebbinghaus forgetting curve. Supports temporal queries like
    "events in last 24h" and "events related to guest X before checkout".

    Args:
        redis_client: Optional async Redis client for persistence.
        decay_threshold: Strength below which memories are considered forgotten.
        prune_interval_hours: How often to prune decayed memories.
    """

    REDIS_PREFIX = "temporal:"

    def __init__(
        self,
        redis_client: Any | None = None,
        decay_threshold: float = 0.1,
        prune_interval_hours: int = 24,
    ) -> None:
        self._redis = redis_client
        self._decay_threshold = decay_threshold
        self._prune_interval = prune_interval_hours
        self._events: dict[str, TemporalEvent] = {}
        self._last_prune: datetime | None = None

    async def add_event(
        self,
        event_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        initial_strength: float = 1.0,
        entity_ids: list[str] | None = None,
        tags: list[str] | None = None,
        timestamp: str | None = None,
    ) -> TemporalEvent:
        """Record a new temporal event.

        Args:
            event_type: Type of event.
            content: Human-readable description.
            metadata: Additional structured data.
            importance: How important (0-1). Affects decay rate.
            initial_strength: Initial memory strength.
            entity_ids: Related entity IDs.
            tags: Categorization tags.
            timestamp: Override timestamp (default = now).

        Returns:
            The created TemporalEvent.
        """
        now = timestamp or datetime.now(timezone.utc).isoformat()
        event = TemporalEvent(
            event_id=f"TE-{uuid.uuid4().hex[:10]}",
            event_type=event_type,
            content=content,
            metadata=metadata or {},
            timestamp=now,
            initial_strength=initial_strength,
            access_count=0,
            last_accessed=now,
            importance=importance,
            entity_ids=entity_ids or [],
            tags=tags or [],
        )

        self._events[event.event_id] = event
        await self._persist_event(event)

        # Auto-prune if due
        await self._maybe_prune()

        logger.debug("Temporal event recorded: %s (%s)", event.event_id, event_type)
        return event

    async def query_by_time(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        hours_back: int | None = None,
        include_decayed: bool = False,
    ) -> list[TemporalEvent]:
        """Query events within a time range.

        Args:
            start: Start of time range.
            end: End of time range (default = now).
            hours_back: Alternative: get events from last N hours.
            include_decayed: Whether to include memories below threshold.

        Returns:
            List of matching events, sorted by timestamp descending.
        """
        t0 = time.perf_counter()
        now = datetime.now(timezone.utc)
        end = end or now

        if hours_back is not None:
            start = now - timedelta(hours=hours_back)

        events = self._filter_events(
            start=start,
            end=end,
            include_decayed=include_decayed,
            now=now,
        )

        for event in events:
            event.reinforce()

        sorted_events = sorted(events, key=lambda e: e.timestamp, reverse=True)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        emit_memory_retrieved(
            tier="temporal",
            query=f"hours_back:{hours_back}" if hours_back is not None else "range",
            hits=[
                {
                    "id": getattr(e, "event_id", ""),
                    "score": float(e.current_strength(now)),
                    "excerpt": getattr(e, "content", "") or getattr(e, "event_type", ""),
                }
                for e in sorted_events
            ],
            latency_ms=latency_ms,
        )
        return sorted_events

    async def query_by_entity(
        self,
        entity_id: str,
        limit: int = 50,
        include_decayed: bool = False,
    ) -> list[TemporalEvent]:
        """Query events related to a specific entity.

        Args:
            entity_id: Entity ID to search for.
            limit: Max events to return.
            include_decayed: Include forgotten memories.

        Returns:
            List of matching events.
        """
        now = datetime.now(timezone.utc)
        results: list[TemporalEvent] = []

        for event in self._events.values():
            if entity_id not in event.entity_ids:
                continue
            if not include_decayed and event.current_strength(now) < self._decay_threshold:
                continue
            results.append(event)
            event.reinforce()

        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[:limit]

    async def query_by_type(
        self,
        event_type: str,
        limit: int = 50,
        hours_back: int | None = None,
    ) -> list[TemporalEvent]:
        """Query events of a specific type.

        Args:
            event_type: Event type to filter by.
            limit: Max events to return.
            hours_back: Optional time filter.

        Returns:
            List of matching events.
        """
        now = datetime.now(timezone.utc)
        start = None
        if hours_back is not None:
            start = now - timedelta(hours=hours_back)

        results: list[TemporalEvent] = []
        for event in self._events.values():
            if event.event_type != event_type:
                continue
            if start and event._parse_dt(event.timestamp):
                event_dt = event._parse_dt(event.timestamp)
                if event_dt and event_dt < start:
                    continue
            if event.current_strength(now) < self._decay_threshold:
                continue
            results.append(event)
            event.reinforce()

        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[:limit]

    async def get_temporal_context(
        self,
        entity_id: str | None = None,
        hours_back: int = 24,
        max_events: int = 20,
    ) -> str:
        """Build a temporal context string for LLM prompt injection.

        Args:
            entity_id: Optional entity to focus on.
            hours_back: How far back to look.
            max_events: Max events to include.

        Returns:
            Formatted string of recent events for the LLM context.
        """
        if entity_id:
            events = await self.query_by_entity(entity_id, limit=max_events)
        else:
            events = await self.query_by_time(hours_back=hours_back)

        events = events[:max_events]

        if not events:
            return "No recent events recorded."

        lines = ["Recent events (newest first):"]
        for event in events:
            strength = event.current_strength()
            strength_indicator = (
                "[vivid]" if strength > 0.7
                else "[fading]" if strength > 0.3
                else "[dim]"
            )
            lines.append(
                f"  {strength_indicator} [{event.timestamp[:16]}] "
                f"{event.event_type}: {event.content}"
            )

        return "\n".join(lines)

    async def cluster_events(
        self,
        events: list[TemporalEvent] | None = None,
        gap_hours: float = 2.0,
    ) -> list[TemporalCluster]:
        """Group events into temporal clusters based on time proximity.

        Args:
            events: Events to cluster (default = all active).
            gap_hours: Max hours between events in same cluster.

        Returns:
            List of TemporalCluster objects.
        """
        if events is None:
            events = await self.query_by_time(hours_back=168)  # Last week

        if not events:
            return []

        sorted_events = sorted(events, key=lambda e: e.timestamp)
        clusters: list[TemporalCluster] = []
        current_cluster_events: list[TemporalEvent] = [sorted_events[0]]

        for event in sorted_events[1:]:
            prev_ts = TemporalEvent._parse_dt(current_cluster_events[-1].timestamp)
            curr_ts = TemporalEvent._parse_dt(event.timestamp)

            if prev_ts and curr_ts:
                gap = (curr_ts - prev_ts).total_seconds() / 3600
                if gap <= gap_hours:
                    current_cluster_events.append(event)
                    continue

            # Save current cluster and start new one
            clusters.append(self._build_cluster(current_cluster_events))
            current_cluster_events = [event]

        # Don't forget the last cluster
        if current_cluster_events:
            clusters.append(self._build_cluster(current_cluster_events))

        return clusters

    async def get_memory_health(self) -> dict[str, Any]:
        """Get memory health statistics.

        Returns:
            Dict with event counts, decay stats, etc.
        """
        now = datetime.now(timezone.utc)
        total = len(self._events)
        active = sum(
            1 for e in self._events.values()
            if e.current_strength(now) >= self._decay_threshold
        )
        decayed = total - active

        strengths = [
            e.current_strength(now)
            for e in self._events.values()
        ]
        avg_strength = sum(strengths) / len(strengths) if strengths else 0.0

        return {
            "total_events": total,
            "active_events": active,
            "decayed_events": decayed,
            "average_strength": round(avg_strength, 3),
            "decay_threshold": self._decay_threshold,
        }

    def _filter_events(
        self,
        start: datetime | None,
        end: datetime | None,
        include_decayed: bool,
        now: datetime,
    ) -> list[TemporalEvent]:
        """Filter events by time range and decay status."""
        results: list[TemporalEvent] = []
        for event in self._events.values():
            if not include_decayed and event.current_strength(now) < self._decay_threshold:
                continue
            event_dt = TemporalEvent._parse_dt(event.timestamp)
            if not event_dt:
                continue
            if start and event_dt < start:
                continue
            if end and event_dt > end:
                continue
            results.append(event)
        return results

    @staticmethod
    def _build_cluster(events: list[TemporalEvent]) -> TemporalCluster:
        """Build a TemporalCluster from a list of events."""
        all_entity_ids: set[str] = set()
        for e in events:
            all_entity_ids.update(e.entity_ids)

        types_summary = ", ".join(sorted({e.event_type for e in events}))

        return TemporalCluster(
            cluster_id=f"TC-{uuid.uuid4().hex[:8]}",
            events=events,
            start_time=events[0].timestamp,
            end_time=events[-1].timestamp,
            summary=f"{len(events)} events: {types_summary}",
            entity_ids=list(all_entity_ids),
        )

    async def _maybe_prune(self) -> None:
        """Prune decayed events if enough time has passed."""
        now = datetime.now(timezone.utc)
        if self._last_prune and (now - self._last_prune).total_seconds() / 3600 < self._prune_interval:
            return

        pruned = 0
        to_remove: list[str] = []
        for event_id, event in self._events.items():
            if event.current_strength(now) < self._decay_threshold * 0.1:
                to_remove.append(event_id)

        for event_id in to_remove:
            del self._events[event_id]
            pruned += 1

        if pruned:
            logger.info("Pruned %d decayed temporal events", pruned)
        self._last_prune = now

    async def _persist_event(self, event: TemporalEvent) -> None:
        """Persist event to Redis (if available)."""
        if not self._redis:
            return
        try:
            import json
            key = f"{self.REDIS_PREFIX}{event.event_id}"
            data = {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "content": event.content,
                "metadata": event.metadata,
                "timestamp": event.timestamp,
                "initial_strength": event.initial_strength,
                "access_count": event.access_count,
                "last_accessed": event.last_accessed,
                "importance": event.importance,
                "entity_ids": event.entity_ids,
                "tags": event.tags,
            }
            await self._redis.set(key, json.dumps(data))
        except Exception:
            logger.exception("Failed to persist temporal event %s", event.event_id)

    async def load_from_redis(self) -> int:
        """Load all temporal events from Redis. Returns count loaded."""
        if not self._redis:
            return 0
        try:
            import json
            keys = await self._redis.keys(f"{self.REDIS_PREFIX}*")
            count = 0
            for key in keys:
                data = await self._redis.get(key)
                if data:
                    d = json.loads(data)
                    event = TemporalEvent(**d)
                    self._events[event.event_id] = event
                    count += 1
            logger.info("Loaded %d temporal events from Redis", count)
            return count
        except Exception:
            logger.exception("Failed to load temporal events from Redis")
            return 0
