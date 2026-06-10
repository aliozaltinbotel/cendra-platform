"""Logging middleware — traces all pipeline activity.

Records message counts, model call timing, and tool executions
to the standard Python logger.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import (
    MiddlewareProtocol,
    Tool,
    ToolRequest,
)

logger = logging.getLogger(__name__)


class LoggingMiddleware:
    """Middleware that logs all pipeline events.

    Logs message counts before model calls, response sizes after,
    and tool call timing around tool executions.
    """

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "logging"

    def get_tools(self) -> list[Tool]:
        """Return empty tool list — logging provides no tools."""
        return []

    def get_prompt_additions(self) -> str:
        """Return empty prompt — logging adds nothing to prompts."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Log message count before model call.

        Args:
            messages: Current message list.

        Returns:
            Unmodified message list.
        """
        logger.info(
            "[LoggingMW] pre_model_call: %d messages", len(messages),
        )
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Log response type after model call.

        Args:
            response: Raw model response.

        Returns:
            Unmodified response.
        """
        resp_type = type(response).__name__
        logger.info("[LoggingMW] post_model_call: type=%s", resp_type)
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Log tool execution timing.

        Args:
            request: Tool call request.
            handler: Next handler in the chain.

        Returns:
            Tool execution result.
        """
        start = time.monotonic()
        logger.info(
            "[LoggingMW] tool_call start: %s", request.name,
        )
        result = await handler(request)
        elapsed = int((time.monotonic() - start) * 1000)
        logger.info(
            "[LoggingMW] tool_call done: %s (%dms)",
            request.name, elapsed,
        )
        return result
