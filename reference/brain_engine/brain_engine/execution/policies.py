"""Execution policies — retry and cache configuration per step.

Provides RetryPolicy for automatic retries on transient failures
and CachePolicy for caching expensive step results.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Type

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RetryPolicy(BaseModel):
    """Retry configuration for a step or tool.

    Implements exponential backoff with jitter.

    Attributes:
        max_attempts: Maximum number of attempts (including first).
        initial_interval: Seconds before first retry.
        backoff_factor: Multiply interval by this each retry.
        max_interval: Cap on retry interval.
        jitter: Whether to add random jitter.
        retry_on: Exception class names to retry on.
    """

    max_attempts: int = 3
    initial_interval: float = 0.5
    backoff_factor: float = 2.0
    max_interval: float = 128.0
    jitter: bool = True
    retry_on: list[str] = Field(
        default_factory=lambda: ["Exception"],
    )

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """Determine if a retry should be attempted.

        Args:
            error: The exception that occurred.
            attempt: Current attempt number (1-based).

        Returns:
            True if a retry should be attempted.
        """
        if attempt >= self.max_attempts:
            return False
        error_name = type(error).__name__
        return any(
            error_name == name or name == "Exception"
            for name in self.retry_on
        )

    def get_delay(self, attempt: int) -> float:
        """Calculate the delay before the next retry.

        Args:
            attempt: Current attempt number (1-based).

        Returns:
            Delay in seconds.
        """
        delay = self.initial_interval * (self.backoff_factor ** (attempt - 1))
        delay = min(delay, self.max_interval)
        if self.jitter:
            delay *= 0.5 + random.random() * 0.5
        return delay


async def execute_with_retry(
    func: Callable[..., Awaitable[Any]],
    policy: RetryPolicy,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute a function with retry policy.

    Args:
        func: Async function to execute.
        policy: RetryPolicy to apply.
        *args: Positional arguments.
        **kwargs: Keyword arguments.

    Returns:
        Function result.

    Raises:
        Exception: The last exception if all retries exhausted.
    """
    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if not policy.should_retry(exc, attempt):
                raise
            delay = policy.get_delay(attempt)
            logger.warning(
                "Retry %d/%d after %.1fs: %s",
                attempt, policy.max_attempts, delay, exc,
            )
            await asyncio.sleep(delay)
    raise last_error  # type: ignore[misc]


class CachePolicy(BaseModel):
    """Cache configuration for step or tool results.

    Controls TTL, capacity, eviction strategy, and provides
    a consistent hashing function for cache keys.

    Attributes:
        enabled: Whether caching is active.
        ttl_seconds: Time-to-live for cached results.
        max_entries: Maximum cached entries before eviction.
        eviction_strategy: How to evict — ``lru`` or ``oldest``.
        hash_inputs: Whether to hash key inputs for uniformity.
    """

    enabled: bool = True
    ttl_seconds: int = 3600
    max_entries: int = 1000
    eviction_strategy: str = "lru"
    hash_inputs: bool = True

    def make_key(self, *parts: Any) -> str:
        """Generate a deterministic cache key from inputs.

        Uses SHA-256 hashing when hash_inputs is True for
        consistent, collision-resistant keys.

        Args:
            *parts: Key components to combine.

        Returns:
            Cache key string.
        """
        import hashlib
        import json

        raw = json.dumps(parts, sort_keys=True, default=str)
        if self.hash_inputs:
            return hashlib.sha256(raw.encode()).hexdigest()[:32]
        return raw


class CacheStats(BaseModel):
    """Runtime cache statistics.

    Attributes:
        hits: Number of cache hits.
        misses: Number of cache misses.
        evictions: Number of entries evicted.
        expirations: Number of entries expired on access.
    """

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def hit_rate(self) -> float:
        """Calculate the cache hit rate as a percentage.

        Returns:
            Hit rate between 0.0 and 1.0, or 0.0 if no accesses.
        """
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total


@dataclass
class _CacheEntry:
    """Internal cache entry with metadata.

    Attributes:
        value: The cached value.
        created_at: Timestamp when entry was stored.
        last_accessed: Timestamp of last access (for LRU).
        access_count: Number of times this entry was accessed.
    """

    value: Any
    created_at: float
    last_accessed: float
    access_count: int = 0


class StepCache:
    """In-memory cache with TTL, LRU eviction, and statistics.

    Provides a production-ready caching layer for step and tool
    results. Supports both LRU and oldest-first eviction.

    Args:
        policy: CachePolicy configuration.
    """

    def __init__(self, policy: CachePolicy | None = None) -> None:
        self._policy = policy or CachePolicy()
        self._cache: dict[str, _CacheEntry] = {}
        self._stats = CacheStats()

    @property
    def size(self) -> int:
        """Return the number of cached entries."""
        return len(self._cache)

    @property
    def stats(self) -> CacheStats:
        """Return current cache statistics."""
        return self._stats

    def get(self, key: str) -> Any | None:
        """Get a cached value by key.

        Handles TTL expiration and updates LRU timestamps.

        Args:
            key: Cache key.

        Returns:
            Cached value or None if miss/expired.
        """
        if not self._policy.enabled:
            return None

        entry = self._cache.get(key)
        if entry is None:
            self._stats.misses += 1
            return None

        if self._is_expired(entry):
            del self._cache[key]
            self._stats.expirations += 1
            self._stats.misses += 1
            return None

        entry.last_accessed = time.time()
        entry.access_count += 1
        self._stats.hits += 1
        return entry.value

    def put(self, key: str, value: Any) -> None:
        """Store a value in the cache.

        Evicts entries when capacity is reached using the
        configured eviction strategy.

        Args:
            key: Cache key.
            value: Value to cache.
        """
        if not self._policy.enabled:
            return
        self._evict_if_needed()
        now = time.time()
        self._cache[key] = _CacheEntry(
            value=value,
            created_at=now,
            last_accessed=now,
        )

    def invalidate(self, key: str) -> bool:
        """Remove a specific entry from the cache.

        Args:
            key: Cache key to invalidate.

        Returns:
            True if the key was found and removed.
        """
        return self._cache.pop(key, None) is not None

    def clear(self) -> None:
        """Clear all cached entries and reset statistics."""
        self._cache.clear()
        self._stats = CacheStats()

    def purge_expired(self) -> int:
        """Remove all expired entries from the cache.

        Returns:
            Number of entries removed.
        """
        expired_keys = [
            k for k, v in self._cache.items() if self._is_expired(v)
        ]
        for key in expired_keys:
            del self._cache[key]
            self._stats.expirations += 1
        return len(expired_keys)

    def _is_expired(self, entry: _CacheEntry) -> bool:
        """Check whether an entry has exceeded its TTL.

        Args:
            entry: Cache entry to check.

        Returns:
            True if expired.
        """
        return (time.time() - entry.created_at) > self._policy.ttl_seconds

    def _evict_if_needed(self) -> None:
        """Evict entries when cache is at capacity.

        Uses LRU (least recently used) or oldest-first based on
        the configured eviction strategy.
        """
        while len(self._cache) >= self._policy.max_entries:
            self._evict_one()

    def _evict_one(self) -> None:
        """Remove one entry using the configured strategy."""
        if not self._cache:
            return
        if self._policy.eviction_strategy == "lru":
            victim = min(
                self._cache,
                key=lambda k: self._cache[k].last_accessed,
            )
        else:
            victim = min(
                self._cache,
                key=lambda k: self._cache[k].created_at,
            )
        del self._cache[victim]
        self._stats.evictions += 1
