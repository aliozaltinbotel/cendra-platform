"""BrainZFS — ZFS-inspired virtual filesystem for AI context management.

Transposes ZFS concepts (COW, snapshots, clones, dedup, compression)
from disk storage to AI context management. Enables zero-cost undo,
instant snapshots, what-if scenarios, and memory deduplication.

Components:
    - COWStore: Content-addressable block storage with versioning.
    - SnapshotEngine: O(1) state snapshots via frozen pointer tables.
    - CloneEngine: Writable forks for parallel scenarios.
    - DeduplicationEngine: Content-addressed dedup with ref-counting.
    - IntegrityChecker: End-to-end checksums and scrubbing.
    - DatasetManager: Hierarchical namespace (brain:// paths).
    - CompressionEngine: Transparent lz4/zstd compression.
    - BrainZFS: Unified interface combining all components.
"""

from brain_engine.zfs.brain_zfs import BrainZFS
from brain_engine.zfs.clones import CloneEngine
from brain_engine.zfs.compression import CompressionEngine
from brain_engine.zfs.cow import COWStore
from brain_engine.zfs.datasets import DatasetManager
from brain_engine.zfs.dedup import DeduplicationEngine
from brain_engine.zfs.integrity import IntegrityChecker
from brain_engine.zfs.models import (
    BlockInfo,
    Change,
    Clone,
    Dataset,
    DedupStats,
    FileInfo,
    GrepMatch,
    PoolStatus,
    Snapshot,
    SnapshotInfo,
    WriteResult,
)
from brain_engine.zfs.snapshots import SnapshotEngine

__all__ = [
    "BlockInfo",
    "BrainZFS",
    "Change",
    "Clone",
    "CloneEngine",
    "CompressionEngine",
    "COWStore",
    "Dataset",
    "DatasetManager",
    "DedupStats",
    "DeduplicationEngine",
    "FileInfo",
    "GrepMatch",
    "IntegrityChecker",
    "PoolStatus",
    "Snapshot",
    "SnapshotEngine",
    "SnapshotInfo",
    "WriteResult",
]
