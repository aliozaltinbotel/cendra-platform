"""Caching middleware — caches identical LLM requests.

Provides deterministic response caching for repeated LLM calls
with identical messages. Uses an in-memory LRU cache with
configurable TTL and max entries.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest


class CachingMiddleware:
    """Middleware that caches LLM responses for identical inputs.

    Uses MD5 hash of messages as cache key. Respects TTL and
    max cache size.

    Args:
        max_entries: Maximum cache entries.
        ttl_seconds: Time-to-live for cached responses.
    """

    def __init__(
        self,
        max_entries: int = 100,
        ttl_seconds: float = 300.0,
    ) -> None:
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[Any, float]] = {}
        self._pending_key: str | None = None
        self._hit_count: int = 0
        self._miss_count: int = 0

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "caching"

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0 to 1.0)."""
        total = self._hit_count + self._miss_count
        if total == 0:
            return 0.0
        return self._hit_count / total

    def get_tools(self) -> list[Tool]:
        """No tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """No prompt additions."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Check cache and set pending key for post_model_call.

        Args:
            messages: Current message list.

        Returns:
            Unmodified messages (cache hit is handled at pipeline level).
        """
        key = _compute_key(messages)
        self._pending_key = key
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Store response in cache if key is pending.

        Args:
            response: Model response.

        Returns:
            Unmodified response.
        """
        if self._pending_key:
            self._store(self._pending_key, response)
            self._pending_key = None
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Pass through tool calls unchanged."""
        return await handler(request)

    def lookup(self, messages: list[dict[str, Any]]) -> Any | None:
        """Look up a cached response for given messages.

        Args:
            messages: Message list to hash.

        Returns:
            Cached response or None.
        """
        key = _compute_key(messages)
        entry = self._cache.get(key)

        if entry is None:
            self._miss_count += 1
            return None

        value, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._cache[key]
            self._miss_count += 1
            return None

        self._hit_count += 1
        return value

    def _store(self, key: str, value: Any) -> None:
        """Store a value in the cache with eviction.

        Args:
            key: Cache key.
            value: Response to cache.
        """
        if len(self._cache) >= self._max_entries:
            self._evict_oldest()
        self._cache[key] = (value, time.monotonic())

    def _evict_oldest(self) -> None:
        """Remove the oldest cache entry."""
        if not self._cache:
            return
        oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
        del self._cache[oldest_key]

    def clear(self) -> None:
        """Clear all cached entries and reset counters."""
        self._cache.clear()
        self._hit_count = 0
        self._miss_count = 0


def _compute_key(messages: list[dict[str, Any]]) -> str:
    """Compute a deterministic cache key from messages.

    Args:
        messages: Message list to hash.

    Returns:
        MD5 hex digest.
    """
    content = json.dumps(messages, sort_keys=True, default=str)
    return hashlib.md5(content.encode()).hexdigest()
