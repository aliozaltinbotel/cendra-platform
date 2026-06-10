"""Memory middleware — injects memory context into prompts.

Retrieves relevant memories from the 6-tier memory system and
injects them into the system prompt before each LLM call.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest

logger = logging.getLogger(__name__)


class MemoryMiddleware:
    """Middleware that injects memory context into LLM prompts.

    Queries semantic, episodic, and procedural memory for relevant
    context and adds it to the system prompt.

    Args:
        memory_store: Memory system with a ``retrieve`` method.
        max_context_chars: Character budget for memory context.
    """

    def __init__(
        self,
        memory_store: Any,
        max_context_chars: int = 4000,
    ) -> None:
        self._memory = memory_store
        self._max_chars = max_context_chars

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "memory"

    def get_tools(self) -> list[Tool]:
        """No tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """No static prompt additions — context is dynamic."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Inject memory context into the system message.

        Args:
            messages: Current message list.

        Returns:
            Messages with memory context appended to system.
        """
        context = await self._retrieve_context(messages)
        if not context:
            return messages

        return _inject_context(messages, context)

    async def post_model_call(self, response: Any) -> Any:
        """Pass through — no post-processing."""
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Pass through tool calls unchanged."""
        return await handler(request)

    # ── Internal ──────────────────────────────────────────────────────

    async def _retrieve_context(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Retrieve relevant memory context for the conversation.

        Args:
            messages: Current messages for query extraction.

        Returns:
            Formatted memory context string.
        """
        query = _extract_query(messages)
        if not query:
            return ""

        try:
            if hasattr(self._memory, "retrieve"):
                results = await self._memory.retrieve(query)
            elif hasattr(self._memory, "search"):
                results = await self._memory.search(query)
            else:
                return ""

            return _format_memory_results(results, self._max_chars)
        except Exception:
            logger.warning("Memory retrieval failed", exc_info=True)
            return ""


def _extract_query(messages: list[dict[str, Any]]) -> str:
    """Extract the latest user message as search query.

    Args:
        messages: Message list.

    Returns:
        Last user message content or empty string.
    """
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))[:500]
    return ""


def _format_memory_results(
    results: Any,
    max_chars: int,
) -> str:
    """Format memory results into a context string.

    Args:
        results: Retrieved memory items.
        max_chars: Maximum character budget.

    Returns:
        Formatted context string.
    """
    if isinstance(results, str):
        return results[:max_chars]

    if isinstance(results, list):
        parts: list[str] = []
        total = 0
        for item in results:
            text = str(item)
            if total + len(text) > max_chars:
                break
            parts.append(text)
            total += len(text)
        return "\n".join(parts)

    return str(results)[:max_chars]


def _inject_context(
    messages: list[dict[str, Any]],
    context: str,
) -> list[dict[str, Any]]:
    """Inject memory context into the system message.

    Args:
        messages: Original message list.
        context: Memory context to inject.

    Returns:
        Modified message list.
    """
    result = list(messages)
    section = f"\n\n## Relevant Memory\n{context}"

    for i, msg in enumerate(result):
        if msg.get("role") == "system":
            result[i] = {
                **msg,
                "content": str(msg.get("content", "")) + section,
            }
            return result

    # No system message — prepend one
    result.insert(0, {"role": "system", "content": section.strip()})
    return result
