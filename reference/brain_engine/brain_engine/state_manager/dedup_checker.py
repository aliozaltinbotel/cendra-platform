"""Deduplication Checker for detecting repeated messages and events.

Uses content hashing with a sliding time window to identify duplicate
inputs. Useful for preventing the agent from re-processing the same
user message or event within a configurable window.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class DedupEntry:
    """A record of a previously seen message/event.

    Attributes:
        content_hash: SHA-256 hash of the content.
        timestamp: Unix timestamp when the content was first seen.
        content_preview: First 80 characters of the original content.
    """

    content_hash: str
    timestamp: float
    content_preview: str


class DedupChecker:
    """Detects repeated messages or events within a configurable time window.

    The checker hashes incoming content and maintains a sliding window
    of recent hashes. Content that matches a hash within the window is
    flagged as a duplicate.

    Args:
        window_seconds: Time window in seconds. Messages older than this
            are automatically evicted.
        max_entries: Maximum number of entries to retain. Oldest entries
            are evicted when this limit is reached.
    """

    def __init__(
        self,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        max_entries: int = 1000,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_entries = max_entries
        self._seen: dict[str, DedupEntry] = {}

    @staticmethod
    def _hash_content(content: str) -> str:
        """Compute a SHA-256 hash of the content string."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _evict_expired(self) -> None:
        """Remove entries that have fallen outside the time window."""
        cutoff = time.time() - self.window_seconds
        expired_keys = [
            k for k, entry in self._seen.items() if entry.timestamp < cutoff
        ]
        for k in expired_keys:
            del self._seen[k]

    def _enforce_max_entries(self) -> None:
        """Evict the oldest entries if we exceed max_entries."""
        if len(self._seen) <= self.max_entries:
            return
        sorted_entries = sorted(
            self._seen.items(), key=lambda item: item[1].timestamp
        )
        excess = len(self._seen) - self.max_entries
        for key, _ in sorted_entries[:excess]:
            del self._seen[key]

    def is_duplicate(self, content: str) -> bool:
        """Check whether the given content has been seen within the time window.

        This method also registers the content if it is not a duplicate,
        so subsequent calls with the same content will return True.

        Args:
            content: The message or event content to check.

        Returns:
            True if the content was already seen within the window.
        """
        self._evict_expired()

        content_hash = self._hash_content(content)

        if content_hash in self._seen:
            logger.debug("Duplicate detected: %s", content[:80])
            return True

        self._seen[content_hash] = DedupEntry(
            content_hash=content_hash,
            timestamp=time.time(),
            content_preview=content[:80],
        )
        self._enforce_max_entries()

        return False

    def check_without_registering(self, content: str) -> bool:
        """Check for duplicates without registering the content.

        Args:
            content: The content to check.

        Returns:
            True if the content is already in the dedup window.
        """
        self._evict_expired()
        content_hash = self._hash_content(content)
        return content_hash in self._seen

    def register(self, content: str) -> None:
        """Explicitly register content without checking for duplicates.

        Args:
            content: The content to register.
        """
        content_hash = self._hash_content(content)
        self._seen[content_hash] = DedupEntry(
            content_hash=content_hash,
            timestamp=time.time(),
            content_preview=content[:80],
        )
        self._enforce_max_entries()

    def clear(self) -> None:
        """Clear all dedup entries."""
        self._seen.clear()

    @property
    def entry_count(self) -> int:
        """Number of entries currently tracked."""
        return len(self._seen)

    def __repr__(self) -> str:
        return (
            f"DedupChecker(window={self.window_seconds}s, "
            f"entries={self.entry_count})"
        )
