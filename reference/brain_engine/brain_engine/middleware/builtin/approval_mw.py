"""Approval middleware — human-in-the-loop tool gating.

Intercepts high-risk tool calls and routes them through the
approval gateway before execution. Integrates with the existing
approval/ module for notification and decision tracking.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest

logger = logging.getLogger(__name__)


class ApprovalMiddleware:
    """Middleware that gates tool calls through human approval.

    Checks each tool call against a list of tools requiring approval.
    If approval is needed, delegates to the approval gateway.

    Args:
        gateway: Approval gateway with ``request_approval`` method.
        tools_requiring_approval: Tool names that need human approval.
    """

    def __init__(
        self,
        gateway: Any,
        tools_requiring_approval: list[str] | None = None,
    ) -> None:
        self._gateway = gateway
        self._gated_tools = set(tools_requiring_approval or [])
        self._approved: list[str] = []
        self._rejected: list[str] = []

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "approval"

    @property
    def approved_count(self) -> int:
        """Number of approved tool calls."""
        return len(self._approved)

    @property
    def rejected_count(self) -> int:
        """Number of rejected tool calls."""
        return len(self._rejected)

    def get_tools(self) -> list[Tool]:
        """No tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """Inform the LLM about gated tools.

        Returns:
            Prompt addition listing gated tools.
        """
        if not self._gated_tools:
            return ""
        tools_str = ", ".join(sorted(self._gated_tools))
        return (
            f"Note: These tools require human approval before execution: "
            f"{tools_str}. Wait for approval before proceeding."
        )

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Pass through — no pre-processing."""
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Pass through — no post-processing."""
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Gate tool calls through approval if needed.

        Args:
            request: Tool call request.
            handler: Next handler in chain.

        Returns:
            Tool result or rejection message.
        """
        if not self._needs_approval(request.name):
            return await handler(request)

        approved = await self._request_approval(request)
        if approved:
            self._approved.append(request.name)
            return await handler(request)

        self._rejected.append(request.name)
        return f"Tool '{request.name}' was rejected by human reviewer."

    def _needs_approval(self, tool_name: str) -> bool:
        """Check if a tool requires human approval.

        Args:
            tool_name: Tool name.

        Returns:
            True if approval needed.
        """
        return tool_name in self._gated_tools

    async def _request_approval(self, request: ToolRequest) -> bool:
        """Request human approval for a tool call.

        Args:
            request: Tool call request.

        Returns:
            True if approved, False if rejected.
        """
        if not self._gateway:
            logger.warning("No approval gateway — auto-rejecting")
            return False

        try:
            if hasattr(self._gateway, "request_approval"):
                return await self._gateway.request_approval(
                    tool_name=request.name,
                    tool_args=request.args,
                )
            return False
        except Exception:
            logger.warning("Approval request failed", exc_info=True)
            return False
