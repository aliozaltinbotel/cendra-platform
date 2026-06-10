"""Callback Protocol — defines the observability hook interface.

All callbacks implement this protocol. The CallbackManager dispatches
events to registered callbacks in order. Hooks are optional — only
implement what you need.

Inspired by LangChain's BaseCallbackHandler with parent/child run IDs.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from brain_engine.observability.models import RunContext


@runtime_checkable
class CallbackProtocol(Protocol):
    """Protocol for observability callbacks.

    All methods are optional. Implement only the hooks you need.
    Each hook receives a RunContext for parent/child tracking.
    """

    @property
    def name(self) -> str:
        """Unique callback name for identification."""
        ...

    async def on_llm_start(
        self,
        ctx: RunContext,
        prompts: list[dict[str, str]],
        **kwargs: Any,
    ) -> None:
        """Called when an LLM call begins.

        Args:
            ctx: Run context with parent/child IDs.
            prompts: Input messages for the LLM.
            **kwargs: Additional model parameters.
        """
        ...

    async def on_llm_end(
        self,
        ctx: RunContext,
        response: Any,
        **kwargs: Any,
    ) -> None:
        """Called when an LLM call completes.

        Args:
            ctx: Run context.
            response: Model response object.
            **kwargs: Additional data.
        """
        ...

    async def on_llm_error(
        self,
        ctx: RunContext,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Called when an LLM call raises an exception.

        Args:
            ctx: Run context.
            error: The exception raised.
            **kwargs: Additional data.
        """
        ...

    async def on_llm_new_token(
        self,
        ctx: RunContext,
        token: str,
        **kwargs: Any,
    ) -> None:
        """Called for each streamed token from the LLM.

        Args:
            ctx: Run context.
            token: The new token string.
            **kwargs: Additional data.
        """
        ...

    async def on_tool_start(
        self,
        ctx: RunContext,
        tool_name: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Called when a tool execution begins.

        Args:
            ctx: Run context.
            tool_name: Name of the tool.
            tool_input: Tool input arguments.
            **kwargs: Additional data.
        """
        ...

    async def on_tool_end(
        self,
        ctx: RunContext,
        tool_name: str,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Called when a tool execution completes.

        Args:
            ctx: Run context.
            tool_name: Name of the tool.
            output: Tool output.
            **kwargs: Additional data.
        """
        ...

    async def on_tool_error(
        self,
        ctx: RunContext,
        tool_name: str,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Called when a tool execution fails.

        Args:
            ctx: Run context.
            tool_name: Name of the tool.
            error: The exception raised.
            **kwargs: Additional data.
        """
        ...

    async def on_agent_action(
        self,
        ctx: RunContext,
        action: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Called when the agent decides on an action.

        Args:
            ctx: Run context.
            action: The chosen action/tool name.
            tool_input: Action input.
            **kwargs: Additional data.
        """
        ...

    async def on_agent_finish(
        self,
        ctx: RunContext,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Called when the agent completes its task.

        Args:
            ctx: Run context.
            output: Final agent output.
            **kwargs: Additional data.
        """
        ...
