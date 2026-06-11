"""Interaction Recorder — MetaClaw-inspired data pipeline.

Based on: MetaClaw (arXiv:2603.17187).
Key: Version separation (support vs query data) prevents feedback loops.

Records every Brain Engine interaction for skill evolution:
    input (event + context) -> output (decision + actions) -> result (graded)

Storage strategy:
    - Each interaction stored as JSON at key brain:ix:{id}
    - Redis sorted set brain:ix:timeline scored by Unix timestamp
      enables O(log N) range queries instead of scanning all IDs
    - Secondary sorted sets per event_type for targeted lookups
    - Separate sorted sets for failures and approvals for nightly queries
    - 90-day TTL on individual records; sorted set entries cleaned in batch
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_TTL_SECONDS = 90 * 86400  # 90 days
_BATCH_SIZE = 100  # max IDs to fetch per ZRANGEBYSCORE page
_PREFIX = "brain:ix:"


@dataclass
class BrainEngineInteraction:
    """A single Brain Engine interaction record.

    Attributes:
        interaction_id: Unique identifier.
        timestamp: ISO-format UTC timestamp.
        event_type: Type of triggering event.
        input_message: Input message or event description.
        context: Full request context.
        output_response: Generated response text.
        output_actions: Actions decided by the engine.
        confidence: Decision confidence score.
        cognitive_level: L1-L4 cognitive depth used.
        reasoning_trace: Reasoning audit trail.
        grader_score: Quality score from APMGrader (filled later).
        owner_approved: Whether owner approved (filled later).
        guest_satisfied: Guest satisfaction ('positive'/'neutral'/'negative').
        owner_intervened: Whether owner manually intervened.
        resolved_without_escalation: Self-resolved without human help.
        response_time_minutes: Time from event to resolution.
        cascade_level: Which cascade level resolved it (1 = first try).
        property_id: Property identifier for grouping.
        owner_id: Owner identifier for grouping.
        data_version: Data version for MetaClaw separation.
        data_type: 'support' (before adaptation) or 'query' (after).
    """

    interaction_id: str = ""
    timestamp: str = ""
    event_type: str = ""
    input_message: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    output_response: str = ""
    output_actions: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.5
    cognitive_level: str = "L1"
    reasoning_trace: str = ""
    grader_score: float | None = None
    owner_approved: bool | None = None
    guest_satisfied: str | None = None
    owner_intervened: bool = False
    resolved_without_escalation: bool = False
    response_time_minutes: float = 0.0
    cascade_level: int = 1
    property_id: str = ""
    owner_id: str = ""
    data_version: str = "v1"
    data_type: str = "support"

    def __post_init__(self) -> None:
        """Auto-generate ID and timestamp if missing."""
        if not self.interaction_id:
            self.interaction_id = str(uuid.uuid4())[:12]
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    @property
    def unix_ts(self) -> float:
        """Return timestamp as Unix epoch float for sorted set scoring."""
        try:
            return datetime.fromisoformat(self.timestamp).timestamp()
        except (ValueError, TypeError):
            return datetime.now(UTC).timestamp()

    @property
    def is_failure(self) -> bool:
        """Check if this interaction is considered a failure."""
        if self.owner_intervened:
            return True
        if self.guest_satisfied == "negative":
            return True
        if self.grader_score is not None and self.grader_score < 0.4:
            return True
        return False


class InteractionRecorder:
    """Redis-backed recorder for all Brain Engine interactions.

    Uses Redis sorted sets for efficient time-range queries:
        brain:ix:timeline          — all interactions scored by timestamp
        brain:ix:by_type:{type}    — per event_type
        brain:ix:failures          — interactions marked as failures
        brain:ix:approvals         — interactions with approval decisions
        brain:ix:by_prop:{id}      — per property
        brain:ix:{interaction_id}  — the JSON payload

    Args:
        redis_client: Async Redis client instance.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._current_version = "v1"

    # ── Recording ───────────────────────────────────────────────────── #

    def record(self, interaction: BrainEngineInteraction) -> str:
        """Store an interaction and add to all relevant indices.

        Args:
            interaction: The interaction to record.

        Returns:
            The interaction ID.
        """
        interaction.data_version = self._current_version
        score = interaction.unix_ts
        iid = interaction.interaction_id
        data = json.dumps(asdict(interaction), default=str)

        pipe = self._redis.pipeline()
        pipe.set(f"{_PREFIX}{iid}", data, ex=_TTL_SECONDS)
        pipe.zadd(f"{_PREFIX}timeline", {iid: score})
        pipe.zadd(f"{_PREFIX}by_type:{interaction.event_type}", {iid: score})

        if interaction.property_id:
            pipe.zadd(f"{_PREFIX}by_prop:{interaction.property_id}", {iid: score})

        pipe.execute()

        logger.debug("Recorded: %s type=%s", iid, interaction.event_type)
        return iid

    # ── Outcome Updates ─────────────────────────────────────────────── #

    def update_outcome(
        self,
        interaction_id: str,
        grader_score: float | None = None,
        owner_approved: bool | None = None,
        guest_satisfied: str | None = None,
        owner_intervened: bool | None = None,
        resolved_without_escalation: bool | None = None,
        response_time_minutes: float | None = None,
        cascade_level: int | None = None,
    ) -> None:
        """Update an interaction with outcome data and refresh indices.

        Args:
            interaction_id: ID of the interaction to update.
            grader_score: Quality score from APMGrader.
            owner_approved: Whether owner approved the action.
            guest_satisfied: Guest satisfaction signal.
            owner_intervened: Whether owner manually intervened.
            resolved_without_escalation: Self-resolved flag.
            response_time_minutes: Minutes from event to resolution.
            cascade_level: Which cascade level resolved it.
        """
        interaction = self._get(interaction_id)
        if not interaction:
            logger.warning("Interaction %s not found for update", interaction_id)
            return

        self._apply_outcome_fields(
            interaction,
            grader_score,
            owner_approved,
            guest_satisfied,
            owner_intervened,
            resolved_without_escalation,
            response_time_minutes,
            cascade_level,
        )

        self._save_and_reindex(interaction)

    @staticmethod
    def _apply_outcome_fields(
        interaction: BrainEngineInteraction,
        grader_score: float | None,
        owner_approved: bool | None,
        guest_satisfied: str | None,
        owner_intervened: bool | None,
        resolved_without_escalation: bool | None,
        response_time_minutes: float | None,
        cascade_level: int | None,
    ) -> None:
        """Apply non-None outcome fields to the interaction.

        Args:
            interaction: The interaction to update.
            grader_score: Quality score.
            owner_approved: Approval flag.
            guest_satisfied: Satisfaction signal.
            owner_intervened: Override flag.
            resolved_without_escalation: Self-resolved flag.
            response_time_minutes: Resolution time.
            cascade_level: Cascade level.
        """
        if grader_score is not None:
            interaction.grader_score = grader_score
        if owner_approved is not None:
            interaction.owner_approved = owner_approved
        if guest_satisfied is not None:
            interaction.guest_satisfied = guest_satisfied
        if owner_intervened is not None:
            interaction.owner_intervened = owner_intervened
        if resolved_without_escalation is not None:
            interaction.resolved_without_escalation = resolved_without_escalation
        if response_time_minutes is not None:
            interaction.response_time_minutes = response_time_minutes
        if cascade_level is not None:
            interaction.cascade_level = cascade_level

    def _save_and_reindex(
        self,
        interaction: BrainEngineInteraction,
    ) -> None:
        """Save updated interaction and refresh failure/approval indices.

        Args:
            interaction: The interaction to save.
        """
        iid = interaction.interaction_id
        score = interaction.unix_ts
        data = json.dumps(asdict(interaction), default=str)

        pipe = self._redis.pipeline()
        pipe.set(f"{_PREFIX}{iid}", data, ex=_TTL_SECONDS)

        if interaction.is_failure:
            pipe.zadd(f"{_PREFIX}failures", {iid: score})
        else:
            pipe.zrem(f"{_PREFIX}failures", iid)

        if interaction.owner_approved is not None:
            pipe.zadd(f"{_PREFIX}approvals", {iid: score})

        pipe.execute()

    # ── Queries ─────────────────────────────────────────────────────── #

    def get_failures(
        self,
        since: datetime,
    ) -> list[BrainEngineInteraction]:
        """Get failed interactions since a given time. O(log N + K).

        Args:
            since: Start datetime for the query window.

        Returns:
            List of failed interactions sorted by time.
        """
        return self._get_from_sorted_set(
            f"{_PREFIX}failures",
            since,
        )

    def get_approvals(
        self,
        since: datetime,
    ) -> list[BrainEngineInteraction]:
        """Get interactions with owner approvals since a given time.

        Args:
            since: Start datetime for the query window.

        Returns:
            List of interactions with approval decisions.
        """
        return self._get_from_sorted_set(
            f"{_PREFIX}approvals",
            since,
        )

    def get_graded(
        self,
        days: int = 30,
    ) -> list[BrainEngineInteraction]:
        """Get graded interactions from the last N days.

        Args:
            days: Number of days to look back.

        Returns:
            List of interactions that have grader scores.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        all_recent = self._get_from_sorted_set(
            f"{_PREFIX}timeline",
            since,
        )
        return [i for i in all_recent if i.grader_score is not None]

    def get_by_property(
        self,
        property_id: str,
        days: int = 30,
    ) -> list[BrainEngineInteraction]:
        """Get interactions for a specific property.

        Args:
            property_id: Property identifier.
            days: Lookback period.

        Returns:
            List of interactions for this property.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        return self._get_from_sorted_set(
            f"{_PREFIX}by_prop:{property_id}",
            since,
        )

    def get_by_event_type(
        self,
        event_type: str,
        days: int = 30,
    ) -> list[BrainEngineInteraction]:
        """Get interactions for a specific event type.

        Args:
            event_type: Event type string.
            days: Lookback period.

        Returns:
            List of matching interactions.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        return self._get_from_sorted_set(
            f"{_PREFIX}by_type:{event_type}",
            since,
        )

    def count(self, days: int = 30) -> int:
        """Count interactions in the last N days. O(log N).

        Args:
            days: Number of days to look back.

        Returns:
            Number of interactions.
        """
        since_ts = (datetime.now(UTC) - timedelta(days=days)).timestamp()
        now_ts = datetime.now(UTC).timestamp()
        return self._redis.zcount(
            f"{_PREFIX}timeline",
            since_ts,
            now_ts,
        )

    def count_failures(self, days: int = 30) -> int:
        """Count failed interactions in the last N days.

        Args:
            days: Lookback period.

        Returns:
            Number of failures.
        """
        since_ts = (datetime.now(UTC) - timedelta(days=days)).timestamp()
        now_ts = datetime.now(UTC).timestamp()
        return self._redis.zcount(
            f"{_PREFIX}failures",
            since_ts,
            now_ts,
        )

    # ── Cleanup ─────────────────────────────────────────────────────── #

    def cleanup_expired(self, max_age_days: int = 90) -> int:
        """Remove entries older than max_age_days from sorted sets.

        The JSON keys auto-expire via TTL, but sorted set entries
        need manual cleanup.

        Args:
            max_age_days: Maximum age in days.

        Returns:
            Number of entries removed.
        """
        cutoff_ts = (datetime.now(UTC) - timedelta(days=max_age_days)).timestamp()

        sets_to_clean = self._get_sorted_set_keys()
        total_removed = 0

        for key in sets_to_clean:
            removed = self._redis.zremrangebyscore(
                key,
                "-inf",
                cutoff_ts,
            )
            total_removed += removed

        if total_removed > 0:
            logger.info("Cleaned %d expired entries from indices", total_removed)
        return total_removed

    def _get_sorted_set_keys(self) -> list[str]:
        """Get all sorted set keys used by the recorder.

        Returns:
            List of Redis sorted set keys.
        """
        keys: list[str] = [
            f"{_PREFIX}timeline",
            f"{_PREFIX}failures",
            f"{_PREFIX}approvals",
        ]
        # Scan for by_type: and by_prop: keys
        cursor = "0"
        while True:
            cursor, found = self._redis.scan(
                cursor=cursor,
                match=f"{_PREFIX}by_*",
                count=100,
            )
            keys.extend(found)
            if cursor in (0, "0"):
                break
        return keys

    # ── Internal helpers ────────────────────────────────────────────── #

    def _get(
        self,
        interaction_id: str,
    ) -> BrainEngineInteraction | None:
        """Fetch a single interaction by ID.

        Args:
            interaction_id: The interaction ID.

        Returns:
            The interaction or None if not found.
        """
        raw = self._redis.get(f"{_PREFIX}{interaction_id}")
        if not raw:
            return None
        return _deserialize(raw)

    def _get_from_sorted_set(
        self,
        key: str,
        since: datetime,
    ) -> list[BrainEngineInteraction]:
        """Fetch interactions from a sorted set by time range.

        Uses ZRANGEBYSCORE for O(log N + K) performance where K is
        the number of results, instead of scanning all IDs.

        Args:
            key: Redis sorted set key.
            since: Start datetime.

        Returns:
            List of interactions sorted by timestamp.
        """
        since_ts = since.timestamp()
        now_ts = datetime.now(UTC).timestamp()

        ids = self._redis.zrangebyscore(
            key,
            since_ts,
            now_ts,
        )

        if not ids:
            return []

        return self._batch_get(ids)

    def _batch_get(
        self,
        ids: list[str],
    ) -> list[BrainEngineInteraction]:
        """Fetch multiple interactions by ID using pipeline.

        Args:
            ids: List of interaction IDs.

        Returns:
            List of valid interactions (skips missing).
        """
        if not ids:
            return []

        pipe = self._redis.pipeline()
        for iid in ids:
            pipe.get(f"{_PREFIX}{iid}")
        raw_values = pipe.execute()

        results: list[BrainEngineInteraction] = []
        for raw in raw_values:
            if raw is None:
                continue
            interaction = _deserialize(raw)
            if interaction:
                results.append(interaction)

        return sorted(results, key=lambda i: i.timestamp)


def _deserialize(raw: str) -> BrainEngineInteraction | None:
    """Deserialize JSON string to BrainEngineInteraction.

    Args:
        raw: JSON string from Redis.

    Returns:
        BrainEngineInteraction or None if parsing fails.
    """
    try:
        data = json.loads(raw)
        return BrainEngineInteraction(
            **{k: v for k, v in data.items() if k in BrainEngineInteraction.__dataclass_fields__}
        )
    except (json.JSONDecodeError, TypeError):
        logger.error("Failed to deserialize interaction", exc_info=True)
        return None
