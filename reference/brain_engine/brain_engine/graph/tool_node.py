"""ToolNode — prebuilt graph node for tool execution.

Provides a ready-to-use graph node that executes tool calls
from the last AI message and returns observations. Pairs with
``tools_condition`` for routing between tools and end.

Example::

    tools = [search_kb, create_task, send_message]
    graph.add_node("tools", ToolNode(tools))
    graph.add_conditional_edges("agent", tools_condition)

Based on: LangGraph ToolNode + tools_condition.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ToolNode:
    """Graph node that executes tool calls from AI messages.

    Given a state with ``messages``, finds the last AI message's
    tool calls, executes each tool, and appends tool response
    messages to the state.

    Args:
        tools: List of tool functions or tool definition dicts.
        handle_errors: Whether to catch and return errors as messages.
    """

    def __init__(
        self,
        tools: list[Any],
        handle_errors: bool = True,
    ) -> None:
        self._tool_map = _build_tool_map(tools)
        self._handle_errors = handle_errors

    async def __call__(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute tool calls and return updated state.

        Args:
            state: Graph state with ``messages`` key.

        Returns:
            State update with tool response messages appended.
        """
        messages = state.get("messages", [])
        last_msg = _get_last_ai_message(messages)
        if last_msg is None:
            return {}

        tool_calls = _extract_tool_calls(last_msg)
        if not tool_calls:
            return {}

        responses = await self._execute_all(tool_calls)
        return {"messages": responses}

    async def _execute_all(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Execute all tool calls and collect responses.

        Args:
            tool_calls: List of tool call dicts.

        Returns:
            List of tool response message dicts.
        """
        responses: list[dict[str, Any]] = []
        for tc in tool_calls:
            response = await self._execute_one(tc)
            responses.append(response)
        return responses

    async def _execute_one(
        self,
        tool_call: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single tool call.

        Args:
            tool_call: Dict with ``name``, ``args``, ``id``.

        Returns:
            Tool response message dict.
        """
        name = tool_call.get("name", "")
        args = tool_call.get("args", {})
        call_id = tool_call.get("id", "")

        func = self._tool_map.get(name)
        if func is None:
            return _error_response(
                call_id, name, f"Tool '{name}' not found",
            )

        try:
            result = await _invoke_tool(func, args)
            return _success_response(call_id, name, str(result))
        except Exception as exc:
            if self._handle_errors:
                return _error_response(call_id, name, str(exc))
            raise

    @property
    def tool_names(self) -> list[str]:
        """List of available tool names."""
        return sorted(self._tool_map.keys())


def tools_condition(
    state: dict[str, Any],
) -> str:
    """Route based on whether the last AI message has tool calls.

    Use as a conditional edge router in StateGraph::

        graph.add_conditional_edges(
            "agent",
            tools_condition,
            {"tools": "tools", "__end__": "__end__"},
        )

    Args:
        state: Graph state with ``messages`` key.

    Returns:
        ``"tools"`` if tool calls present, ``"__end__"`` otherwise.
    """
    messages = state.get("messages", [])
    last_msg = _get_last_ai_message(messages)
    if last_msg is None:
        return "__end__"
    tool_calls = _extract_tool_calls(last_msg)
    return "tools" if tool_calls else "__end__"


# ── Helpers ──────────────────────────────────────────────────────────── #


def _build_tool_map(tools: list[Any]) -> dict[str, Callable[..., Any]]:
    """Build name -> function mapping from tool list.

    Supports both decorated functions (with ``.tool_name``) and
    plain dicts (with ``name`` and ``handler`` keys).

    Args:
        tools: Mixed list of tool functions and dicts.

    Returns:
        Dict of tool_name -> callable.
    """
    tool_map: dict[str, Callable[..., Any]] = {}
    for tool in tools:
        if callable(tool):
            name = getattr(tool, "tool_name", tool.__name__)
            tool_map[name] = tool
        elif isinstance(tool, dict):
            name = tool.get("name", "")
            handler = tool.get("handler")
            if name and handler:
                tool_map[name] = handler
    return tool_map


def _get_last_ai_message(
    messages: list[Any],
) -> dict[str, Any] | Any | None:
    """Find the last assistant/AI message in the list.

    Args:
        messages: Message list (dicts or objects).

    Returns:
        Last AI message or None.
    """
    for msg in reversed(messages):
        role = _get_role(msg)
        if role == "assistant":
            return msg
    return None


def _get_role(msg: Any) -> str:
    """Extract role from a message (dict or object).

    Args:
        msg: Message dict or object.

    Returns:
        Role string.
    """
    if isinstance(msg, dict):
        return msg.get("role", "")
    return getattr(msg, "role", "")


def _extract_tool_calls(msg: Any) -> list[dict[str, Any]]:
    """Extract tool calls from an AI message.

    Args:
        msg: AI message (dict or object).

    Returns:
        List of tool call dicts.
    """
    if isinstance(msg, dict):
        return msg.get("tool_calls", [])
    calls = getattr(msg, "tool_calls", [])
    return [
        {
            "id": getattr(tc, "id", ""),
            "name": getattr(tc, "name", ""),
            "args": getattr(tc, "args", {}),
        }
        for tc in calls
    ]


async def _invoke_tool(
    func: Callable[..., Any],
    args: dict[str, Any],
) -> Any:
    """Invoke a tool function (sync or async).

    Args:
        func: Tool function.
        args: Keyword arguments.

    Returns:
        Tool result.
    """
    if inspect.iscoroutinefunction(func):
        return await func(**args)
    return func(**args)


def _success_response(
    call_id: str,
    name: str,
    content: str,
) -> dict[str, Any]:
    """Build a successful tool response message.

    Args:
        call_id: Tool call ID.
        name: Tool name.
        content: Result content.

    Returns:
        Tool message dict.
    """
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }


def _error_response(
    call_id: str,
    name: str,
    error: str,
) -> dict[str, Any]:
    """Build an error tool response message.

    Args:
        call_id: Tool call ID.
        name: Tool name.
        error: Error description.

    Returns:
        Tool message dict with error prefix.
    """
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": f"Error: {error}",
    }
