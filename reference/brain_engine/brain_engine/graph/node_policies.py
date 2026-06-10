"""Per-node policies — retry and cache at the graph node level.

Attaches retry and cache policies to individual graph nodes,
applied automatically during Pregel execution.

Example::

    graph.add_node("llm_call", llm_node)
    attach_retry(graph, "llm_call", max_attempts=3, backoff=2.0)
    attach_cache(graph, "llm_call", ttl_seconds=300)

Based on: LangGraph RetryPolicy / CachePolicy per StateNodeSpec.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class NodeRetryPolicy:
    """Retry policy for a single graph node.

    Attributes:
        max_attempts: Maximum execution attempts.
        initial_interval: Seconds before first retry.
        backoff_factor: Multiply interval each retry.
        max_interval: Cap on retry interval.
        jitter: Add random jitter to delays.
        retry_on: Exception class names to retry on.
    """

    max_attempts: int = 3
    initial_interval: float = 0.5
    backoff_factor: float = 2.0
    max_interval: float = 60.0
    jitter: bool = True
    retry_on: list[str] = field(
        default_factory=lambda: ["Exception"],
    )

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """Check if a retry should be attempted.

        Args:
            error: The exception that occurred.
            attempt: Current attempt number (1-based).

        Returns:
            True if retry is appropriate.
        """
        if attempt >= self.max_attempts:
            return False
        error_name = type(error).__name__
        return any(
            error_name == name or name == "Exception"
            for name in self.retry_on
        )

    def get_delay(self, attempt: int) -> float:
        """Calculate delay before next retry.

        Args:
            attempt: Current attempt (1-based).

        Returns:
            Delay in seconds.
        """
        delay = self.initial_interval * (
            self.backoff_factor ** (attempt - 1)
        )
        delay = min(delay, self.max_interval)
        if self.jitter:
            delay *= 0.5 + random.random() * 0.5
        return delay


@dataclass
class NodeCachePolicy:
    """Cache policy for a single graph node.

    Caches node output based on input hash. Same input
    produces the same output without re-execution.

    Attributes:
        enabled: Whether caching is active.
        ttl_seconds: Time-to-live for cached results.
        max_entries: Maximum cached entries.
    """

    enabled: bool = True
    ttl_seconds: int = 600
    max_entries: int = 100


class NodePolicyRegistry:
    """Registry for per-node retry and cache policies.

    Stores policies keyed by node name and provides execution
    wrappers that apply the policies automatically.
    """

    def __init__(self) -> None:
        self._retry: dict[str, NodeRetryPolicy] = {}
        self._cache: dict[str, NodeCachePolicy] = {}
        self._cache_store: dict[str, dict[str, tuple[Any, float]]] = {}

    def set_retry(
        self,
        node_name: str,
        policy: NodeRetryPolicy,
    ) -> None:
        """Attach a retry policy to a node.

        Args:
            node_name: Target node name.
            policy: Retry policy to attach.
        """
        self._retry[node_name] = policy

    def set_cache(
        self,
        node_name: str,
        policy: NodeCachePolicy,
    ) -> None:
        """Attach a cache policy to a node.

        Args:
            node_name: Target node name.
            policy: Cache policy to attach.
        """
        self._cache[node_name] = policy
        self._cache_store.setdefault(node_name, {})

    def get_retry(self, node_name: str) -> NodeRetryPolicy | None:
        """Get retry policy for a node.

        Args:
            node_name: Node name.

        Returns:
            Policy or None.
        """
        return self._retry.get(node_name)

    def get_cache(self, node_name: str) -> NodeCachePolicy | None:
        """Get cache policy for a node.

        Args:
            node_name: Node name.

        Returns:
            Policy or None.
        """
        return self._cache.get(node_name)

    async def execute_with_policies(
        self,
        node_name: str,
        func: Callable[..., Any],
        input_data: Any,
    ) -> Any:
        """Execute a node function with retry and cache policies.

        Checks cache first, then executes with retry on miss.
        Stores result in cache on success.

        Args:
            node_name: Node being executed.
            func: Node function.
            input_data: Input to the node.

        Returns:
            Node output (from cache or fresh execution).
        """
        cache_policy = self._cache.get(node_name)
        if cache_policy and cache_policy.enabled:
            cached = self._check_cache(node_name, input_data, cache_policy)
            if cached is not None:
                logger.debug("Cache hit for node '%s'", node_name)
                return cached

        result = await self._execute_with_retry(node_name, func, input_data)

        if cache_policy and cache_policy.enabled:
            self._store_cache(node_name, input_data, result, cache_policy)

        return result

    async def _execute_with_retry(
        self,
        node_name: str,
        func: Callable[..., Any],
        input_data: Any,
    ) -> Any:
        """Execute with retry policy if configured.

        Args:
            node_name: Node name.
            func: Node function.
            input_data: Node input.

        Returns:
            Node output.
        """
        policy = self._retry.get(node_name)
        if policy is None:
            return await _invoke(func, input_data)

        last_error: Exception | None = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return await _invoke(func, input_data)
            except Exception as exc:
                last_error = exc
                if not policy.should_retry(exc, attempt):
                    raise
                delay = policy.get_delay(attempt)
                logger.warning(
                    "Node '%s' attempt %d/%d failed: %s. "
                    "Retry in %.1fs",
                    node_name, attempt, policy.max_attempts,
                    exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    def _check_cache(
        self,
        node_name: str,
        input_data: Any,
        policy: NodeCachePolicy,
    ) -> Any | None:
        """Check cache for a stored result.

        Args:
            node_name: Node name.
            input_data: Input to hash for cache key.
            policy: Cache policy with TTL.

        Returns:
            Cached value or None.
        """
        store = self._cache_store.get(node_name, {})
        key = _hash_input(input_data)
        entry = store.get(key)
        if entry is None:
            return None
        value, timestamp = entry
        if (time.time() - timestamp) > policy.ttl_seconds:
            del store[key]
            return None
        return value

    def _store_cache(
        self,
        node_name: str,
        input_data: Any,
        result: Any,
        policy: NodeCachePolicy,
    ) -> None:
        """Store a result in the node cache.

        Args:
            node_name: Node name.
            input_data: Input (for key hashing).
            result: Value to cache.
            policy: Cache policy with max_entries.
        """
        store = self._cache_store.setdefault(node_name, {})
        if len(store) >= policy.max_entries:
            oldest_key = min(store, key=lambda k: store[k][1])
            del store[oldest_key]
        key = _hash_input(input_data)
        store[key] = (result, time.time())


def attach_retry(
    graph: Any,
    node_name: str,
    *,
    max_attempts: int = 3,
    backoff: float = 2.0,
    jitter: bool = True,
) -> NodeRetryPolicy:
    """Convenience: attach retry policy to a graph node.

    Args:
        graph: StateGraph or registry holder.
        node_name: Target node.
        max_attempts: Max retries.
        backoff: Backoff factor.
        jitter: Enable jitter.

    Returns:
        The created policy.
    """
    policy = NodeRetryPolicy(
        max_attempts=max_attempts,
        backoff_factor=backoff,
        jitter=jitter,
    )
    registry = _get_or_create_registry(graph)
    registry.set_retry(node_name, policy)
    return policy


def attach_cache(
    graph: Any,
    node_name: str,
    *,
    ttl_seconds: int = 600,
    max_entries: int = 100,
) -> NodeCachePolicy:
    """Convenience: attach cache policy to a graph node.

    Args:
        graph: StateGraph or registry holder.
        node_name: Target node.
        ttl_seconds: Cache TTL.
        max_entries: Max cached entries.

    Returns:
        The created policy.
    """
    policy = NodeCachePolicy(
        ttl_seconds=ttl_seconds,
        max_entries=max_entries,
    )
    registry = _get_or_create_registry(graph)
    registry.set_cache(node_name, policy)
    return policy


def _get_or_create_registry(graph: Any) -> NodePolicyRegistry:
    """Get or create a NodePolicyRegistry on a graph.

    Args:
        graph: Graph object.

    Returns:
        NodePolicyRegistry instance.
    """
    if not hasattr(graph, "_node_policies"):
        graph._node_policies = NodePolicyRegistry()
    return graph._node_policies


async def _invoke(func: Callable[..., Any], input_data: Any) -> Any:
    """Invoke a function (sync or async).

    Args:
        func: Function to call.
        input_data: Input argument.

    Returns:
        Function result.
    """
    result = func(input_data)
    if asyncio.iscoroutine(result):
        return await result
    return result


def _hash_input(data: Any) -> str:
    """Hash input data for cache key.

    Args:
        data: Data to hash.

    Returns:
        SHA-256 hash prefix (16 chars).
    """
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
