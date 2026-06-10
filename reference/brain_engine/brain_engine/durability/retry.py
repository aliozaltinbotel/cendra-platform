"""Retry Policy — exponential backoff with jitter for Brain Engine.

Inspired by LangGraph's RetryPolicy, adapted for async-first usage.
Provides both a dataclass for configuration and a decorator for
wrapping async functions with automatic retry logic.

Usage:
    # As decorator
    @with_retry(RetryPolicy(max_attempts=3))
    async def call_llm(prompt: str) -> str:
        ...

    # As context
    policy = RetryPolicy(max_attempts=3, retry_on=(TimeoutError,))
    result = await retry_async(policy, call_llm, prompt="hello")
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Configuration for retry behavior with exponential backoff.

    Attributes:
        max_attempts: Total attempts including the initial call.
        initial_interval: Seconds before first retry.
        backoff_factor: Multiplier for each subsequent retry.
        max_interval: Maximum seconds between retries.
        jitter: Whether to add randomness to intervals.
        retry_on: Exception types that trigger a retry.
    """

    max_attempts: int = 3
    initial_interval: float = 0.5
    backoff_factor: float = 2.0
    max_interval: float = 30.0
    jitter: bool = True
    retry_on: tuple[type[Exception], ...] = (Exception,)


# ── Pre-built policies ───────────────────────────────────────────── #

LLM_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    max_interval=30.0,
    retry_on=(TimeoutError, ConnectionError),
)

REDIS_RETRY = RetryPolicy(
    max_attempts=5,
    initial_interval=0.2,
    backoff_factor=2.0,
    max_interval=10.0,
    retry_on=(ConnectionError, OSError),
)

QDRANT_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    max_interval=15.0,
    retry_on=(ConnectionError, TimeoutError),
)


def _should_retry(policy: RetryPolicy, exc: Exception) -> bool:
    """Check if exception matches retry policy.

    Args:
        policy: Retry configuration.
        exc: The exception that occurred.

    Returns:
        True if the exception should trigger a retry.
    """
    return isinstance(exc, policy.retry_on)


def _calculate_delay(
    policy: RetryPolicy,
    attempt: int,
) -> float:
    """Calculate delay before next retry attempt.

    Uses exponential backoff: base * (factor ^ attempt).
    Adds jitter (0-1s) if enabled to prevent thundering herd.

    Args:
        policy: Retry configuration.
        attempt: Current attempt number (0-based).

    Returns:
        Delay in seconds before next retry.
    """
    delay = policy.initial_interval * (policy.backoff_factor ** attempt)
    delay = min(delay, policy.max_interval)
    if policy.jitter:
        delay += random.uniform(0, 1)
    return delay


async def retry_async(
    policy: RetryPolicy,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute async function with retry logic.

    Args:
        policy: Retry configuration.
        func: Async function to call.
        *args: Positional arguments for func.
        **kwargs: Keyword arguments for func.

    Returns:
        Result of successful function call.

    Raises:
        Exception: Last exception if all attempts exhausted.
    """
    last_exc: Exception | None = None

    for attempt in range(policy.max_attempts):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _should_retry(policy, exc):
                raise

            remaining = policy.max_attempts - attempt - 1
            if remaining <= 0:
                raise

            delay = _calculate_delay(policy, attempt)
            logger.warning(
                "Retry %d/%d for %s after %.1fs: %s",
                attempt + 1,
                policy.max_attempts,
                func.__name__,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]


def with_retry(
    policy: RetryPolicy | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that adds retry logic to async functions.

    Args:
        policy: Retry configuration. Defaults to LLM_RETRY.

    Returns:
        Decorator function.
    """
    effective_policy = policy or LLM_RETRY

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await retry_async(
                effective_policy, func, *args, **kwargs,
            )
        return wrapper

    return decorator
