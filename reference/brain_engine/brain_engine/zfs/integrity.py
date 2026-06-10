"""IntegrityChecker — end-to-end checksums and scrubbing.

Detects corrupted data before the agent responds by re-computing
block hashes and comparing against stored values. Provides per-path
verification and full-session scrubbing.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.zfs.cow import COWStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScrubResult:
    """Result of a full integrity scrub.

    Attributes:
        total_checked: Number of blocks verified.
        healthy: Number of blocks that passed verification.
        corrupted: List of paths with corrupted data.
        missing: List of paths whose blocks are missing.
        passed: Whether all checks passed.
    """

    total_checked: int = 0
    healthy: int = 0
    corrupted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Whether the scrub found no issues."""
        return not self.corrupted and not self.missing


class IntegrityChecker:
    """Verifies data integrity for a COWStore.

    Computes SHA-256 of stored blocks and compares against the
    content-addressed hash. Detects corruption from Redis eviction,
    race conditions, or network errors.

    Args:
        cow_store: COWStore instance to verify.
    """

    def __init__(self, cow_store: COWStore) -> None:
        self._cow = cow_store

    async def verify_block(self, block_hash: str) -> bool:
        """Verify a single block by re-computing its hash.

        Args:
            block_hash: Expected SHA-256 hash.

        Returns:
            True if the block exists and hash matches.
        """
        raw = await self._cow._backend.get_block(block_hash)
        if raw is None:
            return False
        actual_hash = hashlib.sha256(raw).hexdigest()
        return actual_hash == block_hash

    async def verify_path(self, path: str) -> tuple[Any, bool]:
        """Verify a path's block integrity and return its data.

        Args:
            path: Logical path to verify.

        Returns:
            Tuple of (data, is_valid). Data is None if invalid.
        """
        block_hash = self._cow.get_hash(path)
        if block_hash is None:
            return None, False

        valid = await self.verify_block(block_hash)
        if not valid:
            return None, False

        data = await self._cow.read(path)
        return data, True

    async def scrub(self, prefix: str = "") -> ScrubResult:
        """Run a full integrity scrub on all paths under a prefix.

        Verifies every block referenced by the pointer table.
        Reports corrupted and missing blocks.

        Args:
            prefix: Optional path prefix to limit the scrub scope.

        Returns:
            ScrubResult with totals and error lists.
        """
        result = ScrubResult()
        paths = self._cow.list_paths(prefix)

        for path in paths:
            result.total_checked += 1
            block_hash = self._cow.get_hash(path)
            if block_hash is None:
                result.missing.append(path)
                continue

            has_block = await self._cow._backend.has_block(block_hash)
            if not has_block:
                result.missing.append(path)
                logger.warning("Missing block for path: %s", path)
                continue

            valid = await self.verify_block(block_hash)
            if valid:
                result.healthy += 1
            else:
                result.corrupted.append(path)
                logger.warning("Corrupted block for path: %s", path)

        logger.info(
            "Scrub complete: %d checked, %d healthy, %d corrupted, %d missing",
            result.total_checked, result.healthy,
            len(result.corrupted), len(result.missing),
        )
        return result

    async def quick_check(self) -> bool:
        """Perform a quick health check (verify 1 random block).

        Returns:
            True if the checked block is valid, or if no blocks exist.
        """
        table = self._cow.get_pointer_table()
        if not table:
            return True

        first_path = next(iter(table))
        block_hash = table[first_path]
        return await self.verify_block(block_hash)
