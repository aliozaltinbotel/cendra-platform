"""CloneEngine — writable forks for parallel scenarios.

Creates instant writable copies from snapshots. Each clone gets its
own COWStore with an isolated pointer table that shares blocks with
the parent via COW. Used for what-if scenarios, A/B testing, and
parallel subagent execution.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.zfs.cow import COWStore, MemoryBackend
from brain_engine.zfs.models import Clone
from brain_engine.zfs.snapshots import SnapshotEngine

logger = logging.getLogger(__name__)


class CloneEngine:
    """Manages writable clones created from snapshots.

    Each clone is an independent COWStore initialized with a copy
    of the snapshot's pointer table. Writes in a clone do not affect
    the parent store.

    Args:
        parent_cow: The main COWStore.
        snapshot_engine: SnapshotEngine managing parent snapshots.
    """

    def __init__(
        self,
        parent_cow: COWStore,
        snapshot_engine: SnapshotEngine,
    ) -> None:
        self._parent = parent_cow
        self._snapshots = snapshot_engine
        self._clones: dict[str, COWStore] = {}
        self._metadata: dict[str, Clone] = {}

    @property
    def count(self) -> int:
        """Return the number of active clones."""
        return len(self._clones)

    # ── Create ───────────────────────────────────────────────────────

    async def clone(
        self,
        snapshot_name: str,
        clone_name: str,
    ) -> Clone:
        """Create a writable clone from a snapshot.

        The clone shares blocks with the parent through COW semantics.
        Writes in the clone are isolated.

        Args:
            snapshot_name: Name of the source snapshot.
            clone_name: Name for the new clone.

        Returns:
            Clone metadata object.

        Raises:
            KeyError: If the snapshot does not exist.
            ValueError: If a clone with this name already exists.
        """
        if clone_name in self._clones:
            msg = f"Clone '{clone_name}' already exists"
            raise ValueError(msg)

        table = self._snapshots.get_pointer_table(snapshot_name)
        if table is None:
            msg = f"Snapshot '{snapshot_name}' not found"
            raise KeyError(msg)

        clone_cow = COWStore(
            backend=self._parent._backend,
            dataset=clone_name,
        )
        clone_cow.set_pointer_table(table)

        meta = Clone(name=clone_name, source_snapshot=snapshot_name)
        self._clones[clone_name] = clone_cow
        self._metadata[clone_name] = meta

        logger.info(
            "Clone created: %s from snapshot %s (%d pointers)",
            clone_name, snapshot_name, len(table),
        )
        return meta

    # ── Read / Write in clone ────────────────────────────────────────

    async def read(self, clone_name: str, path: str) -> Any:
        """Read data from a clone's namespace.

        Args:
            clone_name: Name of the clone.
            path: Logical path within the clone.

        Returns:
            Deserialized data, or None.

        Raises:
            KeyError: If the clone does not exist.
        """
        cow = self._get_clone_cow(clone_name)
        return await cow.read(path)

    async def write(self, clone_name: str, path: str, data: Any) -> Any:
        """Write data into a clone's namespace.

        Args:
            clone_name: Name of the clone.
            path: Logical path.
            data: Data to write.

        Returns:
            WriteResult from the clone's COWStore.

        Raises:
            KeyError: If the clone does not exist.
        """
        cow = self._get_clone_cow(clone_name)
        return await cow.write(path, data)

    # ── Promote ──────────────────────────────────────────────────────

    async def promote(self, clone_name: str) -> None:
        """Promote a clone to become the main store's state.

        The parent COWStore's pointer table is replaced with the
        clone's. The clone is then removed.

        Args:
            clone_name: Clone to promote.

        Raises:
            KeyError: If the clone does not exist.
        """
        cow = self._get_clone_cow(clone_name)
        table = cow.get_pointer_table()
        self._parent.set_pointer_table(table)

        meta = self._metadata.get(clone_name)
        if meta:
            meta.promoted = True

        del self._clones[clone_name]
        self._metadata.pop(clone_name, None)
        logger.info("Clone promoted: %s", clone_name)

    # ── Destroy ──────────────────────────────────────────────────────

    async def destroy(self, clone_name: str) -> bool:
        """Destroy a clone, releasing its isolated pointers.

        Shared blocks remain (owned by parent or other clones).

        Args:
            clone_name: Clone to destroy.

        Returns:
            True if destroyed, False if not found.
        """
        if clone_name not in self._clones:
            return False
        del self._clones[clone_name]
        self._metadata.pop(clone_name, None)
        logger.info("Clone destroyed: %s", clone_name)
        return True

    # ── Query ────────────────────────────────────────────────────────

    async def get(self, clone_name: str) -> Clone | None:
        """Get clone metadata.

        Args:
            clone_name: Clone name.

        Returns:
            Clone metadata or None.
        """
        return self._metadata.get(clone_name)

    async def list_clones(self) -> list[Clone]:
        """List all active clones.

        Returns:
            List of Clone metadata objects.
        """
        return list(self._metadata.values())

    def get_cow_store(self, clone_name: str) -> COWStore | None:
        """Get the COWStore for a specific clone.

        Args:
            clone_name: Clone name.

        Returns:
            COWStore instance or None.
        """
        return self._clones.get(clone_name)

    # ── Internal ─────────────────────────────────────────────────────

    def _get_clone_cow(self, clone_name: str) -> COWStore:
        """Retrieve a clone's COWStore, raising if not found."""
        cow = self._clones.get(clone_name)
        if cow is None:
            msg = f"Clone '{clone_name}' not found"
            raise KeyError(msg)
        return cow
