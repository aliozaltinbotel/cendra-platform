"""PostgreSQL checkpoint backend for distributed persistence.

Uses ``asyncpg`` for high-performance async access. Stores checkpoints
in a single table with JSONB columns for efficient querying.
Supports connection pooling for production deployments.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg

from brain_engine.checkpointer.base import BaseCheckpointer
from brain_engine.checkpointer.models import (
    Checkpoint,
    CheckpointTuple,
)

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    values_jsonb JSONB NOT NULL DEFAULT '{}',
    next_jsonb JSONB NOT NULL DEFAULT '[]',
    metadata_jsonb JSONB NOT NULL DEFAULT '{}',
    parent_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_created
ON checkpoints(thread_id, created_at DESC)
"""


class PostgresCheckpointer(BaseCheckpointer):
    """PostgreSQL-backed checkpoint store via asyncpg.

    Supports both single-connection DSN and connection pool modes.
    Uses JSONB columns for efficient state storage and querying.

    Args:
        dsn: PostgreSQL connection string.
        pool: Optional pre-existing connection pool.
    """

    def __init__(
        self,
        dsn: str = "postgresql://localhost/brain_engine",
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._dsn = dsn
        self._pool = pool
        self._initialized = False

    async def setup(self) -> None:
        """Create the connection pool and ensure table exists.

        Call this once before using the checkpointer.
        """
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        await self._ensure_table()

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _ensure_table(self) -> None:
        """Create the checkpoints table if it doesn't exist."""
        if self._initialized:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)
            await conn.execute(_CREATE_INDEX)
        self._initialized = True

    async def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        """Save a checkpoint to PostgreSQL.

        Args:
            config: Must contain ``thread_id``.
            checkpoint: Full graph state.
            metadata: Checkpoint metadata.

        Returns:
            The checkpoint ID.
        """
        await self._ensure_table()
        thread_id = config["thread_id"]
        parent_id = await self._get_latest_id(thread_id)

        cp = Checkpoint(
            thread_id=thread_id,
            values=copy.deepcopy(checkpoint),
            next=tuple(metadata.get("next", ())),
            metadata=copy.deepcopy(metadata),
            parent_id=parent_id,
        )
        await self._insert_checkpoint(cp)
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
        await self._ensure_table()
        row = await self._fetch_latest(config["thread_id"])
        if row is None:
            return None
        return row["values_jsonb"]

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
        await self._ensure_table()
        row = await self._fetch_latest(config["thread_id"])
        if row is None:
            return None
        return _row_to_tuple(row, config)

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
        await self._ensure_table()
        thread_id = config["thread_id"]
        rows = await self._fetch_list(thread_id, limit, before)
        return [_row_to_tuple(r, config) for r in rows]

    # ── Internal ──────────────────────────────────────────────────────

    async def _insert_checkpoint(self, cp: Checkpoint) -> None:
        """Insert a checkpoint row into the database.

        Args:
            cp: Checkpoint to insert.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO checkpoints "
                "(id, thread_id, values_jsonb, next_jsonb, "
                "metadata_jsonb, parent_id, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                cp.id,
                cp.thread_id,
                json.dumps(cp.values),
                json.dumps(cp.next),
                json.dumps(cp.metadata),
                cp.parent_id,
                cp.created_at,
            )

    async def _get_latest_id(self, thread_id: str) -> str | None:
        """Get the latest checkpoint ID for a thread.

        Args:
            thread_id: Thread identifier.

        Returns:
            Latest checkpoint ID or ``None``.
        """
        row = await self._fetch_latest(thread_id)
        return row["id"] if row else None

    async def _fetch_latest(
        self, thread_id: str,
    ) -> asyncpg.Record | None:
        """Fetch the most recent checkpoint row.

        Args:
            thread_id: Thread identifier.

        Returns:
            asyncpg Record or ``None``.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT id, thread_id, values_jsonb, next_jsonb, "
                "metadata_jsonb, parent_id, created_at "
                "FROM checkpoints WHERE thread_id = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                thread_id,
            )

    async def _fetch_list(
        self,
        thread_id: str,
        limit: int,
        before: str | None,
    ) -> list[asyncpg.Record]:
        """Fetch checkpoint history with optional before filter.

        Args:
            thread_id: Thread identifier.
            limit: Maximum rows.
            before: Only rows before this checkpoint ID.

        Returns:
            List of asyncpg Records.
        """
        async with self._pool.acquire() as conn:
            if before:
                return await conn.fetch(
                    "SELECT id, thread_id, values_jsonb, next_jsonb, "
                    "metadata_jsonb, parent_id, created_at "
                    "FROM checkpoints WHERE thread_id = $1 "
                    "AND created_at < ("
                    "  SELECT created_at FROM checkpoints WHERE id = $2"
                    ") ORDER BY created_at DESC LIMIT $3",
                    thread_id, before, limit,
                )
            return await conn.fetch(
                "SELECT id, thread_id, values_jsonb, next_jsonb, "
                "metadata_jsonb, parent_id, created_at "
                "FROM checkpoints WHERE thread_id = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                thread_id, limit,
            )


# ── Row conversion ────────────────────────────────────────────────── #


def _row_to_tuple(
    row: asyncpg.Record,
    config: dict[str, Any],
) -> CheckpointTuple:
    """Convert an asyncpg Record to a CheckpointTuple.

    Args:
        row: Database row.
        config: Original config dict.

    Returns:
        ``CheckpointTuple``.
    """
    values = _parse_jsonb(row["values_jsonb"])
    next_val = _parse_jsonb(row["next_jsonb"])
    metadata = _parse_jsonb(row["metadata_jsonb"])

    cp = Checkpoint(
        id=row["id"],
        thread_id=row["thread_id"],
        values=values,
        next=tuple(next_val) if isinstance(next_val, list) else (),
        metadata=metadata,
        parent_id=row["parent_id"],
        created_at=row["created_at"],
    )
    parent_config = _build_parent_config(cp)
    return CheckpointTuple(
        checkpoint=cp, config=config, parent_config=parent_config,
    )


def _parse_jsonb(value: Any) -> Any:
    """Parse JSONB value — asyncpg may return str or native Python.

    Args:
        value: JSONB column value.

    Returns:
        Parsed Python object.
    """
    if isinstance(value, str):
        return json.loads(value)
    return value


def _build_parent_config(cp: Checkpoint) -> dict[str, Any] | None:
    """Build parent config from a checkpoint's parent_id.

    Args:
        cp: Checkpoint with possible parent_id.

    Returns:
        Parent config dict or None.
    """
    if cp.parent_id:
        return {"thread_id": cp.thread_id, "checkpoint_id": cp.parent_id}
    return None
