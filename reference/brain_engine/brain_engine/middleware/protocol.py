"""Protocol definition for Brain Engine middleware components.

Each middleware implements hooks that run before/after model calls,
around tool calls, and during prompt assembly. All hooks are optional —
unimplemented hooks pass through transparently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool definition exposed by middleware.

    Attributes:
        name: Unique tool identifier.
        description: Human-readable tool description.
        parameters: JSON Schema for tool parameters.
        handler: Async callable that executes the tool.
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: Callable[..., Awaitable[Any]] | None = None


@dataclass(slots=True)
class ToolRequest:
    """A request to execute a tool.

    Attributes:
        name: Tool name to invoke.
        args: Arguments to pass to the tool.
        call_id: Provider-assigned tool call identifier.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@runtime_checkable
class MiddlewareProtocol(Protocol):
    """Protocol that all middleware components must satisfy.

    All methods have default no-op behavior — middleware only needs
    to implement the hooks it cares about.
    """

    @property
    def name(self) -> str:
        """Return the middleware's unique name."""
        ...

    def get_tools(self) -> list[Tool]:
        """Return tools provided by this middleware.

        Returns:
            List of ``Tool`` definitions (empty if none).
        """
        ...

    def get_prompt_additions(self) -> str:
        """Return text to append to the system prompt.

        Returns:
            Additional system prompt text (empty string if none).
        """
        ...

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Hook called before sending messages to the LLM.

        Args:
            messages: Current message list.

        Returns:
            Potentially modified message list.
        """
        ...

    async def post_model_call(
        self,
        response: Any,
    ) -> Any:
        """Hook called after receiving the LLM response.

        Args:
            response: Raw model response.

        Returns:
            Potentially modified response.
        """
        ...

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Hook that wraps tool execution.

        Args:
            request: The tool call request.
            handler: The next handler in the chain (call to proceed).

        Returns:
            Tool execution result.
        """
        ...
