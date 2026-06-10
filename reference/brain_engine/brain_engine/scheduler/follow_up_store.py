"""Follow-Up Store — Redis-backed persistence for scheduled follow-ups.

Follow-ups are future checks that Brain Engine schedules in its responses.
The Ticker service polls this store and triggers Brain Engine when
a follow-up's time arrives.

Key structure:
    brain:followup:{follow_up_id}          -> Follow-up JSON
    brain:followup:schedule                -> Sorted set (score = trigger_at unix ts)
    brain:followup:process:{process_id}    -> Set of follow_up_ids for a process
    brain:followup:property:{property_id}  -> Set of follow_up_ids for a property
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class FollowUpStore:
    """Redis-backed store for scheduled follow-ups.

    Args:
        redis_url: Redis connection URL.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        workspace_id: str = "",
    ) -> None:
        import redis.asyncio as aioredis
        from brain_engine.memory.tenant import build_prefix
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = build_prefix("brain:followup:", workspace_id)

    def _key(self, *parts: str) -> str:
        """Build a Redis key.

        Args:
            parts: Key segments.

        Returns:
            Full Redis key.
        """
        return self._prefix + ":".join(parts)

    async def schedule(
        self,
        check_after_minutes: int,
        condition: str,
        condition_params: dict[str, Any],
        description: str = "",
        process_id: str = "",
        property_id: str = "",
    ) -> dict[str, Any]:
        """Schedule a new follow-up.

        Args:
            check_after_minutes: Minutes until the check should trigger.
            condition: Condition type to evaluate.
            condition_params: Parameters for evaluation.
            description: Human-readable description.
            process_id: Related active process.
            property_id: Related property.

        Returns:
            Created follow-up dict.
        """
        now = datetime.now(timezone.utc)
        trigger_at = now + timedelta(minutes=check_after_minutes)
        follow_up_id = f"fu_{uuid.uuid4().hex[:12]}"

        follow_up: dict[str, Any] = {
            "id": follow_up_id,
            "check_after_minutes": check_after_minutes,
            "condition": condition,
            "condition_params": condition_params,
            "description": description,
            "process_id": process_id,
            "property_id": property_id,
            "created_at": now.isoformat(),
            "trigger_at": trigger_at.isoformat(),
            "status": "pending",
        }

        pipe = self._redis.pipeline()
        pipe.set(self._key(follow_up_id), json.dumps(follow_up))
        pipe.zadd(
            self._key("schedule"),
            {follow_up_id: trigger_at.timestamp()},
        )
        if process_id:
            pipe.sadd(self._key("process", process_id), follow_up_id)
        if property_id:
            pipe.sadd(self._key("property", property_id), follow_up_id)
        await pipe.execute()

        logger.info(
            "Scheduled follow-up %s: %s in %d min",
            follow_up_id, condition, check_after_minutes,
        )
        return follow_up

    async def get_due(self) -> list[dict[str, Any]]:
        """Get all follow-ups whose trigger time has passed.

        Returns:
            List of due follow-up dicts.
        """
        now = datetime.now(timezone.utc).timestamp()
        due_ids = await self._redis.zrangebyscore(
            self._key("schedule"), "-inf", now,
        )

        due_followups: list[dict[str, Any]] = []
        for fid in due_ids:
            raw = await self._redis.get(self._key(fid))
            if raw:
                fu = json.loads(raw)
                if fu.get("status") == "pending":
                    due_followups.append(fu)

        return due_followups

    async def mark_triggered(self, follow_up_id: str) -> None:
        """Mark a follow-up as triggered.

        Args:
            follow_up_id: Follow-up to mark.
        """
        raw = await self._redis.get(self._key(follow_up_id))
        if raw:
            fu = json.loads(raw)
            fu["status"] = "triggered"
            fu["triggered_at"] = datetime.now(timezone.utc).isoformat()
            await self._redis.set(self._key(follow_up_id), json.dumps(fu))

        await self._redis.zrem(self._key("schedule"), follow_up_id)
        logger.info("Follow-up triggered: %s", follow_up_id)

    async def cancel_for_process(self, process_id: str) -> int:
        """Cancel all pending follow-ups for a process.

        Args:
            process_id: Process whose follow-ups to cancel.

        Returns:
            Number of follow-ups cancelled.
        """
        fu_ids = await self._redis.smembers(
            self._key("process", process_id),
        )
        cancelled = 0

        for fid in fu_ids:
            raw = await self._redis.get(self._key(fid))
            if raw:
                fu = json.loads(raw)
                if fu.get("status") == "pending":
                    fu["status"] = "cancelled"
                    await self._redis.set(self._key(fid), json.dumps(fu))
                    await self._redis.zrem(self._key("schedule"), fid)
                    cancelled += 1

        logger.info(
            "Cancelled %d follow-ups for process %s",
            cancelled, process_id,
        )
        return cancelled

    async def get(self, follow_up_id: str) -> dict[str, Any] | None:
        """Get a follow-up by ID.

        Args:
            follow_up_id: Follow-up identifier.

        Returns:
            Follow-up dict or None.
        """
        raw = await self._redis.get(self._key(follow_up_id))
        if raw:
            return json.loads(raw)
        return None

    async def get_for_process(
        self, process_id: str,
    ) -> list[dict[str, Any]]:
        """Get all follow-ups for a process.

        Args:
            process_id: Process identifier.

        Returns:
            List of follow-up dicts.
        """
        fu_ids = await self._redis.smembers(
            self._key("process", process_id),
        )
        results: list[dict[str, Any]] = []
        for fid in fu_ids:
            fu = await self.get(fid)
            if fu:
                results.append(fu)
        return results

    async def count_pending(self) -> int:
        """Count all pending follow-ups.

        Returns:
            Number of pending follow-ups.
        """
        return await self._redis.zcard(self._key("schedule"))

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.close()
