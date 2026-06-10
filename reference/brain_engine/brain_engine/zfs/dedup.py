"""DeduplicationEngine — content-addressed dedup with ref-counting.

Tracks block reference counts across the COWStore. When multiple
pointers reference the same content (same SHA-256 hash), the data
is stored only once. Provides statistics on dedup efficiency.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.zfs.cow import COWStore
from brain_engine.zfs.models import DedupStats

logger = logging.getLogger(__name__)


class DeduplicationEngine:
    """Manages deduplication statistics and block lifecycle.

    Works with the COWStore's ref-counting system to track
    how effectively data is being deduplicated.

    Args:
        cow_store: The COWStore instance to monitor.
    """

    def __init__(self, cow_store: COWStore) -> None:
        self._cow = cow_store

    async def get_stats(self) -> DedupStats:
        """Compute current deduplication statistics.

        Returns:
            DedupStats with block counts, ref totals, and ratio.
        """
        total_blocks = await self._cow.total_blocks()
        ref_counts = self._cow._ref_counts

        total_refs = sum(ref_counts.values())
        ratio = (total_refs / total_blocks) if total_blocks > 0 else 1.0

        saved = self._estimate_saved_bytes(ref_counts)

        return DedupStats(
            total_blocks=total_blocks,
            total_refs=total_refs,
            dedup_ratio=round(ratio, 2),
            saved_bytes=saved,
        )

    async def get_ref_count(self, block_hash: str) -> int:
        """Get the reference count for a specific block.

        Args:
            block_hash: SHA-256 hash of the block.

        Returns:
            Reference count (0 if block is unknown).
        """
        return await self._cow.get_ref_count(block_hash)

    async def find_duplicates(self) -> list[str]:
        """Find all block hashes that are referenced more than once.

        Returns:
            List of deduplicated block hashes.
        """
        return [
            h for h, count in self._cow._ref_counts.items()
            if count > 1
        ]

    async def get_orphaned_blocks(self) -> list[str]:
        """Find blocks with zero references (eligible for GC).

        Returns:
            List of orphaned block hashes.
        """
        return [
            h for h, count in self._cow._ref_counts.items()
            if count <= 0
        ]

    def _estimate_saved_bytes(
        self,
        ref_counts: dict[str, int],
    ) -> int:
        """Estimate bytes saved by deduplication.

        For each block referenced more than once, we save
        (ref_count - 1) * estimated_block_size bytes.
        Uses a conservative estimate of 1KB per block.

        Args:
            ref_counts: Mapping of hash → ref_count.

        Returns:
            Estimated saved bytes.
        """
        avg_block_size = 1024
        saved = 0
        for count in ref_counts.values():
            if count > 1:
                saved += (count - 1) * avg_block_size
        return saved
