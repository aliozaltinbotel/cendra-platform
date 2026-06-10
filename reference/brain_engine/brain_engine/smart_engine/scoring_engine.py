"""ScoringEngine — Weighted scoring with time decay for cleaners, vendors, guests.

Central component of the Self-Learning Engine. Every interaction
(call result, vendor check, guest review, manager action) generates
an event that updates the scoring tables.

Formula: score = Σ (event_weight × 0.95^(weeks_since_event))
- Fresh events weigh more, old data fades
- ~14 weeks (3 months): event weighs ~50%
- ~28 weeks (6 months): event weighs ~25%

Scores are maintained at 3 levels:
- Global: overall reliability
- Per-city: how well they perform in a specific city
- Per-property: how well they perform at a specific property

Priority: property_score × 3 + city_score × 2 + global_score × 1
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from brain_engine.exceptions import BrainEngineError
from brain_engine.protocols import MemoryStore

logger = logging.getLogger(__name__)


class ScoringError(BrainEngineError):
    """Raised when scoring operations fail."""

DECAY_FACTOR = 0.95  # per week

# ── Event weights ────────────────────────────────────────────────────────

CLEANER_WEIGHTS: dict[str, int] = {
    "accepted_fast": 3,       # responded < 5 min
    "accepted_slow": 1,       # responded 5-15 min
    "rejected": -1,
    "no_answer": -2,
    "no_show": -5,
    "good_review": 2,         # guest happy with cleaning
    "bad_review": -3,
    "manager_preferred": 4,   # manager chose manually
    "on_time": 2,
    "late_arrival": -2,
    "quality_excellent": 3,
    "quality_poor": -4,
}

VENDOR_WEIGHTS: dict[str, int] = {
    "confirmed_same_day": 3,
    "fixed_issue_fast": 5,
    "confirmed_slow": 1,
    "missed_deadline": -3,
    "needed_followup": -1,
    "manager_preferred": 4,
    "quality_excellent": 3,
    "overcharged": -2,
}

GUEST_WEIGHTS: dict[str, int] = {
    "repeat_booking": 15,
    "five_star_review": 10,
    "no_incidents": 5,
    "clean_checkout": 3,
    "on_time_checkout": 3,
    "damage_reported": -20,
    "noise_complaint": -15,
    "late_checkout_unapproved": -10,
    "negative_review": -5,
    "excessive_cleaning": -3,
}


@dataclass(slots=True)
class ScoreEvent:
    """A single scoring event."""

    event_type: str
    weight: int
    timestamp: str
    property_id: str = ""
    city: str = ""
    response_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompositeScore:
    """Composite score with global, city, and property breakdowns."""

    entity_id: str
    entity_type: str  # "cleaner", "vendor", "guest"
    global_score: float = 0.0
    city_scores: dict[str, float] = field(default_factory=dict)
    property_scores: dict[str, float] = field(default_factory=dict)
    event_count: int = 0
    last_updated: str = ""

    def __repr__(self) -> str:
        return (
            f"CompositeScore({self.entity_type}:{self.entity_id}, "
            f"global={self.global_score:.1f}, composite={self.composite:.1f}, "
            f"events={self.event_count})"
        )

    @property
    def composite(self) -> float:
        """Weighted composite for ranking (used by CleaningCascade)."""
        best_property = max(self.property_scores.values()) if self.property_scores else 0
        best_city = max(self.city_scores.values()) if self.city_scores else 0
        return best_property * 3.0 + best_city * 2.0 + self.global_score * 1.0

    def for_property(self, property_id: str, city: str = "") -> float:
        """Score specific to a property/city combination."""
        p = self.property_scores.get(property_id, 0)
        c = self.city_scores.get(city, 0) if city else 0
        return p * 3.0 + c * 2.0 + self.global_score * 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "global_score": round(self.global_score, 2),
            "city_scores": {k: round(v, 2) for k, v in self.city_scores.items()},
            "property_scores": {k: round(v, 2) for k, v in self.property_scores.items()},
            "composite": round(self.composite, 2),
            "event_count": self.event_count,
            "last_updated": self.last_updated,
        }


class ScoringEngine:
    """Central scoring engine with weighted events and time decay.

    Stores events in Redis (episodic) and maintains computed scores
    for fast retrieval. Each entity (cleaner, vendor, guest) has
    global, per-city, and per-property scores.

    Args:
        redis_client: Async Redis client for persistence.
    """

    REDIS_PREFIX = "scoring:"

    def __init__(self, redis_client: MemoryStore | None = None) -> None:
        self._redis = redis_client
        # In-memory fallback when Redis unavailable
        self._events: dict[str, list[ScoreEvent]] = {}
        self._scores: dict[str, CompositeScore] = {}

    async def record_event(
        self,
        entity_id: str,
        entity_type: str,
        event_type: str,
        property_id: str = "",
        city: str = "",
        response_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompositeScore:
        """Record a scoring event and recalculate scores.

        Args:
            entity_id: Cleaner/vendor/guest ID.
            entity_type: "cleaner", "vendor", or "guest".
            event_type: Event type matching the weight tables.
            property_id: Property where the event happened.
            city: City where the event happened.
            response_time: Response time in seconds (if applicable).
            metadata: Additional event data.

        Returns:
            Updated CompositeScore for the entity.
        """
        weights = self._get_weights(entity_type)
        base_weight = weights.get(event_type, 0)

        # Bonus for fast response
        if response_time is not None and response_time < 300:
            base_weight += 1

        event = ScoreEvent(
            event_type=event_type,
            weight=base_weight,
            timestamp=datetime.now(timezone.utc).isoformat(),
            property_id=property_id,
            city=city,
            response_time=response_time,
            metadata=metadata or {},
        )

        key = f"{entity_type}:{entity_id}"
        if key not in self._events:
            self._events[key] = []
        self._events[key].append(event)

        # Persist to Redis
        await self._persist_event(key, event)

        # Recalculate score
        score = self._calculate_score(key, entity_id, entity_type)
        self._scores[key] = score

        await self._persist_score(key, score)

        logger.info(
            "Score updated: %s %s → %s: weight=%+d, global=%.1f",
            entity_type, entity_id, event_type, base_weight, score.global_score,
        )
        return score

    async def get_score(self, entity_id: str, entity_type: str) -> CompositeScore:
        """Get current composite score for an entity."""
        key = f"{entity_type}:{entity_id}"

        if key in self._scores:
            return self._scores[key]

        # Try loading from Redis
        score = await self._load_score(key, entity_id, entity_type)
        if score:
            self._scores[key] = score
            return score

        # No data — return zero score
        return CompositeScore(
            entity_id=entity_id,
            entity_type=entity_type,
        )

    async def get_ranked(
        self,
        entity_type: str,
        property_id: str = "",
        city: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get entities ranked by composite score.

        Priority: property_score × 3 + city_score × 2 + global_score × 1

        Args:
            entity_type: "cleaner" or "vendor".
            property_id: Prioritize scores for this property.
            city: Prioritize scores for this city.
            limit: Max results.

        Returns:
            List of dicts with entity_id, composite score, and breakdown.
        """
        results: list[dict[str, Any]] = []

        for key, score in self._scores.items():
            if not key.startswith(f"{entity_type}:"):
                continue

            composite = score.for_property(property_id, city)

            results.append({
                "entity_id": score.entity_id,
                "composite_score": round(composite, 2),
                "global_score": round(score.global_score, 2),
                "property_score": round(score.property_scores.get(property_id, 0), 2),
                "city_score": round(score.city_scores.get(city, 0), 2),
                "event_count": score.event_count,
            })

        results.sort(key=lambda x: x["composite_score"], reverse=True)
        return results[:limit]

    def _calculate_score(
        self,
        key: str,
        entity_id: str,
        entity_type: str,
    ) -> CompositeScore:
        """Recalculate score with time decay."""
        events = self._events.get(key, [])
        now = datetime.now(timezone.utc)

        global_score = 0.0
        city_scores: dict[str, float] = {}
        property_scores: dict[str, float] = {}

        for event in events:
            try:
                event_time = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue

            weeks_elapsed = max((now - event_time).total_seconds() / (7 * 86400), 0)
            decayed_weight = event.weight * (DECAY_FACTOR ** weeks_elapsed)

            global_score += decayed_weight

            if event.property_id:
                property_scores[event.property_id] = (
                    property_scores.get(event.property_id, 0) + decayed_weight
                )

            if event.city:
                city_scores[event.city] = (
                    city_scores.get(event.city, 0) + decayed_weight
                )

        return CompositeScore(
            entity_id=entity_id,
            entity_type=entity_type,
            global_score=global_score,
            city_scores=city_scores,
            property_scores=property_scores,
            event_count=len(events),
            last_updated=now.isoformat(),
        )

    @staticmethod
    def _get_weights(entity_type: str) -> dict[str, int]:
        """Get weight table for entity type."""
        match entity_type:
            case "cleaner":
                return CLEANER_WEIGHTS
            case "vendor":
                return VENDOR_WEIGHTS
            case "guest":
                return GUEST_WEIGHTS
            case _:
                return {}

    async def _persist_event(self, key: str, event: ScoreEvent) -> None:
        """Persist event to Redis."""
        if not self._redis:
            return
        try:
            redis_key = f"{self.REDIS_PREFIX}events:{key}"
            data = json.dumps({
                "event_type": event.event_type,
                "weight": event.weight,
                "timestamp": event.timestamp,
                "property_id": event.property_id,
                "city": event.city,
                "response_time": event.response_time,
                "metadata": event.metadata,
            })
            await self._redis.lpush(redis_key, data)
            # Keep last 200 events per entity
            await self._redis.ltrim(redis_key, 0, 199)
        except Exception:
            logger.exception("Failed to persist scoring event")

    async def _persist_score(self, key: str, score: CompositeScore) -> None:
        """Persist computed score to Redis."""
        if not self._redis:
            return
        try:
            redis_key = f"{self.REDIS_PREFIX}score:{key}"
            await self._redis.set(redis_key, json.dumps(score.to_dict()))
        except Exception:
            logger.exception("Failed to persist score")

    async def _load_score(
        self,
        key: str,
        entity_id: str,
        entity_type: str,
    ) -> CompositeScore | None:
        """Load score from Redis."""
        if not self._redis:
            return None
        try:
            redis_key = f"{self.REDIS_PREFIX}score:{key}"
            data = await self._redis.get(redis_key)
            if not data:
                return None
            d = json.loads(data)
            return CompositeScore(
                entity_id=entity_id,
                entity_type=entity_type,
                global_score=d.get("global_score", 0),
                city_scores=d.get("city_scores", {}),
                property_scores=d.get("property_scores", {}),
                event_count=d.get("event_count", 0),
                last_updated=d.get("last_updated", ""),
            )
        except Exception:
            logger.exception("Failed to load score from Redis")
            return None
