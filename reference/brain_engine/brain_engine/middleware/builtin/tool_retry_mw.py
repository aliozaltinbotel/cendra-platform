"""ToolRetryMiddleware — automatic retry for failed tool calls.

Retries tool executions on transient failures with exponential
backoff. Configurable per-tool retry policies and exception filters.

Based on: LangChain ToolRetryMiddleware concept.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class ToolRetryMiddleware:
    """Middleware that retries failed tool calls with backoff.

    Wraps tool execution to catch transient errors and retry
    automatically. Tracks retry statistics for observability.

    Args:
        max_retries: Default maximum retries per tool call.
        initial_delay: Seconds before first retry.
        backoff_factor: Multiply delay each retry.
        max_delay: Cap on delay between retries.
        jitter: Add random jitter to prevent thundering herd.
        retry_on: Exception types to retry on (default: all).
        per_tool_retries: Override max_retries for specific tools.
    """

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 0.5,
        backoff_factor: float = 2.0,
        max_delay: float = 30.0,
        jitter: bool = True,
        retry_on: tuple[type[Exception], ...] | None = None,
        per_tool_retries: dict[str, int] | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._initial_delay = initial_delay
        self._backoff_factor = backoff_factor
        self._max_delay = max_delay
        self._jitter = jitter
        self._retry_on = retry_on or (Exception,)
        self._per_tool_retries = per_tool_retries or {}
        self._retry_stats: dict[str, int] = {}
        self._total_retries: int = 0

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "tool_retry"

    @property
    def total_retries(self) -> int:
        """Total retries across all tools."""
        return self._total_retries

    @property
    def retry_stats(self) -> dict[str, int]:
        """Per-tool retry counts."""
        return dict(self._retry_stats)

    def get_tools(self) -> list[dict[str, Any]]:
        """No tools."""
        return []

    def get_prompt_additions(self) -> str:
        """No additions."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Pass through."""
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Pass through."""
        return response

    async def execute_tool_with_retry(
        self,
        tool_name: str,
        tool_func: Callable[..., Awaitable[Any]],
        tool_input: dict[str, Any],
    ) -> Any:
        """Execute a tool with automatic retry on failure.

        Args:
            tool_name: Name of the tool being called.
            tool_func: Async function to execute.
            tool_input: Arguments for the tool.

        Returns:
            Tool execution result.

        Raises:
            Exception: The last exception if all retries fail.
        """
        max_attempts = self._get_max_retries(tool_name)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                return await tool_func(**tool_input)
            except self._retry_on as exc:
                last_error = exc
                if attempt >= max_attempts:
                    break
                delay = self._compute_delay(attempt)
                self._record_retry(tool_name)
                logger.warning(
                    "Tool '%s' attempt %d/%d failed: %s. "
                    "Retrying in %.1fs",
                    tool_name, attempt, max_attempts,
                    exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    def _get_max_retries(self, tool_name: str) -> int:
        """Get the max retries for a specific tool.

        Args:
            tool_name: Tool name.

        Returns:
            Max retry count.
        """
        return self._per_tool_retries.get(
            tool_name, self._max_retries,
        )

    def _compute_delay(self, attempt: int) -> float:
        """Calculate delay before next retry.

        Args:
            attempt: Current attempt number (1-based).

        Returns:
            Delay in seconds.
        """
        delay = self._initial_delay * (
            self._backoff_factor ** (attempt - 1)
        )
        delay = min(delay, self._max_delay)
        if self._jitter:
            delay *= 0.5 + random.random() * 0.5
        return delay

    def _record_retry(self, tool_name: str) -> None:
        """Record a retry event for statistics.

        Args:
            tool_name: Tool that was retried.
        """
        self._total_retries += 1
        self._retry_stats[tool_name] = (
            self._retry_stats.get(tool_name, 0) + 1
        )

    def reset(self) -> None:
        """Reset all retry statistics."""
        self._total_retries = 0
        self._retry_stats.clear()
