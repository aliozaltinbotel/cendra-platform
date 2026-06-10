"""COWStore — Copy-on-Write content-addressable block storage.

Implements the core storage engine for BrainZFS. Every write creates
a new immutable block identified by its SHA-256 hash. Pointer tables
map logical paths to block hashes, enabling O(1) snapshots by
copying only the pointer table (not the data).

Key properties:
    - Content-addressable: identical data → same hash → automatic dedup.
    - Versioned: every write appends to a version log.
    - Immutable blocks: blocks are never modified in place.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Protocol, runtime_checkable

from brain_engine.zfs.models import BlockInfo, Change, WriteResult

logger = logging.getLogger(__name__)


@runtime_checkable
class BlockBackend(Protocol):
    """Protocol for pluggable block storage backends.

    Any backend (Redis, dict, S3) that implements these methods
    can serve as the COWStore storage layer.
    """

    async def get_block(self, block_hash: str) -> bytes | None:
        """Retrieve raw block data by hash."""
        ...

    async def put_block(self, block_hash: str, data: bytes) -> bool:
        """Store a block. Returns True if new, False if dedup."""
        ...

    async def has_block(self, block_hash: str) -> bool:
        """Check if a block exists."""
        ...

    async def delete_block(self, block_hash: str) -> bool:
        """Delete a block. Returns True if deleted."""
        ...

    async def block_count(self) -> int:
        """Return total number of stored blocks."""
        ...


class MemoryBackend:
    """In-memory block storage backend for testing and lightweight use.

    Stores blocks as bytes in a dictionary keyed by SHA-256 hash.
    """

    def __init__(self) -> None:
        self._blocks: dict[str, bytes] = {}

    async def get_block(self, block_hash: str) -> bytes | None:
        """Retrieve a block by hash."""
        return self._blocks.get(block_hash)

    async def put_block(self, block_hash: str, data: bytes) -> bool:
        """Store a block. Returns True if new."""
        if block_hash in self._blocks:
            return False
        self._blocks[block_hash] = data
        return True

    async def has_block(self, block_hash: str) -> bool:
        """Check block existence."""
        return block_hash in self._blocks

    async def delete_block(self, block_hash: str) -> bool:
        """Delete a block."""
        if block_hash in self._blocks:
            del self._blocks[block_hash]
            return True
        return False

    async def block_count(self) -> int:
        """Return block count."""
        return len(self._blocks)


class COWStore:
    """Copy-on-Write store with content-addressable blocks.

    Manages a pointer table (path → hash) and a version log
    (path → list of historical hashes). Writes are atomic:
    data is stored as a block first, then the pointer is updated.

    Args:
        backend: Block storage backend (default: MemoryBackend).
        dataset: Namespace prefix for pointer isolation.
    """

    def __init__(
        self,
        backend: BlockBackend | None = None,
        dataset: str = "default",
    ) -> None:
        self._backend: BlockBackend = backend or MemoryBackend()
        self._dataset = dataset
        self._pointers: dict[str, str] = {}
        self._versions: dict[str, list[str]] = {}
        self._ref_counts: dict[str, int] = {}

    @property
    def dataset(self) -> str:
        """Return the current dataset namespace."""
        return self._dataset

    @property
    def pointer_count(self) -> int:
        """Return the number of active pointers."""
        return len(self._pointers)

    # ── Write ────────────────────────────────────────────────────────

    async def write(self, path: str, data: Any) -> WriteResult:
        """Write data to a path using Copy-on-Write semantics.

        Steps:
            1. Serialize data to bytes.
            2. Compute SHA-256 hash.
            3. Store block if new (dedup if exists).
            4. Save old pointer to version log.
            5. Update pointer table.

        Args:
            path: Logical path for the data.
            data: Any JSON-serializable data.

        Returns:
            WriteResult with hash, size, dedup flag, and version.
        """
        raw = _serialize(data)
        block_hash = _compute_hash(raw)

        is_new = await self._backend.put_block(block_hash, raw)
        deduplicated = not is_new

        self._increment_ref(block_hash)

        old_hash = self._pointers.get(path)
        if old_hash:
            self._decrement_ref(old_hash)

        self._append_version(path, block_hash)
        self._pointers[path] = block_hash

        version = len(self._versions.get(path, []))
        logger.debug(
            "COW write: %s → %s (dedup=%s, v=%d)",
            path, block_hash[:12], deduplicated, version,
        )
        return WriteResult(
            path=path,
            block_hash=block_hash,
            size_bytes=len(raw),
            deduplicated=deduplicated,
            version=version,
        )

    # ── Read ─────────────────────────────────────────────────────────

    async def read(self, path: str, version: int | None = None) -> Any:
        """Read data from a path, optionally at a historical version.

        Args:
            path: Logical path.
            version: Specific version number (1-based). None = current.

        Returns:
            Deserialized data, or None if path not found.
        """
        block_hash = self._resolve_hash(path, version)
        if block_hash is None:
            return None

        raw = await self._backend.get_block(block_hash)
        if raw is None:
            logger.warning("Block %s missing for path %s", block_hash[:12], path)
            return None
        return _deserialize(raw)

    # ── Delete ───────────────────────────────────────────────────────

    async def delete(self, path: str) -> bool:
        """Remove a path and decrement its block ref count.

        Args:
            path: Path to remove.

        Returns:
            True if removed, False if not found.
        """
        block_hash = self._pointers.pop(path, None)
        if block_hash is None:
            return False
        self._decrement_ref(block_hash)
        await self._gc_block(block_hash)
        return True

    # ── List / Search ────────────────────────────────────────────────

    def list_paths(self, prefix: str = "") -> list[str]:
        """List all paths under a prefix.

        Args:
            prefix: Path prefix filter.

        Returns:
            Sorted list of matching paths.
        """
        return sorted(
            p for p in self._pointers
            if p.startswith(prefix)
        )

    def has_path(self, path: str) -> bool:
        """Check if a path exists in the pointer table."""
        return path in self._pointers

    def get_hash(self, path: str) -> str | None:
        """Return the current block hash for a path."""
        return self._pointers.get(path)

    # ── Pointer table operations (for snapshots) ────────────────────

    def get_pointer_table(self) -> dict[str, str]:
        """Return a shallow copy of the current pointer table.

        Returns:
            Dict mapping path → block_hash.
        """
        return dict(self._pointers)

    def set_pointer_table(self, table: dict[str, str]) -> None:
        """Replace the pointer table (used by snapshot rollback).

        Adjusts ref counts for the old and new tables.

        Args:
            table: New pointer table to apply.
        """
        for path, old_hash in self._pointers.items():
            self._decrement_ref(old_hash)

        self._pointers = dict(table)

        for path, new_hash in self._pointers.items():
            self._increment_ref(new_hash)

    def get_version_count(self, path: str) -> int:
        """Return the number of versions stored for a path."""
        return len(self._versions.get(path, []))

    # ── Block info ───────────────────────────────────────────────────

    async def get_block_info(self, block_hash: str) -> BlockInfo | None:
        """Get metadata for a specific block.

        Args:
            block_hash: SHA-256 hash of the block.

        Returns:
            BlockInfo or None if not found.
        """
        raw = await self._backend.get_block(block_hash)
        if raw is None:
            return None
        return BlockInfo(
            block_hash=block_hash,
            size_bytes=len(raw),
            ref_count=self._ref_counts.get(block_hash, 0),
        )

    async def get_ref_count(self, block_hash: str) -> int:
        """Return the reference count for a block."""
        return self._ref_counts.get(block_hash, 0)

    async def total_blocks(self) -> int:
        """Return total number of stored blocks."""
        return await self._backend.block_count()

    # ── Internal helpers ─────────────────────────────────────────────

    def _resolve_hash(self, path: str, version: int | None) -> str | None:
        """Resolve a path+version to a block hash.

        Args:
            path: Logical path.
            version: 1-based version, or None for current.

        Returns:
            Block hash or None.
        """
        if version is None:
            return self._pointers.get(path)

        history = self._versions.get(path, [])
        if not history or version < 1 or version > len(history):
            return None
        return history[version - 1]

    def _append_version(self, path: str, block_hash: str) -> None:
        """Add a hash to the version history for a path."""
        if path not in self._versions:
            self._versions[path] = []
        self._versions[path].append(block_hash)

    def _increment_ref(self, block_hash: str) -> None:
        """Increment reference count for a block."""
        self._ref_counts[block_hash] = self._ref_counts.get(block_hash, 0) + 1

    def _decrement_ref(self, block_hash: str) -> None:
        """Decrement reference count for a block."""
        count = self._ref_counts.get(block_hash, 0)
        if count > 0:
            self._ref_counts[block_hash] = count - 1

    async def _gc_block(self, block_hash: str) -> None:
        """Garbage-collect a block if its ref count reaches zero."""
        if self._ref_counts.get(block_hash, 0) <= 0:
            await self._backend.delete_block(block_hash)
            self._ref_counts.pop(block_hash, None)
            logger.debug("GC'd block: %s", block_hash[:12])


# ── Module-level helpers ─────────────────────────────────────────────


def _serialize(data: Any) -> bytes:
    """Serialize any JSON-compatible data to bytes.

    Args:
        data: Data to serialize.

    Returns:
        UTF-8 encoded JSON bytes.
    """
    return json.dumps(data, default=str, sort_keys=True).encode("utf-8")


def _deserialize(raw: bytes) -> Any:
    """Deserialize bytes back to Python objects.

    Args:
        raw: UTF-8 JSON bytes.

    Returns:
        Deserialized Python object.
    """
    return json.loads(raw.decode("utf-8"))


def _compute_hash(data: bytes) -> str:
    """Compute SHA-256 hash of raw bytes.

    Args:
        data: Raw bytes to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(data).hexdigest()
