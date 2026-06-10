"""Surprise Detector — Titans-inspired surprise-based memorization.

Implements the core insight from Titans (Google Research, NeurIPS 2025):
"Surprising" events (those with high deviation from expectations) should be
memorized more strongly. This is inspired by cognitive neuroscience where
the brain prioritizes encoding of unexpected stimuli.

The surprise metric is computed as the divergence between:
- What the agent expected to happen (based on patterns/history)
- What actually happened

High-surprise events get:
1. Higher storage priority in episodic memory
2. Stronger reinforcement in the knowledge graph
3. Automatic promotion to long-term semantic memory
4. Alerts/flags for the cognitive controller

Also implements Ebbinghaus Forgetting Curve from MemoryBank:
- Memories decay over time based on an exponential function
- Access/reinforcement resets the decay timer
- Surprise level affects initial memory strength
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SurpriseScore:
    """Result of surprise analysis for an event."""

    event: str
    raw_score: float  # 0.0 (expected) to 1.0 (highly surprising)
    category: str  # "expected", "unusual", "surprising", "shocking"
    factors: list[str]  # What made it surprising
    should_memorize: bool  # Whether this warrants long-term storage
    memory_strength: float  # Initial memory strength (affected by surprise)
    detail: str = ""


# ── Surprise Rules ───────────────────────────────────────────────────── #

# Domain-specific surprise heuristics for Airbnb property management
_SURPRISE_RULES: dict[str, dict[str, Any]] = {
    # Guest behavior
    "repeat_damage": {
        "description": "Same guest caused damage again",
        "base_surprise": 0.3,  # Expected if tagged as damage-prone
        "first_time_surprise": 0.7,
    },
    "high_damage_severity": {
        "description": "Damage severity >= 4/5",
        "base_surprise": 0.8,
    },
    "damage_amount_exceeds": {
        "description": "Claim amount > $500",
        "threshold": 500,
        "base_surprise": 0.7,
    },
    "late_checkout_pattern": {
        "description": "Guest requested late checkout again",
        "base_surprise": 0.2,  # Low surprise if pattern exists
        "first_time_surprise": 0.4,
    },
    "no_damage_after_damage_guest": {
        "description": "Previously damage-prone guest left property clean",
        "base_surprise": 0.6,  # Positive surprise
    },
    "claim_denied_after_approval_pattern": {
        "description": "Claim denied when similar ones were approved",
        "base_surprise": 0.7,
    },
    "new_damage_location": {
        "description": "Damage in a location not previously reported for this property",
        "base_surprise": 0.5,
    },
    "unusually_fast_resolution": {
        "description": "Incident resolved much faster than average",
        "base_surprise": 0.4,
    },
    "cleaner_no_show": {
        "description": "Assigned cleaner did not arrive",
        "base_surprise": 0.8,
    },
    "guest_dispute": {
        "description": "Guest is disputing a charge",
        "base_surprise": 0.6,
    },
}


def _categorize_surprise(score: float) -> str:
    if score < 0.2:
        return "expected"
    if score < 0.5:
        return "unusual"
    if score < 0.8:
        return "surprising"
    return "shocking"


class SurpriseDetector:
    """Analyzes events for surprise level and determines memorization priority.

    Uses domain-specific heuristics combined with historical pattern analysis
    to score how "surprising" each event is. High-surprise events are
    prioritized for long-term memory storage.

    Args:
        redis_url: Redis URL for accessing historical patterns.
        surprise_threshold: Minimum surprise score to trigger long-term memorization.
        decay_rate: Ebbinghaus forgetting curve decay rate (higher = faster forgetting).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        surprise_threshold: float = 0.4,
        decay_rate: float = 0.1,
        workspace_id: str = "",
    ) -> None:
        import redis.asyncio as aioredis

        from core.brain.memory.tenant import build_prefix

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._surprise_threshold = surprise_threshold
        self._decay_rate = decay_rate
        self._prefix = build_prefix("brain:surprise:", workspace_id)

    def analyze_event(
        self,
        event: str,
        context: dict[str, Any] | None = None,
    ) -> SurpriseScore:
        """Analyze an event and compute its surprise score.

        Args:
            event: The event type (e.g., "damage_detected", "claim_denied").
            context: Event context with details for surprise analysis.

        Returns:
            SurpriseScore with the analysis result.
        """
        ctx = context or {}
        factors: list[str] = []
        scores: list[float] = []

        # Check against domain rules
        if event == "damage_detected":
            severity = ctx.get("severity", 1)
            amount = ctx.get("claim_amount", 0)
            guest_id = ctx.get("guest_id", "")
            property_id = ctx.get("property_id", "")

            # High severity damage
            if severity >= 4:
                scores.append(0.8)
                factors.append(f"High damage severity: {severity}/5")

            # Large claim amount
            if amount > 500:
                scores.append(0.7)
                factors.append(f"Large claim amount: ${amount}")

            # Check if guest has damage history
            if guest_id:
                prev_count = self._get_pattern_count(f"guest:{guest_id}:damage")
                if prev_count > 0:
                    scores.append(0.3)
                    factors.append(f"Repeat damage by guest (previous: {prev_count})")
                else:
                    scores.append(0.6)
                    factors.append("First-time damage by this guest")

            # Check if property has damage history in this area
            damage_location = ctx.get("damage_location", "")
            if damage_location and property_id:
                known = self._get_pattern_count(f"property:{property_id}:damage:{damage_location}")
                if known == 0:
                    scores.append(0.5)
                    factors.append(f"New damage location for property: {damage_location}")

        elif event == "no_damage":
            guest_id = ctx.get("guest_id", "")
            if guest_id:
                prev_damage = self._get_pattern_count(f"guest:{guest_id}:damage")
                if prev_damage > 0:
                    scores.append(0.6)
                    factors.append("Previously damage-prone guest left property clean")

        elif event == "claim_status_changed":
            new_status = ctx.get("new_status", "")
            if new_status == "denied":
                scores.append(0.7)
                factors.append("Claim was denied")
            elif new_status == "approved":
                scores.append(0.3)
                factors.append("Claim approved (expected outcome)")

        elif event == "late_checkout_requested":
            guest_id = ctx.get("guest_id", "")
            if guest_id:
                prev = self._get_pattern_count(f"guest:{guest_id}:late_checkout")
                if prev > 0:
                    scores.append(0.2)
                    factors.append(f"Guest has requested late checkout {prev} times before")
                else:
                    scores.append(0.4)
                    factors.append("First late checkout request by this guest")

        elif event == "cleaner_no_show":
            scores.append(0.8)
            factors.append("Cleaner failed to arrive")

        elif event == "guest_dispute":
            scores.append(0.6)
            factors.append("Guest is disputing a charge")

        elif event == "incident_escalated":
            scores.append(0.7)
            factors.append("Incident required escalation")

        # Default for unknown events
        if not scores:
            scores.append(0.3)
            factors.append(f"Standard event: {event}")

        # Combine scores (weighted average, max influence)
        raw_score = min(1.0, (sum(scores) / len(scores)) * 0.6 + max(scores) * 0.4)

        # Record pattern for future surprise analysis
        self._record_pattern(event, ctx)

        category = _categorize_surprise(raw_score)
        should_memorize = raw_score >= self._surprise_threshold

        # Memory strength: base + surprise bonus (Titans insight)
        memory_strength = 0.5 + (raw_score * 0.5)

        return SurpriseScore(
            event=event,
            raw_score=raw_score,
            category=category,
            factors=factors,
            should_memorize=should_memorize,
            memory_strength=memory_strength,
        )

    # ── Pattern Tracking ─────────────────────────────────────────────── #

    def _record_pattern(self, event: str, context: dict[str, Any]) -> None:
        """Record event occurrence for future surprise calculations."""
        guest_id = context.get("guest_id", "")
        property_id = context.get("property_id", "")

        pipe = self._redis.pipeline()

        # Track event frequency
        pipe.incr(self._prefix + f"freq:{event}")

        if guest_id:
            if event == "damage_detected":
                pipe.incr(self._prefix + f"guest:{guest_id}:damage")
            elif event == "late_checkout_requested":
                pipe.incr(self._prefix + f"guest:{guest_id}:late_checkout")
            elif event == "claim_submitted":
                pipe.incr(self._prefix + f"guest:{guest_id}:claims")

        if property_id:
            pipe.incr(self._prefix + f"property:{property_id}:{event}")
            damage_location = context.get("damage_location", "")
            if damage_location:
                pipe.incr(self._prefix + f"property:{property_id}:damage:{damage_location}")

        pipe.execute()

    def _get_pattern_count(self, pattern_key: str) -> int:
        """Get historical count for a pattern."""
        val = self._redis.get(self._prefix + pattern_key)
        return int(val) if val else 0

    # ── Ebbinghaus Forgetting Curve ──────────────────────────────────── #

    def compute_memory_retention(
        self,
        initial_strength: float,
        hours_since_last_access: float,
        reinforcement_count: int = 0,
    ) -> float:
        """Compute current memory retention using Ebbinghaus forgetting curve.

        R = S * e^(-λt / (1 + r))

        Where:
            R = retention level (0-1)
            S = initial strength (affected by surprise level)
            λ = decay rate
            t = time since last access (hours)
            r = reinforcement count (slows decay)

        Returns:
            Current retention level (0.0 = forgotten, 1.0 = fully retained).
        """
        effective_decay = self._decay_rate / (1 + reinforcement_count * 0.5)
        retention = initial_strength * math.exp(-effective_decay * hours_since_last_access)
        return max(0.0, min(1.0, retention))

    def should_consolidate(
        self,
        surprise_score: float,
        access_count: int,
        retention: float,
    ) -> bool:
        """Determine if a memory should be consolidated to long-term storage.

        High-surprise + frequently accessed + still retained = consolidate.
        """
        return surprise_score >= self._surprise_threshold and access_count >= 2 and retention >= 0.3

    def close(self) -> None:
        self._redis.close()
