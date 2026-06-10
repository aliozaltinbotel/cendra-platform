"""BrainZFS data models — value objects for the virtual filesystem.

All models are Pydantic BaseModel or frozen dataclasses, ensuring
immutability and safe serialization.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ── Write / Block models ────────────────────────────────────────────


class WriteResult(BaseModel):
    """Result of a COW write operation.

    Attributes:
        path: The written path.
        block_hash: SHA-256 hash of the stored content.
        size_bytes: Size of the raw content in bytes.
        deduplicated: Whether the block was already present.
        version: Version number of this write.
    """

    path: str
    block_hash: str
    size_bytes: int
    deduplicated: bool = False
    version: int = 1


class BlockInfo(BaseModel):
    """Metadata about a single content block.

    Attributes:
        block_hash: SHA-256 content hash.
        size_bytes: Size of raw content.
        ref_count: Number of pointers referencing this block.
        compressed_size: Size after compression (0 if uncompressed).
    """

    block_hash: str
    size_bytes: int
    ref_count: int = 1
    compressed_size: int = 0


# ── Snapshot models ─────────────────────────────────────────────────


class Snapshot(BaseModel):
    """A frozen-in-time copy of a pointer table.

    Attributes:
        name: Unique snapshot name.
        created_at: UTC creation timestamp.
        pointer_count: Number of pointers captured.
        source_dataset: Dataset that was snapshotted.
    """

    name: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    pointer_count: int = 0
    source_dataset: str = ""


class SnapshotInfo(BaseModel):
    """Summary info for listing snapshots.

    Attributes:
        name: Snapshot name.
        created_at: UTC creation timestamp.
        pointer_count: Number of pointers.
    """

    name: str
    created_at: datetime
    pointer_count: int = 0


# ── Clone models ────────────────────────────────────────────────────


class Clone(BaseModel):
    """A writable fork created from a snapshot.

    Attributes:
        name: Clone name.
        source_snapshot: Snapshot this clone was created from.
        created_at: UTC creation timestamp.
        promoted: Whether this clone has been promoted to main.
    """

    name: str
    source_snapshot: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    promoted: bool = False


# ── Dataset models ──────────────────────────────────────────────────


class Dataset(BaseModel):
    """A hierarchical namespace node in the brain:// filesystem.

    Attributes:
        path: Full brain:// path.
        properties: Key-value configuration (compression, dedup, etc.).
        created_at: UTC creation timestamp.
        children: Paths of child datasets.
    """

    path: str
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    children: list[str] = Field(default_factory=list)


# ── Deduplication models ────────────────────────────────────────────


class DedupStats(BaseModel):
    """Deduplication statistics.

    Attributes:
        total_blocks: Number of unique blocks stored.
        total_refs: Total reference count across all blocks.
        dedup_ratio: Ratio of refs to blocks (higher = more dedup).
        saved_bytes: Estimated bytes saved by deduplication.
    """

    total_blocks: int = 0
    total_refs: int = 0
    dedup_ratio: float = 1.0
    saved_bytes: int = 0


# ── Pool status ─────────────────────────────────────────────────────


class PoolStatus(BaseModel):
    """Overall health and usage of a BrainZFS pool.

    Attributes:
        healthy: Whether all integrity checks pass.
        total_blocks: Number of content blocks.
        total_pointers: Number of active pointers.
        total_snapshots: Number of snapshots.
        total_clones: Number of active clones.
        dedup_stats: Deduplication statistics.
    """

    healthy: bool = True
    total_blocks: int = 0
    total_pointers: int = 0
    total_snapshots: int = 0
    total_clones: int = 0
    dedup_stats: DedupStats = Field(default_factory=DedupStats)


# ── Diff / Change models ───────────────────────────────────────────


class Change(BaseModel):
    """A single difference between two snapshots or versions.

    Attributes:
        path: Affected path.
        change_type: One of 'added', 'modified', 'removed'.
        old_hash: Previous block hash (empty for additions).
        new_hash: New block hash (empty for removals).
    """

    path: str
    change_type: str  # "added" | "modified" | "removed"
    old_hash: str = ""
    new_hash: str = ""


# ── Filesystem listing models ───────────────────────────────────────


class FileInfo(BaseModel):
    """Metadata for a single path entry.

    Attributes:
        path: Full path.
        block_hash: Content hash.
        size_bytes: Content size.
    """

    path: str
    block_hash: str = ""
    size_bytes: int = 0


class GrepMatch(BaseModel):
    """A single content search match.

    Attributes:
        path: Path where the match was found.
        line: Matching content line.
        line_number: Line number within the content.
    """

    path: str
    line: str
    line_number: int = 0
