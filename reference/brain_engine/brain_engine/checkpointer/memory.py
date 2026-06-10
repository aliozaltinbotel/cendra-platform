"""In-memory checkpoint backend for development and testing.

Stores checkpoints in a dict, keyed by ``(thread_id, checkpoint_id)``.
Fast and simple — no external dependencies. Data lost on process exit.
"""

from __future__ import annotations

import copy
from typing import Any

from brain_engine.checkpointer.base import BaseCheckpointer
from brain_engine.checkpointer.models import (
    Checkpoint,
    CheckpointTuple,
)


class MemoryCheckpointer(BaseCheckpointer):
    """Dict-based in-memory checkpoint store.

    Suitable for development, testing, and short-lived processes.
    Thread-safe within a single asyncio event loop (no locks needed).
    """

    def __init__(self) -> None:
        """Initialize MemoryCheckpointer."""
        self._store: dict[str, list[Checkpoint]] = {}

    async def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        """Save a checkpoint to memory.

        Args:
            config: Must contain ``thread_id``.
            checkpoint: Full graph state.
            metadata: Checkpoint metadata.

        Returns:
            The checkpoint ID.
        """
        thread_id = _get_thread_id(config)
        parent_id = self._get_latest_id(thread_id)

        cp = Checkpoint(
            thread_id=thread_id,
            values=copy.deepcopy(checkpoint),
            next=tuple(metadata.get("next", ())),
            metadata=copy.deepcopy(metadata),
            parent_id=parent_id,
        )

        self._store.setdefault(thread_id, []).append(cp)
        return cp.id

    async def get(
        self,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Retrieve the latest state for a thread.

        Args:
            config: Must contain ``thread_id``.

        Returns:
            State dict or ``None``.
        """
        thread_id = _get_thread_id(config)
        history = self._store.get(thread_id, [])
        if not history:
            return None
        return copy.deepcopy(history[-1].values)

    async def get_tuple(
        self,
        config: dict[str, Any],
    ) -> CheckpointTuple | None:
        """Retrieve the latest checkpoint tuple for a thread.

        Args:
            config: Must contain ``thread_id``.

        Returns:
            ``CheckpointTuple`` or ``None``.
        """
        thread_id = _get_thread_id(config)
        history = self._store.get(thread_id, [])
        if not history:
            return None

        cp = history[-1]
        parent_config = _parent_config(cp)
        return CheckpointTuple(
            checkpoint=cp,
            config=config,
            parent_config=parent_config,
        )

    async def list(
        self,
        config: dict[str, Any],
        *,
        limit: int = 10,
        before: str | None = None,
    ) -> list[CheckpointTuple]:
        """List checkpoint history in reverse chronological order.

        Args:
            config: Must contain ``thread_id``.
            limit: Maximum checkpoints to return.
            before: Only include checkpoints before this ID.

        Returns:
            List of ``CheckpointTuple``.
        """
        thread_id = _get_thread_id(config)
        history = self._store.get(thread_id, [])

        filtered = _apply_before_filter(history, before)
        selected = filtered[-limit:][::-1]

        return [
            CheckpointTuple(
                checkpoint=cp,
                config=config,
                parent_config=_parent_config(cp),
            )
            for cp in selected
        ]

    def _get_latest_id(self, thread_id: str) -> str | None:
        """Return the ID of the most recent checkpoint for a thread.

        Args:
            thread_id: Thread identifier.

        Returns:
            Latest checkpoint ID or ``None``.
        """
        history = self._store.get(thread_id, [])
        if not history:
            return None
        return history[-1].id

    @property
    def thread_count(self) -> int:
        """Return the number of threads with checkpoints."""
        return len(self._store)

    def checkpoint_count(self, thread_id: str) -> int:
        """Return the number of checkpoints for a thread.

        Args:
            thread_id: Thread identifier.

        Returns:
            Number of stored checkpoints.
        """
        return len(self._store.get(thread_id, []))

    def clear(self) -> None:
        """Remove all stored checkpoints."""
        self._store.clear()


def _get_thread_id(config: dict[str, Any]) -> str:
    """Extract thread_id from config.

    Args:
        config: Config dict.

    Returns:
        Thread ID string.

    Raises:
        ValueError: If ``thread_id`` is missing.
    """
    thread_id = config.get("thread_id")
    if not thread_id:
        raise ValueError("Config must contain 'thread_id'")
    return str(thread_id)


def _parent_config(cp: Checkpoint) -> dict[str, Any] | None:
    """Build a parent config from a checkpoint's parent_id.

    Args:
        cp: Checkpoint with optional ``parent_id``.

    Returns:
        Config dict or ``None``.
    """
    if cp.parent_id is None:
        return None
    return {"thread_id": cp.thread_id, "checkpoint_id": cp.parent_id}


def _apply_before_filter(
    history: list[Checkpoint],
    before: str | None,
) -> list[Checkpoint]:
    """Filter checkpoints created before a given checkpoint ID.

    Args:
        history: Ordered list of checkpoints.
        before: Checkpoint ID to filter before (or ``None``).

    Returns:
        Filtered list (or full list if ``before`` is None).
    """
    if before is None:
        return history

    for i, cp in enumerate(history):
        if cp.id == before:
            return history[:i]
    return history
