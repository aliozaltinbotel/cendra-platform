"""Abstract base class for checkpoint backends.

All checkpoint storage implementations (memory, SQLite, PostgreSQL)
must implement this interface. The ``CompiledGraph`` interacts with
checkpointers exclusively through these methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from brain_engine.checkpointer.models import (
    Checkpoint,
    CheckpointTuple,
    StateSnapshot,
)


class BaseCheckpointer(ABC):
    """Abstract interface for checkpoint persistence.

    Subclasses must implement ``put``, ``get``, ``get_tuple``, and
    ``list`` for full checkpoint lifecycle support.
    """

    @abstractmethod
    async def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        """Save a checkpoint.

        Args:
            config: Must contain ``thread_id``.
            checkpoint: Full graph state to persist.
            metadata: Additional metadata (source, step, next nodes).

        Returns:
            The checkpoint ID of the saved checkpoint.
        """

    @abstractmethod
    async def get(
        self,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Retrieve the latest checkpoint state for a thread.

        Args:
            config: Must contain ``thread_id``.

        Returns:
            The state dict, or ``None`` if no checkpoint exists.
        """

    @abstractmethod
    async def get_tuple(
        self,
        config: dict[str, Any],
    ) -> CheckpointTuple | None:
        """Retrieve the latest checkpoint with full metadata.

        Args:
            config: Must contain ``thread_id``.

        Returns:
            ``CheckpointTuple`` or ``None`` if no checkpoint exists.
        """

    @abstractmethod
    async def list(
        self,
        config: dict[str, Any],
        *,
        limit: int = 10,
        before: str | None = None,
    ) -> list[CheckpointTuple]:
        """List checkpoint history for a thread.

        Args:
            config: Must contain ``thread_id``.
            limit: Maximum number of checkpoints to return.
            before: Only return checkpoints created before this ID.

        Returns:
            List of ``CheckpointTuple`` in reverse chronological order.
        """

    # ── Convenience methods (built on abstract ones) ───────────────────

    async def get_state(
        self,
        config: dict[str, Any],
    ) -> StateSnapshot | None:
        """Get a public-facing state snapshot.

        Args:
            config: Must contain ``thread_id``.

        Returns:
            ``StateSnapshot`` or ``None``.
        """
        from brain_engine.checkpointer.models import checkpoint_to_snapshot

        tup = await self.get_tuple(config)
        if tup is None:
            return None
        return checkpoint_to_snapshot(
            tup.checkpoint, tup.config, tup.parent_config,
        )

    async def get_state_history(
        self,
        config: dict[str, Any],
        *,
        limit: int = 20,
    ) -> list[StateSnapshot]:
        """Get the checkpoint history as state snapshots.

        Args:
            config: Must contain ``thread_id``.
            limit: Maximum number of snapshots.

        Returns:
            List of ``StateSnapshot`` in reverse chronological order.
        """
        from brain_engine.checkpointer.models import checkpoint_to_snapshot

        tuples = await self.list(config, limit=limit)
        return [
            checkpoint_to_snapshot(t.checkpoint, t.config, t.parent_config)
            for t in tuples
        ]
