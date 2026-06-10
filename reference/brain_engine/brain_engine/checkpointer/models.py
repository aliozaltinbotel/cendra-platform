"""Data models for the checkpoint persistence system.

Defines the canonical ``Checkpoint``, ``CheckpointTuple``, and
``StateSnapshot`` types used across all checkpoint backends.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Generate a new checkpoint UUID."""
    return str(uuid.uuid4())


@dataclass(slots=True)
class Checkpoint:
    """A single checkpoint snapshot of graph state.

    Attributes:
        id: Unique checkpoint identifier (UUID).
        thread_id: Thread this checkpoint belongs to.
        values: Full graph state at this point.
        next: Tuple of node names scheduled to run next.
        metadata: Additional metadata (source, step, writes).
        created_at: UTC timestamp of creation.
        parent_id: ID of the previous checkpoint (for history chain).
    """

    id: str = field(default_factory=_new_id)
    thread_id: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    next: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class CheckpointTuple:
    """A checkpoint bundled with its config for retrieval.

    Attributes:
        checkpoint: The checkpoint data.
        config: The config used to store it (contains thread_id).
        parent_config: Config of the parent checkpoint (if any).
    """

    checkpoint: Checkpoint
    config: dict[str, Any]
    parent_config: dict[str, Any] | None = None


@dataclass(slots=True)
class StateSnapshot:
    """Public-facing view of a checkpoint for the API layer.

    Simplified version of ``CheckpointTuple`` for ``get_state`` calls.

    Attributes:
        values: Full graph state.
        next: Nodes scheduled to run next.
        config: Config that produced this snapshot.
        metadata: Checkpoint metadata.
        parent_config: Config of the parent checkpoint.
        checkpoint_id: Unique ID of this checkpoint.
        created_at: UTC timestamp.
    """

    values: dict[str, Any] = field(default_factory=dict)
    next: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_config: dict[str, Any] | None = None
    checkpoint_id: str = ""
    created_at: datetime = field(default_factory=_utcnow)


def checkpoint_to_snapshot(
    cp: Checkpoint,
    config: dict[str, Any],
    parent_config: dict[str, Any] | None = None,
) -> StateSnapshot:
    """Convert a Checkpoint to a public StateSnapshot.

    Args:
        cp: The internal checkpoint.
        config: Config used to retrieve the checkpoint.
        parent_config: Parent checkpoint config (if any).

    Returns:
        Public ``StateSnapshot`` view.
    """
    return StateSnapshot(
        values=cp.values,
        next=cp.next,
        config=config,
        metadata=cp.metadata,
        parent_config=parent_config,
        checkpoint_id=cp.id,
        created_at=cp.created_at,
    )
