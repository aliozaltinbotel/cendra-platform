"""SnapshotEngine — O(1) state snapshots via frozen pointer tables.

Creates instant snapshots by copying only the pointer table (not the
actual data blocks). Supports rollback, diffing between snapshots,
and lifecycle management.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from brain_engine.zfs.cow import COWStore
from brain_engine.zfs.models import Change, Snapshot, SnapshotInfo

logger = logging.getLogger(__name__)


class SnapshotEngine:
    """Manages snapshots for a COWStore instance.

    Snapshots are O(1) because they only copy the pointer table
    (a dict of path→hash), not the underlying data blocks. Blocks
    are shared via COW semantics.

    Args:
        cow_store: The COWStore to snapshot.
    """

    def __init__(self, cow_store: COWStore) -> None:
        self._cow = cow_store
        self._snapshots: dict[str, dict[str, str]] = {}
        self._metadata: dict[str, Snapshot] = {}

    @property
    def count(self) -> int:
        """Return the number of stored snapshots."""
        return len(self._snapshots)

    # ── Create ───────────────────────────────────────────────────────

    async def create(self, name: str) -> Snapshot:
        """Create an O(1) snapshot of the current state.

        Copies the pointer table (not data). If a snapshot with the
        same name exists, it is overwritten.

        Args:
            name: Unique snapshot name.

        Returns:
            Snapshot metadata object.
        """
        table = self._cow.get_pointer_table()
        self._snapshots[name] = table

        snap = Snapshot(
            name=name,
            pointer_count=len(table),
            source_dataset=self._cow.dataset,
        )
        self._metadata[name] = snap
        logger.info("Snapshot created: %s (%d pointers)", name, len(table))
        return snap

    # ── Rollback ─────────────────────────────────────────────────────

    async def rollback(self, name: str) -> None:
        """Restore the COWStore to a snapshot state.

        Replaces the current pointer table with the snapshotted one.
        All changes after the snapshot are discarded.

        Args:
            name: Snapshot name to roll back to.

        Raises:
            KeyError: If the snapshot does not exist.
        """
        if name not in self._snapshots:
            msg = f"Snapshot '{name}' not found"
            raise KeyError(msg)

        table = self._snapshots[name]
        self._cow.set_pointer_table(dict(table))
        logger.info("Rolled back to snapshot: %s", name)

    # ── Diff ─────────────────────────────────────────────────────────

    async def diff(self, snap_a: str, snap_b: str) -> list[Change]:
        """Compute differences between two snapshots.

        Args:
            snap_a: Name of the first (older) snapshot.
            snap_b: Name of the second (newer) snapshot.

        Returns:
            List of Change objects describing the differences.

        Raises:
            KeyError: If either snapshot does not exist.
        """
        table_a = self._get_table(snap_a)
        table_b = self._get_table(snap_b)
        return _diff_tables(table_a, table_b)

    async def diff_from_current(self, snap_name: str) -> list[Change]:
        """Compute differences between a snapshot and current state.

        Args:
            snap_name: Snapshot to compare against.

        Returns:
            List of Change objects.

        Raises:
            KeyError: If the snapshot does not exist.
        """
        table_snap = self._get_table(snap_name)
        table_current = self._cow.get_pointer_table()
        return _diff_tables(table_snap, table_current)

    # ── List / Get ───────────────────────────────────────────────────

    async def list_snapshots(self, prefix: str = "") -> list[SnapshotInfo]:
        """List all snapshots, optionally filtered by name prefix.

        Args:
            prefix: Filter by snapshot name prefix.

        Returns:
            List of SnapshotInfo objects sorted by creation time.
        """
        infos: list[SnapshotInfo] = []
        for name, meta in self._metadata.items():
            if name.startswith(prefix):
                infos.append(SnapshotInfo(
                    name=meta.name,
                    created_at=meta.created_at,
                    pointer_count=meta.pointer_count,
                ))
        return sorted(infos, key=lambda s: s.created_at)

    async def get(self, name: str) -> Snapshot | None:
        """Get snapshot metadata by name.

        Args:
            name: Snapshot name.

        Returns:
            Snapshot metadata or None.
        """
        return self._metadata.get(name)

    async def exists(self, name: str) -> bool:
        """Check if a snapshot exists."""
        return name in self._snapshots

    # ── Destroy ──────────────────────────────────────────────────────

    async def destroy(self, name: str) -> bool:
        """Delete a snapshot.

        Args:
            name: Snapshot name to delete.

        Returns:
            True if deleted, False if not found.
        """
        if name not in self._snapshots:
            return False
        del self._snapshots[name]
        self._metadata.pop(name, None)
        logger.info("Destroyed snapshot: %s", name)
        return True

    # ── Internal ─────────────────────────────────────────────────────

    def _get_table(self, name: str) -> dict[str, str]:
        """Get a snapshot's pointer table, raising if not found."""
        if name not in self._snapshots:
            msg = f"Snapshot '{name}' not found"
            raise KeyError(msg)
        return self._snapshots[name]

    def get_pointer_table(self, name: str) -> dict[str, str] | None:
        """Return a copy of a snapshot's pointer table.

        Args:
            name: Snapshot name.

        Returns:
            Copy of the pointer table, or None.
        """
        table = self._snapshots.get(name)
        if table is None:
            return None
        return dict(table)


def _diff_tables(
    table_a: dict[str, str],
    table_b: dict[str, str],
) -> list[Change]:
    """Compute diff between two pointer tables.

    Args:
        table_a: Older state.
        table_b: Newer state.

    Returns:
        List of Change objects.
    """
    changes: list[Change] = []
    all_paths = set(table_a.keys()) | set(table_b.keys())

    for path in sorted(all_paths):
        hash_a = table_a.get(path)
        hash_b = table_b.get(path)

        if hash_a is None and hash_b is not None:
            changes.append(Change(
                path=path, change_type="added", new_hash=hash_b,
            ))
        elif hash_a is not None and hash_b is None:
            changes.append(Change(
                path=path, change_type="removed", old_hash=hash_a,
            ))
        elif hash_a != hash_b:
            changes.append(Change(
                path=path, change_type="modified",
                old_hash=hash_a or "", new_hash=hash_b or "",
            ))

    return changes
