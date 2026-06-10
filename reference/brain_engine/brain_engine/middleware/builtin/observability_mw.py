"""Observability middleware — integrates callback system with pipeline.

Dispatches observability events (on_llm_start, on_llm_end, etc.)
through the CallbackManager at each pipeline stage.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest
from brain_engine.observability.manager import CallbackManager
from brain_engine.observability.models import RunContext

logger = logging.getLogger(__name__)


class ObservabilityMiddleware:
    """Middleware that emits observability events through CallbackManager.

    Creates child RunContexts for each LLM and tool call,
    dispatching start/end/error events.

    Args:
        callback_manager: The callback manager instance.
        parent_context: Root run context for hierarchy.
    """

    def __init__(
        self,
        callback_manager: CallbackManager,
        parent_context: RunContext | None = None,
    ) -> None:
        self._manager = callback_manager
        self._parent = parent_context
        self._llm_ctx: RunContext | None = None

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "observability"

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
        """Emit on_llm_start event.

        Args:
            messages: Current message list.

        Returns:
            Unmodified messages.
        """
        self._llm_ctx = self._manager.create_run_context(
            "llm_call", "llm", parent=self._parent,
        )
        await self._manager.on_llm_start(self._llm_ctx, messages)
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Emit on_llm_end event.

        Args:
            response: Model response.

        Returns:
            Unmodified response.
        """
        if self._llm_ctx:
            await self._manager.on_llm_end(self._llm_ctx, response)
            self._llm_ctx = None
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Emit tool start/end/error events around execution.

        Args:
            request: Tool call request.
            handler: Next handler in chain.

        Returns:
            Tool result.
        """
        tool_ctx = self._manager.create_run_context(
            request.name, "tool", parent=self._parent,
        )
        await self._manager.on_tool_start(
            tool_ctx, request.name, request.args,
        )

        try:
            result = await handler(request)
            await self._manager.on_tool_end(
                tool_ctx, request.name, str(result),
            )
            return result
        except Exception as exc:
            await self._manager.on_tool_error(
                tool_ctx, request.name, exc,
            )
            raise
