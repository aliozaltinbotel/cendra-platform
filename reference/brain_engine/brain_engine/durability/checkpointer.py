"""Pipeline Checkpointer — Redis-based durable execution for Brain Engine.

Inspired by LangGraph's checkpoint architecture:
- Saves pipeline state after each step
- Resumes from last successful step on failure
- Thread-safe via Redis atomic operations

Unlike LangGraph's channel-based graph checkpointing, this is designed
for Brain Engine's linear cognitive pipeline (classify → route → memory →
generate → validate → escalate → record).

Key differences from LangGraph:
- No channels/graph topology — simple step-by-step state
- Redis-native (not PostgreSQL) — matches our existing infrastructure
- Async-first — all operations are async
- Lightweight — ~200 lines vs LangGraph's ~3000 for checkpointing
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class RedisLike(Protocol):
    """Minimal Redis interface for checkpointing."""

    async def hset(self, name: str, mapping: dict[str, str]) -> int: ...
    async def hgetall(self, name: str) -> dict[str, str]: ...
    async def delete(self, *names: str) -> int: ...
    async def expire(self, name: str, seconds: int) -> bool: ...
    async def exists(self, *names: str) -> int: ...


@dataclass(frozen=True, slots=True)
class StepResult:
    """Result of a single pipeline step.

    Attributes:
        name: Step name (e.g., 'classify', 'generate').
        data: Step output data (must be JSON-serializable).
        duration_ms: Step execution time in milliseconds.
    """

    name: str
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


@dataclass(slots=True)
class PipelineState:
    """Current state of a pipeline execution.

    Attributes:
        pipeline_id: Unique execution identifier.
        thread_id: Conversation/request thread.
        current_step: Index of current step (0-based).
        total_steps: Total number of pipeline steps.
        status: Pipeline status.
        steps: Completed step results keyed by name.
        created_at: ISO timestamp of creation.
        updated_at: ISO timestamp of last update.
        metadata: Additional execution metadata.
    """

    pipeline_id: str = ""
    thread_id: str = ""
    current_step: int = 0
    total_steps: int = 0
    status: str = "pending"
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_resumable(self) -> bool:
        """Check if pipeline can be resumed from current step."""
        return self.status in ("in_progress", "interrupted")

    def completed_step_names(self) -> list[str]:
        """Return names of all completed steps."""
        return list(self.steps.keys())


class PipelineCheckpointer:
    """Redis-based pipeline state checkpointer.

    Saves and restores pipeline state for durable execution.
    Each step's output is checkpointed to Redis so the pipeline
    can resume from the last successful step after a crash.

    Args:
        redis: Async Redis client (or FakeRedis for tests).
        key_prefix: Redis key prefix for namespacing.
        ttl_seconds: Time-to-live for checkpoint data.
    """

    def __init__(
        self,
        redis: RedisLike,
        key_prefix: str = "checkpoint",
        ttl_seconds: int = 86400,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._ttl = ttl_seconds

    async def create(
        self,
        thread_id: str,
        total_steps: int,
        metadata: dict[str, Any] | None = None,
    ) -> PipelineState:
        """Create a new pipeline checkpoint.

        Args:
            thread_id: Conversation/request thread identifier.
            total_steps: Total number of steps in pipeline.
            metadata: Additional execution context.

        Returns:
            Initialized PipelineState.
        """
        state = PipelineState(
            pipeline_id=str(uuid.uuid4()),
            thread_id=thread_id,
            total_steps=total_steps,
            status="in_progress",
            created_at=_now_iso(),
            updated_at=_now_iso(),
            metadata=metadata or {},
        )
        await self._save(state)
        return state

    async def save_step(
        self,
        state: PipelineState,
        result: StepResult,
    ) -> PipelineState:
        """Save a completed step to checkpoint.

        Args:
            state: Current pipeline state.
            result: Completed step result.

        Returns:
            Updated PipelineState with step recorded.
        """
        state.steps[result.name] = {
            "data": result.data,
            "duration_ms": result.duration_ms,
        }
        state.current_step += 1
        state.updated_at = _now_iso()

        if state.current_step >= state.total_steps:
            state.status = "completed"

        await self._save(state)
        return state

    async def load(self, pipeline_id: str) -> PipelineState | None:
        """Load pipeline state from Redis.

        Args:
            pipeline_id: Pipeline execution identifier.

        Returns:
            PipelineState if found, None otherwise.
        """
        key = self._key(pipeline_id)
        raw = await self._redis.hgetall(key)
        if not raw:
            return None
        return _deserialize_state(raw)

    async def load_by_thread(self, thread_id: str) -> PipelineState | None:
        """Load latest pipeline state for a thread.

        Checks the thread index for the latest pipeline_id,
        then loads that pipeline's state.

        Args:
            thread_id: Conversation/request thread identifier.

        Returns:
            Latest PipelineState for thread, or None.
        """
        idx_key = f"{self._prefix}:thread:{thread_id}"
        raw = await self._redis.hgetall(idx_key)
        if not raw:
            return None
        pipeline_id = raw.get("pipeline_id", "")
        if not pipeline_id:
            return None
        return await self.load(pipeline_id)

    async def mark_failed(
        self,
        state: PipelineState,
        error: str,
    ) -> PipelineState:
        """Mark pipeline as failed at current step.

        Args:
            state: Current pipeline state.
            error: Error description.

        Returns:
            Updated PipelineState with failed status.
        """
        state.status = "failed"
        state.metadata["error"] = error
        state.metadata["failed_step"] = state.current_step
        state.updated_at = _now_iso()
        await self._save(state)
        return state

    async def mark_interrupted(
        self,
        state: PipelineState,
        reason: str,
    ) -> PipelineState:
        """Mark pipeline as interrupted (awaiting human input).

        Args:
            state: Current pipeline state.
            reason: Why the pipeline was interrupted.

        Returns:
            Updated PipelineState with interrupted status.
        """
        state.status = "interrupted"
        state.metadata["interrupt_reason"] = reason
        state.updated_at = _now_iso()
        await self._save(state)
        return state

    async def save(self, state: PipelineState) -> None:
        """Persist pipeline state to Redis (public API).

        Use this when updating state outside of step/mark methods.

        Args:
            state: Pipeline state to save.
        """
        await self._save(state)

    async def delete(self, pipeline_id: str) -> None:
        """Delete a pipeline checkpoint.

        Args:
            pipeline_id: Pipeline execution identifier.
        """
        await self._redis.delete(self._key(pipeline_id))

    def _key(self, pipeline_id: str) -> str:
        """Build Redis key for a pipeline.

        Args:
            pipeline_id: Pipeline execution identifier.

        Returns:
            Redis key string.
        """
        return f"{self._prefix}:pipeline:{pipeline_id}"

    async def _save(self, state: PipelineState) -> None:
        """Persist pipeline state to Redis.

        Args:
            state: Pipeline state to save.
        """
        key = self._key(state.pipeline_id)
        mapping = _serialize_state(state)
        await self._redis.hset(key, mapping=mapping)
        await self._redis.expire(key, self._ttl)

        idx_key = f"{self._prefix}:thread:{state.thread_id}"
        await self._redis.hset(idx_key, mapping={
            "pipeline_id": state.pipeline_id,
            "status": state.status,
            "updated_at": state.updated_at,
        })
        await self._redis.expire(idx_key, self._ttl)


# ── Serialization helpers ────────────────────────────────────────── #


def _serialize_state(state: PipelineState) -> dict[str, str]:
    """Serialize PipelineState to Redis HASH mapping.

    Args:
        state: Pipeline state to serialize.

    Returns:
        Dict of string key-value pairs for Redis HSET.
    """
    return {
        "pipeline_id": state.pipeline_id,
        "thread_id": state.thread_id,
        "current_step": str(state.current_step),
        "total_steps": str(state.total_steps),
        "status": state.status,
        "steps": json.dumps(state.steps),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "metadata": json.dumps(state.metadata),
    }


def _deserialize_state(raw: dict[str, str]) -> PipelineState:
    """Deserialize Redis HASH mapping to PipelineState.

    Args:
        raw: Raw Redis HGETALL result.

    Returns:
        Reconstructed PipelineState.
    """
    return PipelineState(
        pipeline_id=raw.get("pipeline_id", ""),
        thread_id=raw.get("thread_id", ""),
        current_step=int(raw.get("current_step", "0")),
        total_steps=int(raw.get("total_steps", "0")),
        status=raw.get("status", "pending"),
        steps=json.loads(raw.get("steps", "{}")),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
        metadata=json.loads(raw.get("metadata", "{}")),
    )


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
