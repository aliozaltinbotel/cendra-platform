"""Call limit middleware — enforce budgets on model and tool calls.

Prevents runaway agents from making unlimited LLM or tool calls.
Tracks call counts per session and raises when limits are exceeded.

Based on: LangChain ModelCallLimitMiddleware + ToolCallLimitMiddleware.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CallLimitExceeded(Exception):
    """Raised when a call limit is exceeded.

    Attributes:
        limit_type: What was limited (model or tool).
        limit: The configured maximum.
        current: Current count when limit hit.
    """

    def __init__(
        self,
        limit_type: str,
        limit: int,
        current: int,
    ) -> None:
        self.limit_type = limit_type
        self.limit = limit
        self.current = current
        super().__init__(
            f"{limit_type} call limit exceeded: "
            f"{current}/{limit}"
        )


class ModelCallLimitMiddleware:
    """Limits the total number of LLM model calls per session.

    Counts every ``pre_model_call`` invocation. When the limit
    is reached, raises ``CallLimitExceeded`` to stop execution.

    Args:
        max_calls: Maximum model calls allowed.
    """

    def __init__(self, max_calls: int = 50) -> None:
        self._max_calls = max_calls
        self._call_count: int = 0

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "model_call_limit"

    @property
    def call_count(self) -> int:
        """Current model call count."""
        return self._call_count

    @property
    def remaining(self) -> int:
        """Remaining calls before limit."""
        return max(0, self._max_calls - self._call_count)

    def get_tools(self) -> list[dict[str, Any]]:
        """No tools."""
        return []

    def get_prompt_additions(self) -> str:
        """Inject remaining budget into prompt."""
        if self.remaining < 10:
            return (
                f"\n[BUDGET WARNING: {self.remaining} model calls "
                f"remaining out of {self._max_calls}. "
                f"Be concise and efficient.]\n"
            )
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Increment counter and check limit.

        Args:
            messages: Input messages.

        Returns:
            Unmodified messages.

        Raises:
            CallLimitExceeded: If limit reached.
        """
        self._call_count += 1
        if self._call_count > self._max_calls:
            raise CallLimitExceeded(
                "model", self._max_calls, self._call_count,
            )
        logger.debug(
            "Model call %d/%d", self._call_count, self._max_calls,
        )
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Pass through."""
        return response

    def reset(self) -> None:
        """Reset the call counter."""
        self._call_count = 0


class ToolCallLimitMiddleware:
    """Limits the total number of tool calls per session.

    Tracks tool invocations across all tools. Optionally applies
    per-tool limits for specific expensive tools.

    Args:
        max_calls: Global maximum tool calls.
        per_tool_limits: Optional per-tool maximums.
    """

    def __init__(
        self,
        max_calls: int = 100,
        per_tool_limits: dict[str, int] | None = None,
    ) -> None:
        self._max_calls = max_calls
        self._per_tool_limits = per_tool_limits or {}
        self._total_count: int = 0
        self._per_tool_counts: dict[str, int] = {}

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "tool_call_limit"

    @property
    def total_calls(self) -> int:
        """Total tool calls made."""
        return self._total_count

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
        """Count tool calls in model response.

        Args:
            response: Model response (may contain tool_calls).

        Returns:
            Unmodified response.

        Raises:
            CallLimitExceeded: If limit reached.
        """
        tool_calls = getattr(response, "tool_calls", [])
        for tc in tool_calls:
            tool_name = getattr(tc, "name", "unknown")
            self._count_tool_call(tool_name)
        return response

    def _count_tool_call(self, tool_name: str) -> None:
        """Increment counters and check limits.

        Args:
            tool_name: Name of the tool called.

        Raises:
            CallLimitExceeded: If any limit exceeded.
        """
        self._total_count += 1
        self._per_tool_counts[tool_name] = (
            self._per_tool_counts.get(tool_name, 0) + 1
        )

        if self._total_count > self._max_calls:
            raise CallLimitExceeded(
                "tool (global)", self._max_calls, self._total_count,
            )

        per_limit = self._per_tool_limits.get(tool_name)
        if per_limit and self._per_tool_counts[tool_name] > per_limit:
            raise CallLimitExceeded(
                f"tool ({tool_name})",
                per_limit,
                self._per_tool_counts[tool_name],
            )

    def reset(self) -> None:
        """Reset all counters."""
        self._total_count = 0
        self._per_tool_counts.clear()
