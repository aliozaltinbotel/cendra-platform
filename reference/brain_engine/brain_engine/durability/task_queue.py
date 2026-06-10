"""Task Queue — Redis-based async background task processing.

Replaces Cendra's Temporal Cloud for Brain Engine's own task execution.
Uses Redis sorted sets (ZADD/ZPOPMIN) for priority-ordered queuing
with JSON-serialized task payloads.

Architecture:
    Producer (API endpoint) → Redis Sorted Set → Consumer (WorkerPool)

    POST /booking/new
        ├→ enqueue("create_access_code", {...}, priority=1)  # urgent
        ├→ enqueue("send_welcome", {...}, priority=2)        # normal
        └→ enqueue("schedule_cleaning", {...}, priority=3)   # low

    WorkerPool picks up highest-priority tasks first.

Priority scoring:
    score = priority * 1e12 + unix_timestamp_ms
    Lower score = higher priority + earlier enqueue time.
    Priority 1 task always runs before priority 3, regardless of time.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class QueueRedisLike(Protocol):
    """Minimal Redis interface for priority task queue."""

    async def zadd(
        self, name: str, mapping: dict[str, float],
    ) -> int: ...

    async def zpopmin(
        self, name: str, count: int = 1,
    ) -> list[tuple[str, float]]: ...

    async def zcard(self, name: str) -> int: ...

    async def zrange(
        self, name: str, start: int, stop: int,
    ) -> list[str]: ...

    async def lpush(self, name: str, *values: str) -> int: ...

    async def llen(self, name: str) -> int: ...

    async def delete(self, *names: str) -> int: ...


@dataclass(frozen=True, slots=True)
class Task:
    """A background task to be processed by a worker.

    Attributes:
        task_id: Unique task identifier.
        task_type: Task type for routing to handler.
        payload: Task data (must be JSON-serializable).
        priority: Task priority (1=highest, 3=lowest).
        created_at: ISO timestamp of creation.
        source: What created this task (endpoint name).
        property_id: Property context (for routing).
        max_retries: Maximum retry attempts.
        attempt: Current attempt number.
    """

    task_id: str = ""
    task_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 1
    created_at: str = ""
    source: str = ""
    property_id: str = ""
    max_retries: int = 3
    attempt: int = 0


class TaskQueue:
    """Redis sorted set-backed priority task queue.

    Tasks are scored by priority * 1e12 + timestamp_ms.
    Lower score = dequeued first. Priority 1 always before priority 3.
    Within same priority, FIFO ordering by enqueue time.

    Args:
        redis: Async Redis client.
        queue_name: Redis key for the sorted set.
    """

    def __init__(
        self,
        redis: QueueRedisLike,
        queue_name: str = "brain_engine:tasks",
    ) -> None:
        self._redis = redis
        self._queue = queue_name
        self._dead_letter = f"{queue_name}:dead"

    async def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        priority: int = 1,
        source: str = "",
        property_id: str = "",
        max_retries: int = 3,
    ) -> Task:
        """Add a task to the priority queue.

        Args:
            task_type: Task type identifier.
            payload: Task data.
            priority: Task priority (1=highest, 3=lowest).
            source: What created this task.
            property_id: Property context.
            max_retries: Maximum retry attempts.

        Returns:
            Created Task with assigned ID.
        """
        task = _create_task(
            task_type, payload, priority, source, property_id, max_retries,
        )
        score = _priority_score(task.priority)
        serialized = _serialize_task(task)
        await self._redis.zadd(self._queue, {serialized: score})

        logger.info(
            "Enqueued task %s type=%s priority=%d",
            task.task_id, task_type, priority,
        )
        return task

    async def enqueue_batch(
        self,
        tasks: list[tuple[str, dict[str, Any]]],
        source: str = "",
        property_id: str = "",
    ) -> list[Task]:
        """Enqueue multiple tasks at once.

        Args:
            tasks: List of (task_type, payload) tuples.
            source: What created these tasks.
            property_id: Property context.

        Returns:
            List of created Tasks.
        """
        created: list[Task] = []
        for task_type, payload in tasks:
            task = await self.enqueue(
                task_type, payload, source=source, property_id=property_id,
            )
            created.append(task)
        return created

    async def dequeue(self) -> Task | None:
        """Pop highest-priority task from queue.

        Uses ZPOPMIN — atomically removes the task with lowest score
        (= highest priority + earliest time).

        Returns:
            Highest-priority Task, or None if queue is empty.
        """
        result = await self._redis.zpopmin(self._queue, count=1)
        if not result:
            return None

        raw, _score = result[0]
        return _deserialize_task(raw)

    async def requeue_or_dead_letter(self, task: Task) -> bool:
        """Retry a failed task or move to dead letter queue.

        If task has retries remaining, re-enqueues with incremented
        attempt and same priority. Otherwise moves to dead letter list.

        Args:
            task: Failed task to retry or discard.

        Returns:
            True if requeued, False if dead-lettered.
        """
        if task.attempt < task.max_retries:
            return await self._requeue(task)

        await self._dead_letter_task(task)
        return False

    async def size(self) -> int:
        """Get number of pending tasks in queue."""
        return await self._redis.zcard(self._queue)

    async def dead_letter_size(self) -> int:
        """Get number of dead-lettered tasks."""
        return await self._redis.llen(self._dead_letter)

    async def peek(self, count: int = 5) -> list[Task]:
        """Peek at next tasks in priority order without removing.

        Args:
            count: Number of tasks to peek at.

        Returns:
            List of upcoming tasks (highest priority first).
        """
        raw_list = await self._redis.zrange(self._queue, 0, count - 1)
        return [_deserialize_task(raw) for raw in raw_list]

    async def _requeue(self, task: Task) -> bool:
        """Re-enqueue task with incremented attempt counter.

        Args:
            task: Task to retry.

        Returns:
            True (always requeued successfully).
        """
        retried = Task(
            task_id=task.task_id,
            task_type=task.task_type,
            payload=task.payload,
            priority=task.priority,
            created_at=task.created_at,
            source=task.source,
            property_id=task.property_id,
            max_retries=task.max_retries,
            attempt=task.attempt + 1,
        )
        score = _priority_score(retried.priority)
        await self._redis.zadd(
            self._queue, {_serialize_task(retried): score},
        )
        logger.warning(
            "Requeued task %s attempt %d/%d",
            task.task_id, retried.attempt, task.max_retries,
        )
        return True

    async def _dead_letter_task(self, task: Task) -> None:
        """Move task to dead letter queue.

        Args:
            task: Task that exhausted all retries.
        """
        await self._redis.lpush(self._dead_letter, _serialize_task(task))
        logger.error(
            "Dead-lettered task %s after %d attempts",
            task.task_id, task.max_retries,
        )


# ── Scoring & Serialization ─────────────────────────────────────── #


def _priority_score(priority: int) -> float:
    """Calculate sorted set score from priority and current time.

    Score = priority * 1e12 + unix_timestamp_ms.
    Lower score = dequeued first.
    Priority 1 (score ~1e12) always before priority 3 (~3e12).

    Args:
        priority: Task priority (1=highest).

    Returns:
        Float score for Redis ZADD.
    """
    timestamp_ms = int(time.time() * 1000)
    return priority * 1e12 + timestamp_ms


def _create_task(
    task_type: str,
    payload: dict[str, Any],
    priority: int,
    source: str,
    property_id: str,
    max_retries: int,
) -> Task:
    """Create a new Task with generated ID and timestamp.

    Args:
        task_type: Task type identifier.
        payload: Task data.
        priority: Task priority.
        source: Creator source.
        property_id: Property context.
        max_retries: Max retry attempts.

    Returns:
        Initialized Task.
    """
    from brain_engine.durability.checkpointer import _now_iso

    return Task(
        task_id=str(uuid.uuid4()),
        task_type=task_type,
        payload=payload,
        priority=priority,
        created_at=_now_iso(),
        source=source,
        property_id=property_id,
        max_retries=max_retries,
    )


def _serialize_task(task: Task) -> str:
    """Serialize Task to JSON string for Redis storage.

    Args:
        task: Task to serialize.

    Returns:
        JSON string.
    """
    return json.dumps({
        "task_id": task.task_id,
        "task_type": task.task_type,
        "payload": task.payload,
        "priority": task.priority,
        "created_at": task.created_at,
        "source": task.source,
        "property_id": task.property_id,
        "max_retries": task.max_retries,
        "attempt": task.attempt,
    })


def _deserialize_task(raw: str) -> Task:
    """Deserialize JSON string to Task.

    Args:
        raw: JSON string from Redis.

    Returns:
        Reconstructed Task.
    """
    data = json.loads(raw)
    return Task(
        task_id=data.get("task_id", ""),
        task_type=data.get("task_type", ""),
        payload=data.get("payload", {}),
        priority=data.get("priority", 1),
        created_at=data.get("created_at", ""),
        source=data.get("source", ""),
        property_id=data.get("property_id", ""),
        max_retries=data.get("max_retries", 3),
        attempt=data.get("attempt", 0),
    )
