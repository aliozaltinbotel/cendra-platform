"""Middleware stack — ordered execution of middleware hooks.

Manages a list of middleware components and orchestrates their
hooks around model calls, tool calls, and prompt assembly.
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


class MiddlewareStack:
    """Ordered stack of middleware components.

    Middleware is executed in insertion order for pre-hooks and
    reverse order for post-hooks (onion model).
    """

    def __init__(self) -> None:
        """Initialize MiddlewareStack."""
        self._middlewares: list[MiddlewareProtocol] = []

    @property
    def count(self) -> int:
        """Return the number of registered middlewares."""
        return len(self._middlewares)

    @property
    def names(self) -> list[str]:
        """Return ordered list of middleware names."""
        return [mw.name for mw in self._middlewares]

    def add(self, middleware: MiddlewareProtocol) -> None:
        """Add a middleware to the end of the stack.

        Args:
            middleware: Middleware component to register.
        """
        self._middlewares.append(middleware)
        logger.debug("Added middleware: %s", middleware.name)

    def remove(self, name: str) -> bool:
        """Remove a middleware by name.

        Args:
            name: Name of the middleware to remove.

        Returns:
            ``True`` if removed, ``False`` if not found.
        """
        for i, mw in enumerate(self._middlewares):
            if mw.name == name:
                self._middlewares.pop(i)
                logger.debug("Removed middleware: %s", name)
                return True
        return False

    def collect_tools(self) -> list[Tool]:
        """Gather all tools from all middleware.

        Returns:
            Combined list of ``Tool`` definitions.
        """
        tools: list[Tool] = []
        for mw in self._middlewares:
            mw_tools = mw.get_tools()
            if mw_tools:
                tools.extend(mw_tools)
        return tools

    def build_prompt_additions(self) -> str:
        """Concatenate prompt additions from all middleware.

        Returns:
            Combined prompt addition text.
        """
        parts: list[str] = []
        for mw in self._middlewares:
            addition = mw.get_prompt_additions()
            if addition:
                parts.append(addition)
        return "\n\n".join(parts)

    async def run_pre_model(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Run all pre_model_call hooks in order.

        Args:
            messages: Original message list.

        Returns:
            Message list after all middleware modifications.
        """
        result = messages
        for mw in self._middlewares:
            result = await mw.pre_model_call(result)
        return result

    async def run_post_model(self, response: Any) -> Any:
        """Run all post_model_call hooks in reverse order.

        Args:
            response: Raw model response.

        Returns:
            Response after all middleware modifications.
        """
        result = response
        for mw in reversed(self._middlewares):
            result = await mw.post_model_call(result)
        return result

    async def run_tool_call(
        self,
        request: ToolRequest,
        final_handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Execute a tool call through the middleware chain.

        Each middleware wraps the next, forming an onion of handlers.
        The innermost handler is ``final_handler``.

        Args:
            request: Tool call request.
            final_handler: The actual tool execution function.

        Returns:
            Tool execution result.
        """
        chain = self._build_tool_chain(final_handler)
        return await chain(request)

    def _build_tool_chain(
        self,
        final_handler: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        """Build the nested tool call handler chain.

        Args:
            final_handler: Innermost handler (actual tool execution).

        Returns:
            Outermost handler with all middleware wrapping.
        """
        handler = _wrap_final(final_handler)

        for mw in reversed(self._middlewares):
            handler = _wrap_middleware(mw, handler)

        return handler

    async def execute_pipeline(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        model_caller: Callable[..., Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute the full middleware pipeline.

        Runs pre-hooks, calls the model, runs post-hooks, and returns
        the result with timing metadata.

        Args:
            session_id: Current session identifier.
            messages: Input messages.
            model_caller: Async function to call the LLM. If ``None``,
                returns messages after pre-processing only.

        Returns:
            Dict with ``response``, ``messages``, and ``elapsed_ms``.
        """
        start = time.monotonic()
        processed = await self.run_pre_model(messages)

        response = None
        if model_caller:
            response = await model_caller(processed)
            response = await self.run_post_model(response)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return _build_result(processed, response, session_id, elapsed_ms)


def _wrap_final(
    handler: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Wrap the final handler to accept a ToolRequest.

    Args:
        handler: The actual tool execution function.

    Returns:
        Wrapped async function.
    """
    async def wrapped(request: ToolRequest) -> Any:
        """Forward request to the final handler."""
        return await handler(request)
    return wrapped


def _wrap_middleware(
    mw: MiddlewareProtocol,
    next_handler: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Wrap a middleware around the next handler in the chain.

    Args:
        mw: Middleware to wrap.
        next_handler: Next handler in the chain.

    Returns:
        Wrapped async function.
    """
    async def wrapped(request: ToolRequest) -> Any:
        """Delegate to middleware wrap_tool_call hook."""
        return await mw.wrap_tool_call(request, next_handler)
    return wrapped


def _build_result(
    messages: list[dict[str, Any]],
    response: Any,
    session_id: str,
    elapsed_ms: int,
) -> dict[str, Any]:
    """Build the pipeline result dict.

    Args:
        messages: Processed messages.
        response: Model response (or None).
        session_id: Session identifier.
        elapsed_ms: Elapsed time in milliseconds.

    Returns:
        Result dict with all pipeline outputs.
    """
    return {
        "session_id": session_id,
        "messages": messages,
        "response": response,
        "elapsed_ms": elapsed_ms,
    }
