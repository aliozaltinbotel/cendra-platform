"""Rate limit middleware — throttles LLM and tool calls.

Implements a sliding window rate limiter per session to prevent
runaway costs and API abuse. Configurable limits per minute.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest


class RateLimitError(Exception):
    """Raised when rate limit is exceeded."""


class RateLimitMiddleware:
    """Middleware that enforces rate limits on LLM and tool calls.

    Uses a sliding window counter per call type.

    Args:
        llm_calls_per_minute: Max LLM calls per 60-second window.
        tool_calls_per_minute: Max tool calls per 60-second window.
    """

    def __init__(
        self,
        llm_calls_per_minute: int = 30,
        tool_calls_per_minute: int = 60,
    ) -> None:
        self._llm_limit = llm_calls_per_minute
        self._tool_limit = tool_calls_per_minute
        self._llm_window: deque[float] = deque()
        self._tool_window: deque[float] = deque()

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "rate_limit"

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
        """Check LLM call rate limit before proceeding.

        Args:
            messages: Current message list.

        Returns:
            Unmodified messages.

        Raises:
            RateLimitError: If limit exceeded.
        """
        _check_window(self._llm_window, self._llm_limit, "LLM")
        self._llm_window.append(time.monotonic())
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Pass through — no post-processing."""
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Check tool call rate limit before proceeding.

        Args:
            request: Tool call request.
            handler: Next handler in chain.

        Returns:
            Tool result.

        Raises:
            RateLimitError: If limit exceeded.
        """
        _check_window(self._tool_window, self._tool_limit, "tool")
        self._tool_window.append(time.monotonic())
        return await handler(request)


def _check_window(
    window: deque[float],
    limit: int,
    call_type: str,
) -> None:
    """Check if a sliding window rate limit has been exceeded.

    Args:
        window: Deque of timestamps.
        limit: Max calls per 60-second window.
        call_type: Label for error messages.

    Raises:
        RateLimitError: If limit exceeded.
    """
    now = time.monotonic()
    cutoff = now - 60.0

    while window and window[0] < cutoff:
        window.popleft()

    if len(window) >= limit:
        raise RateLimitError(
            f"{call_type} rate limit exceeded: {limit}/min"
        )
