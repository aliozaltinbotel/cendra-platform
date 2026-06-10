"""BrainZFS — unified interface for the virtual filesystem.

Single entry point combining all ZFS components: COW storage,
snapshots, clones, dedup, integrity, datasets, and compression.
Provides high-level operations for session management, what-if
scenarios, and data lifecycle.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any

from brain_engine.zfs.clones import CloneEngine
from brain_engine.zfs.compression import CompressionAlgo, CompressionEngine
from brain_engine.zfs.cow import COWStore, MemoryBackend
from brain_engine.zfs.datasets import DatasetManager
from brain_engine.zfs.dedup import DeduplicationEngine
from brain_engine.zfs.integrity import IntegrityChecker, ScrubResult
from brain_engine.zfs.models import (
    Change,
    Clone,
    Dataset,
    FileInfo,
    GrepMatch,
    PoolStatus,
    Snapshot,
    SnapshotInfo,
    WriteResult,
)
from brain_engine.zfs.snapshots import SnapshotEngine

logger = logging.getLogger(__name__)


class BrainZFS:
    """Unified BrainZFS virtual filesystem.

    Combines all ZFS-inspired components into a single interface
    for the Brain Engine's context management needs.

    Args:
        backend: Block storage backend (default: MemoryBackend).
        pool_name: Name of the storage pool.
        compression_algo: Default compression algorithm.
    """

    def __init__(
        self,
        backend: Any | None = None,
        pool_name: str = "brain",
        compression_algo: CompressionAlgo = CompressionAlgo.ZLIB,
    ) -> None:
        self._pool_name = pool_name
        self.cow = COWStore(backend=backend or MemoryBackend())
        self.snapshots = SnapshotEngine(self.cow)
        self.clones = CloneEngine(self.cow, self.snapshots)
        self.dedup = DeduplicationEngine(self.cow)
        self.integrity = IntegrityChecker(self.cow)
        self.datasets = DatasetManager(root_path="brain://")
        self.compression = CompressionEngine(default_algo=compression_algo)

    @property
    def pool_name(self) -> str:
        """Return the pool name."""
        return self._pool_name

    # ── Pool operations ──────────────────────────────────────────────

    async def pool_status(self) -> PoolStatus:
        """Get overall pool health and usage statistics.

        Returns:
            PoolStatus with block counts, pointers, and dedup stats.
        """
        dedup_stats = await self.dedup.get_stats()
        scrub = await self.integrity.quick_check()
        snap_list = await self.snapshots.list_snapshots()
        clone_list = await self.clones.list_clones()

        return PoolStatus(
            healthy=scrub,
            total_blocks=dedup_stats.total_blocks,
            total_pointers=self.cow.pointer_count,
            total_snapshots=len(snap_list),
            total_clones=len(clone_list),
            dedup_stats=dedup_stats,
        )

    async def scrub(self, prefix: str = "") -> ScrubResult:
        """Run a full integrity scrub.

        Args:
            prefix: Optional path prefix to limit scope.

        Returns:
            ScrubResult with verification details.
        """
        return await self.integrity.scrub(prefix)

    # ── Dataset operations ───────────────────────────────────────────

    async def create_dataset(
        self,
        path: str,
        **properties: Any,
    ) -> Dataset:
        """Create a new dataset in the hierarchy.

        Args:
            path: Full brain:// path.
            **properties: Dataset properties.

        Returns:
            Created Dataset.
        """
        return await self.datasets.create(path, **properties)

    async def destroy_dataset(
        self,
        path: str,
        recursive: bool = False,
    ) -> bool:
        """Destroy a dataset.

        Args:
            path: Dataset path.
            recursive: Delete children too.

        Returns:
            True if deleted.
        """
        return await self.datasets.destroy(path, recursive=recursive)

    # ── Data operations ──────────────────────────────────────────────

    async def read(self, path: str, version: int | None = None) -> Any:
        """Read data from a path, optionally at a specific version.

        Args:
            path: Logical path.
            version: Historical version (1-based). None = current.

        Returns:
            Deserialized data or None.
        """
        return await self.cow.read(path, version)

    async def write(self, path: str, data: Any) -> WriteResult:
        """Write data to a path.

        Args:
            path: Logical path.
            data: JSON-serializable data.

        Returns:
            WriteResult with hash, size, and dedup info.
        """
        return await self.cow.write(path, data)

    async def edit(
        self,
        path: str,
        old_value: str,
        new_value: str,
    ) -> WriteResult | None:
        """Edit existing data by replacing a substring.

        Reads the current value, performs string replacement,
        and writes back.

        Args:
            path: Path to edit.
            old_value: String to find.
            new_value: Replacement string.

        Returns:
            WriteResult or None if path not found or no match.
        """
        current = await self.cow.read(path)
        if current is None:
            return None

        text = str(current)
        if old_value not in text:
            return None

        updated = text.replace(old_value, new_value, 1)
        return await self.cow.write(path, updated)

    async def delete(self, path: str) -> bool:
        """Delete a path.

        Args:
            path: Path to remove.

        Returns:
            True if deleted.
        """
        return await self.cow.delete(path)

    async def ls(self, prefix: str = "") -> list[FileInfo]:
        """List all paths under a prefix.

        Args:
            prefix: Path prefix.

        Returns:
            List of FileInfo objects.
        """
        paths = self.cow.list_paths(prefix)
        result: list[FileInfo] = []
        for p in paths:
            block_hash = self.cow.get_hash(p) or ""
            info = await self.cow.get_block_info(block_hash) if block_hash else None
            result.append(FileInfo(
                path=p,
                block_hash=block_hash,
                size_bytes=info.size_bytes if info else 0,
            ))
        return result

    async def glob(self, pattern: str) -> list[str]:
        """Find paths matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g., "sessions/*/context").

        Returns:
            List of matching paths.
        """
        all_paths = self.cow.list_paths()
        return [p for p in all_paths if fnmatch.fnmatch(p, pattern)]

    async def grep(self, pattern: str, prefix: str = "") -> list[GrepMatch]:
        """Search content of all paths for a regex pattern.

        Args:
            pattern: Regular expression to search for.
            prefix: Limit search to paths under this prefix.

        Returns:
            List of GrepMatch objects.
        """
        compiled = re.compile(pattern)
        paths = self.cow.list_paths(prefix)
        matches: list[GrepMatch] = []

        for path in paths:
            data = await self.cow.read(path)
            if data is None:
                continue
            text = str(data)
            for i, line in enumerate(text.split("\n"), start=1):
                if compiled.search(line):
                    matches.append(GrepMatch(
                        path=path, line=line, line_number=i,
                    ))

        return matches

    # ── Snapshot operations ──────────────────────────────────────────

    async def snapshot(self, name: str) -> Snapshot:
        """Create an O(1) snapshot.

        Args:
            name: Snapshot name.

        Returns:
            Snapshot metadata.
        """
        return await self.snapshots.create(name)

    async def rollback(self, name: str) -> None:
        """Rollback to a snapshot state.

        Args:
            name: Snapshot to restore.
        """
        await self.snapshots.rollback(name)

    async def diff(self, snap_a: str, snap_b: str) -> list[Change]:
        """Diff two snapshots.

        Args:
            snap_a: First snapshot name.
            snap_b: Second snapshot name.

        Returns:
            List of changes.
        """
        return await self.snapshots.diff(snap_a, snap_b)

    # ── Clone operations ─────────────────────────────────────────────

    async def clone(self, snapshot_name: str, target: str) -> Clone:
        """Create a writable clone from a snapshot.

        Args:
            snapshot_name: Source snapshot.
            target: Clone name.

        Returns:
            Clone metadata.
        """
        return await self.clones.clone(snapshot_name, target)

    async def promote(self, clone_name: str) -> None:
        """Promote a clone to become the main state.

        Args:
            clone_name: Clone to promote.
        """
        await self.clones.promote(clone_name)

    # ── Session integration ──────────────────────────────────────────

    async def checkpoint_session(self, session_id: str) -> str:
        """Create a checkpoint snapshot for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Snapshot name.
        """
        import time
        snap_name = f"sessions/{session_id}@{int(time.time())}"
        await self.snapshots.create(snap_name)
        return snap_name

    async def fork_for_whatif(self, session_id: str) -> str:
        """Fork a session for what-if scenario exploration.

        Creates a snapshot and then a writable clone.

        Args:
            session_id: Session to fork.

        Returns:
            Clone name for the what-if fork.
        """
        import time
        snap_name = f"sessions/{session_id}@whatif_{int(time.time())}"
        clone_name = f"whatif_{session_id}"

        await self.snapshots.create(snap_name)
        await self.clones.clone(snap_name, clone_name)
        return clone_name
