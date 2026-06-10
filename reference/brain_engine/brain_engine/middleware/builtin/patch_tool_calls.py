"""PatchToolCalls middleware — prevents orphaned tool_use errors.

When a tool call starts but never gets a result (due to interruption
or cancellation), this middleware injects a placeholder ToolMessage
to prevent "tool_use_id not found" errors on the next model call.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest

logger = logging.getLogger(__name__)

_PLACEHOLDER_CONTENT = "[Tool call was interrupted — no result available]"


class PatchToolCallsMiddleware:
    """Middleware that patches orphaned tool calls in message history.

    Scans messages for assistant tool_calls without a matching tool
    response and injects a placeholder ToolMessage for each.
    """

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "patch_tool_calls"

    def get_tools(self) -> list[Tool]:
        """Return empty tool list."""
        return []

    def get_prompt_additions(self) -> str:
        """Return empty prompt addition."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Patch orphaned tool calls before sending to the model.

        Args:
            messages: Current message list.

        Returns:
            Message list with placeholder tool responses injected.
        """
        pending_ids = _find_pending_tool_calls(messages)
        if not pending_ids:
            return messages

        logger.info(
            "[PatchToolCalls] Patching %d orphaned tool calls",
            len(pending_ids),
        )
        return _inject_placeholders(messages, pending_ids)

    async def post_model_call(self, response: Any) -> Any:
        """Pass through — no post-processing needed.

        Args:
            response: Raw model response.

        Returns:
            Unmodified response.
        """
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Pass through — no tool wrapping needed.

        Args:
            request: Tool call request.
            handler: Next handler in the chain.

        Returns:
            Tool execution result.
        """
        return await handler(request)


def _find_pending_tool_calls(
    messages: list[dict[str, Any]],
) -> set[str]:
    """Find tool call IDs without matching tool responses.

    Args:
        messages: Message list to scan.

    Returns:
        Set of orphaned tool_call IDs.
    """
    requested: set[str] = set()
    responded: set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                tc_id = _extract_call_id(tc)
                if tc_id:
                    requested.add(tc_id)
        elif msg.get("role") == "tool":
            tool_id = msg.get("tool_call_id", "")
            if tool_id:
                responded.add(tool_id)

    return requested - responded


def _extract_call_id(tool_call: Any) -> str:
    """Extract the call ID from a tool_call object or dict.

    Args:
        tool_call: Tool call (dict or object with ``id`` attribute).

    Returns:
        Call ID string, or empty string if not found.
    """
    if isinstance(tool_call, dict):
        return tool_call.get("id", "")
    return getattr(tool_call, "id", "")


def _inject_placeholders(
    messages: list[dict[str, Any]],
    pending_ids: set[str],
) -> list[dict[str, Any]]:
    """Inject placeholder tool responses for orphaned calls.

    Inserts placeholders immediately after the assistant message
    that contains the orphaned tool call.

    Args:
        messages: Original message list.
        pending_ids: Set of tool_call IDs needing placeholders.

    Returns:
        New message list with placeholders inserted.
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        result.append(msg)
        if msg.get("role") != "assistant":
            continue

        for tc in msg.get("tool_calls", []):
            tc_id = _extract_call_id(tc)
            if tc_id in pending_ids:
                result.append(_make_placeholder(tc_id))

    return result


def _make_placeholder(tool_call_id: str) -> dict[str, Any]:
    """Create a placeholder tool response message.

    Args:
        tool_call_id: The orphaned tool_call ID.

    Returns:
        Tool message dict with placeholder content.
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": _PLACEHOLDER_CONTENT,
    }
