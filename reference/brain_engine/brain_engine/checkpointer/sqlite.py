"""SQLite checkpoint backend for production-grade persistence.

Uses ``aiosqlite`` for async access. Stores checkpoints in a single
table with JSON-serialized state and metadata.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from brain_engine.checkpointer.base import BaseCheckpointer
from brain_engine.checkpointer.models import (
    Checkpoint,
    CheckpointTuple,
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    values_json TEXT NOT NULL,
    next_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    parent_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(thread_id, id)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread
ON checkpoints(thread_id, created_at DESC)
"""


class SQLiteCheckpointer(BaseCheckpointer):
    """SQLite-backed checkpoint store via aiosqlite.

    Args:
        db_path: Path to SQLite database file. Use ``:memory:`` for
            in-memory databases.
    """

    def __init__(self, db_path: str = "checkpoints.db") -> None:
        """Initialize SQLiteCheckpointer."""
        self._db_path = db_path
        self._initialized = False

    async def _ensure_table(self) -> None:
        """Create the checkpoints table if it doesn't exist."""
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX)
            await db.commit()
        self._initialized = True

    async def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        """Save a checkpoint to SQLite.

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
        thread_id = config["thread_id"]

        async with aiosqlite.connect(self._db_path) as db:
            row = await _fetch_latest(db, thread_id)

        if row is None:
            return None
        return json.loads(row[2])

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
        thread_id = config["thread_id"]

        async with aiosqlite.connect(self._db_path) as db:
            row = await _fetch_latest(db, thread_id)

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

        async with aiosqlite.connect(self._db_path) as db:
            rows = await _fetch_list(db, thread_id, limit, before)

        return [_row_to_tuple(r, config) for r in rows]

    async def _insert_checkpoint(self, cp: Checkpoint) -> None:
        """Insert a checkpoint row into the database.

        Args:
            cp: Checkpoint to insert.
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO checkpoints "
                "(id, thread_id, values_json, next_json, metadata_json, "
                "parent_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cp.id,
                    cp.thread_id,
                    json.dumps(cp.values),
                    json.dumps(cp.next),
                    json.dumps(cp.metadata),
                    cp.parent_id,
                    cp.created_at.isoformat(),
                ),
            )
            await db.commit()

    async def _get_latest_id(self, thread_id: str) -> str | None:
        """Get the latest checkpoint ID for a thread.

        Args:
            thread_id: Thread identifier.

        Returns:
            Latest checkpoint ID or ``None``.
        """
        async with aiosqlite.connect(self._db_path) as db:
            row = await _fetch_latest(db, thread_id)
        return row[0] if row else None


async def _fetch_latest(
    db: aiosqlite.Connection,
    thread_id: str,
) -> tuple[Any, ...] | None:
    """Fetch the most recent checkpoint row for a thread.

    Args:
        db: Active database connection.
        thread_id: Thread identifier.

    Returns:
        Row tuple or ``None``.
    """
    cursor = await db.execute(
        "SELECT id, thread_id, values_json, next_json, "
        "metadata_json, parent_id, created_at "
        "FROM checkpoints WHERE thread_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (thread_id,),
    )
    return await cursor.fetchone()


async def _fetch_list(
    db: aiosqlite.Connection,
    thread_id: str,
    limit: int,
    before: str | None,
) -> list[tuple[Any, ...]]:
    """Fetch checkpoint history with optional before filter.

    Args:
        db: Active database connection.
        thread_id: Thread identifier.
        limit: Maximum rows to return.
        before: Only rows before this checkpoint ID's timestamp.

    Returns:
        List of row tuples in reverse chronological order.
    """
    if before:
        cursor = await db.execute(
            "SELECT id, thread_id, values_json, next_json, "
            "metadata_json, parent_id, created_at "
            "FROM checkpoints WHERE thread_id = ? "
            "AND created_at < (SELECT created_at FROM checkpoints WHERE id = ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (thread_id, before, limit),
        )
    else:
        cursor = await db.execute(
            "SELECT id, thread_id, values_json, next_json, "
            "metadata_json, parent_id, created_at "
            "FROM checkpoints WHERE thread_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (thread_id, limit),
        )
    return await cursor.fetchall()


def _row_to_tuple(
    row: tuple[Any, ...],
    config: dict[str, Any],
) -> CheckpointTuple:
    """Convert a database row to a CheckpointTuple.

    Args:
        row: Database row (id, thread_id, values_json, next_json,
            metadata_json, parent_id, created_at).
        config: Original config dict.

    Returns:
        ``CheckpointTuple``.
    """
    cp = Checkpoint(
        id=row[0],
        thread_id=row[1],
        values=json.loads(row[2]),
        next=tuple(json.loads(row[3])),
        metadata=json.loads(row[4]),
        parent_id=row[5],
        created_at=datetime.fromisoformat(row[6]),
    )
    parent_config = None
    if cp.parent_id:
        parent_config = {"thread_id": cp.thread_id, "checkpoint_id": cp.parent_id}

    return CheckpointTuple(
        checkpoint=cp,
        config=config,
        parent_config=parent_config,
    )
