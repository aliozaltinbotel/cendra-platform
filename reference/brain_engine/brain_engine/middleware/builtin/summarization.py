"""Summarization middleware — compresses context when approaching limits.

Triggers automatic summarization when the message list exceeds a
configurable token-count threshold. Keeps recent messages intact
and replaces older ones with a summary.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest

logger = logging.getLogger(__name__)

# Rough estimate: 1 token ≈ 4 chars for English text
_CHARS_PER_TOKEN = 4
_DEFAULT_MAX_TOKENS = 170_000
_DEFAULT_TRIGGER_RATIO = 0.85
_DEFAULT_KEEP_RECENT = 6


class SummarizationMiddleware:
    """Middleware that summarizes old messages to save context space.

    When estimated token count exceeds ``trigger_ratio * max_tokens``,
    older messages are replaced with a single summary message while
    keeping the most recent ``keep_recent`` messages intact.

    Args:
        max_tokens: Maximum context window tokens.
        trigger_ratio: Fraction of max_tokens that triggers summarization.
        keep_recent: Number of recent messages to preserve.
        summarizer: Async callable that summarizes a message list.
            If ``None``, uses a simple concatenation fallback.
    """

    def __init__(
        self,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        trigger_ratio: float = _DEFAULT_TRIGGER_RATIO,
        keep_recent: int = _DEFAULT_KEEP_RECENT,
        summarizer: Callable[..., Awaitable[str]] | None = None,
    ) -> None:
        """Initialize SummarizationMiddleware."""
        self._max_tokens = max_tokens
        self._trigger_threshold = int(max_tokens * trigger_ratio)
        self._keep_recent = keep_recent
        self._summarizer = summarizer

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "summarization"

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
        """Summarize old messages if context is too large.

        Args:
            messages: Current message list.

        Returns:
            Potentially compressed message list.
        """
        estimated = _estimate_tokens(messages)
        if estimated < self._trigger_threshold:
            return messages

        logger.info(
            "[SummarizationMW] Triggered: ~%d tokens > %d threshold",
            estimated, self._trigger_threshold,
        )
        return await self._compress_messages(messages)

    async def post_model_call(self, response: Any) -> Any:
        """Pass through — no post-processing.

        Args:
            response: Model response.

        Returns:
            Unmodified response.
        """
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Pass through — no tool wrapping.

        Args:
            request: Tool call request.
            handler: Next handler in chain.

        Returns:
            Tool execution result.
        """
        return await handler(request)

    async def _compress_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Split messages into old + recent and summarize old.

        Args:
            messages: Full message list.

        Returns:
            Compressed message list with summary + recent messages.
        """
        system_msgs, other_msgs = _split_system(messages)
        old, recent = _split_recent(other_msgs, self._keep_recent)

        if not old:
            return messages

        summary_text = await self._summarize(old)
        summary_msg = _build_summary_message(summary_text)

        logger.info(
            "[SummarizationMW] Compressed %d msgs -> 1 summary + %d recent",
            len(old), len(recent),
        )
        return system_msgs + [summary_msg] + recent

    async def _summarize(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Generate a summary of the given messages.

        Args:
            messages: Messages to summarize.

        Returns:
            Summary text string.
        """
        if self._summarizer:
            return await self._summarizer(messages)
        return _fallback_summarize(messages)


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate the token count of a message list.

    Args:
        messages: Message list to estimate.

    Returns:
        Estimated token count.
    """
    total_chars = sum(
        len(str(msg.get("content", ""))) for msg in messages
    )
    return total_chars // _CHARS_PER_TOKEN


def _split_system(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate system messages from other messages.

    Args:
        messages: Full message list.

    Returns:
        Tuple of (system_messages, other_messages).
    """
    system: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") == "system":
            system.append(msg)
        else:
            other.append(msg)

    return system, other


def _split_recent(
    messages: list[dict[str, Any]],
    keep: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split messages into old and recent portions.

    Args:
        messages: Non-system messages.
        keep: Number of recent messages to keep.

    Returns:
        Tuple of (old_messages, recent_messages).
    """
    if len(messages) <= keep:
        return [], messages
    split_at = len(messages) - keep
    return messages[:split_at], messages[split_at:]


def _fallback_summarize(messages: list[dict[str, Any]]) -> str:
    """Simple fallback summarizer — concatenates message content.

    Args:
        messages: Messages to summarize.

    Returns:
        Concatenated summary string.
    """
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))[:200]
        lines.append(f"[{role}]: {content}")
    return "Previous conversation summary:\n" + "\n".join(lines)


def _build_summary_message(summary_text: str) -> dict[str, Any]:
    """Build a system message containing the conversation summary.

    Args:
        summary_text: The generated summary.

    Returns:
        System message dict with the summary.
    """
    return {
        "role": "system",
        "content": f"[Conversation Summary]\n{summary_text}",
    }
