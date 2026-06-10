"""ToolRuntime — context injection for tool execution.

Provides tools with access to agent state, configuration, store,
and stream writer during execution. Passed automatically to tools
that declare a ``runtime`` parameter.

Example::

    @tool
    async def search_kb(
        query: str,
        runtime: ToolRuntime | None = None,
    ) -> str:
        store = runtime.store
        config = runtime.config
        ...

Based on: LangGraph ToolRuntime / InjectedState / InjectedStore.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolRuntime:
    """Runtime context available to tools during execution.

    Injected automatically into tools that accept a ``runtime``
    keyword argument.

    Attributes:
        state: Current agent/graph state (read-only snapshot).
        config: Execution configuration dict.
        store: Cross-thread persistent store (if available).
        stream_writer: StreamWriter for custom stream events.
        metadata: Additional runtime metadata.
    """

    state: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    store: Any = None
    stream_writer: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_user_id(self) -> str | None:
        """Extract user_id from config or state.

        Returns:
            User ID string or None.
        """
        return (
            self.config.get("user_id")
            or self.state.get("user_id")
            or self.metadata.get("user_id")
        )

    def get_thread_id(self) -> str | None:
        """Extract thread_id from config.

        Returns:
            Thread ID string or None.
        """
        return self.config.get("thread_id")


def inject_runtime(
    tool_func: Any,
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """Inject runtime into tool input if the tool accepts it.

    Checks the tool function's signature for a ``runtime``
    parameter. If present, adds the runtime to the input dict.

    Args:
        tool_func: The tool function to inspect.
        tool_input: Original tool input arguments.
        runtime: Runtime context to inject.

    Returns:
        Updated tool input dict (may include ``runtime``).
    """
    import inspect

    sig = inspect.signature(tool_func)
    if "runtime" in sig.parameters:
        return {**tool_input, "runtime": runtime}
    return tool_input


def create_runtime(
    state: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    store: Any = None,
    stream_writer: Any = None,
) -> ToolRuntime:
    """Factory function to create a ToolRuntime.

    Args:
        state: Current agent state snapshot.
        config: Execution config.
        store: Optional cross-thread store.
        stream_writer: Optional stream writer.

    Returns:
        Configured ToolRuntime instance.
    """
    return ToolRuntime(
        state=state or {},
        config=config or {},
        store=store,
        stream_writer=stream_writer,
    )
