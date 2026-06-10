"""Active Process Store — 7th memory tier for tracking ongoing processes.

Tracks what is currently happening: cleaning in progress, maintenance
awaiting vendor response, upsell offer pending, etc. Unlike episodic
memory (what happened), this stores what is happening NOW.

Key structure:
    brain:process:{process_id}             -> Process JSON
    brain:process:active                   -> Set of active process_ids
    brain:process:property:{property_id}   -> Set of process_ids for property
    brain:process:type:{type}              -> Set of process_ids by type
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ActiveProcessStore:
    """Redis-backed store for active operational processes.

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
        self._prefix = build_prefix("brain:process:", workspace_id)

    def _key(self, *parts: str) -> str:
        """Build a Redis key.

        Args:
            parts: Key segments.

        Returns:
            Full Redis key.
        """
        return self._prefix + ":".join(parts)

    async def create(
        self,
        process_type: str,
        property_id: str,
        reason: str = "",
        deadline: str = "",
        participants: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        related_booking: str = "",
    ) -> dict[str, Any]:
        """Create a new active process.

        Args:
            process_type: Type (cleaning, maintenance, sales, complaint).
            property_id: Related property.
            reason: What triggered this process.
            deadline: ISO deadline timestamp.
            participants: Process participants.
            context: Additional context data.
            related_booking: Related booking ID.

        Returns:
            Created process dict.
        """
        now = datetime.now(timezone.utc).isoformat()
        process_id = f"proc_{process_type}_{uuid.uuid4().hex[:8]}"

        process: dict[str, Any] = {
            "process_id": process_id,
            "type": process_type,
            "property_id": property_id,
            "status": "active",
            "started_at": now,
            "deadline": deadline,
            "reason": reason,
            "participants": participants or [],
            "history": [
                {
                    "time": now,
                    "event": "process_started",
                    "detail": reason or f"{process_type} started",
                },
            ],
            "pending_follow_ups": [],
            "related_booking": related_booking,
            "context": context or {},
            "completed_at": "",
        }

        pipe = self._redis.pipeline()
        pipe.set(self._key(process_id), json.dumps(process))
        pipe.sadd(self._key("active"), process_id)
        pipe.sadd(self._key("property", property_id), process_id)
        pipe.sadd(self._key("type", process_type), process_id)
        await pipe.execute()

        logger.info(
            "Created process: %s type=%s property=%s",
            process_id, process_type, property_id,
        )
        return process

    async def get(self, process_id: str) -> dict[str, Any] | None:
        """Get a process by ID.

        Args:
            process_id: Process identifier.

        Returns:
            Process dict or None.
        """
        raw = await self._redis.get(self._key(process_id))
        if raw:
            return json.loads(raw)
        return None

    async def get_active(
        self,
        property_id: str | None = None,
        process_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get active processes with optional filters.

        Args:
            property_id: Filter by property.
            process_type: Filter by process type.

        Returns:
            List of active process dicts.
        """
        if property_id:
            all_ids = await self._redis.smembers(
                self._key("property", property_id),
            )
        else:
            all_ids = await self._redis.smembers(self._key("active"))

        processes: list[dict[str, Any]] = []
        for pid in all_ids:
            proc = await self.get(pid)
            if not proc:
                continue
            if proc.get("status") != "active":
                continue
            if process_type and proc.get("type") != process_type:
                continue
            processes.append(proc)

        return sorted(
            processes,
            key=lambda p: p.get("started_at", ""),
            reverse=True,
        )

    async def update_status(
        self,
        process_id: str,
        status: str,
        detail: str = "",
    ) -> dict[str, Any] | None:
        """Update a process status and add history entry.

        Args:
            process_id: Process to update.
            status: New status.
            detail: History entry detail.

        Returns:
            Updated process or None.
        """
        proc = await self.get(process_id)
        if not proc:
            return None

        now = datetime.now(timezone.utc).isoformat()
        proc["status"] = status
        proc["history"].append({
            "time": now,
            "event": f"status_changed_to_{status}",
            "detail": detail or f"Status → {status}",
        })

        if status == "completed":
            proc["completed_at"] = now
            await self._redis.srem(self._key("active"), process_id)

        await self._redis.set(
            self._key(process_id), json.dumps(proc),
        )
        logger.info("Process %s → %s", process_id, status)
        return proc

    async def update_participant(
        self,
        process_id: str,
        contact_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update a participant within a process.

        Args:
            process_id: Process to update.
            contact_id: Participant to update.
            updates: Fields to merge into the participant.

        Returns:
            Updated process or None.
        """
        proc = await self.get(process_id)
        if not proc:
            return None

        for participant in proc.get("participants", []):
            if participant.get("contact_id") == contact_id:
                participant.update(updates)
                break

        now = datetime.now(timezone.utc).isoformat()
        proc["history"].append({
            "time": now,
            "event": "participant_updated",
            "detail": f"{contact_id}: {updates}",
        })

        await self._redis.set(
            self._key(process_id), json.dumps(proc),
        )
        return proc

    async def add_participant(
        self,
        process_id: str,
        contact_id: str,
        role: str,
        status: str = "waiting",
    ) -> dict[str, Any] | None:
        """Add a new participant to a process.

        Args:
            process_id: Process to update.
            contact_id: New participant ID.
            role: Participant role.
            status: Initial status.

        Returns:
            Updated process or None.
        """
        proc = await self.get(process_id)
        if not proc:
            return None

        now = datetime.now(timezone.utc).isoformat()
        proc["participants"].append({
            "contact_id": contact_id,
            "role": role,
            "status": status,
            "last_message": "",
            "last_message_at": "",
        })
        proc["history"].append({
            "time": now,
            "event": "participant_added",
            "detail": f"{contact_id} ({role}) joined",
        })

        await self._redis.set(
            self._key(process_id), json.dumps(proc),
        )
        return proc

    async def add_follow_up(
        self, process_id: str, follow_up_id: str,
    ) -> None:
        """Track a follow-up ID in a process.

        Args:
            process_id: Process to update.
            follow_up_id: Follow-up to track.
        """
        proc = await self.get(process_id)
        if proc:
            proc["pending_follow_ups"].append(follow_up_id)
            await self._redis.set(
                self._key(process_id), json.dumps(proc),
            )

    async def count_active(self) -> int:
        """Count all active processes.

        Returns:
            Number of active processes.
        """
        return len(await self._redis.smembers(self._key("active")))

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.close()
