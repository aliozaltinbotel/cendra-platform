"""Anthropic (Claude) chat model provider.

Uses ``litellm`` as the transport layer to call Anthropic models
(claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5). This avoids an extra
direct dependency while preserving a unified interface.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import litellm

from brain_engine.models.base import BaseChatModel
from brain_engine.models.messages import AIMessage, TokenUsage, ToolCall

logger = logging.getLogger(__name__)

# litellm requires "anthropic/" prefix for Anthropic models
_LITELLM_PREFIX = "anthropic/"


class AnthropicChatModel(BaseChatModel):
    """Anthropic-backed chat model via litellm.

    Args:
        model: Anthropic model identifier (e.g. ``claude-sonnet-4-6``).
        api_key: Anthropic API key. Falls back to ``ANTHROPIC_API_KEY`` env.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        *,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize AnthropicChatModel."""
        super().__init__(
            model=model,
            provider="anthropic",
            temperature=temperature,
            max_tokens=max_tokens or 4096,
        )
        self._api_key = api_key

    async def _do_invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AIMessage:
        """Call Anthropic via litellm.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Formatted tool schemas.

        Returns:
            ``AIMessage`` with content and/or tool_calls.
        """
        kwargs = self._build_kwargs(messages, tools)
        response = await litellm.acompletion(**kwargs)
        return _parse_litellm_response(response)

    async def _do_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream tokens from Anthropic via litellm.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Formatted tool schemas.

        Yields:
            Individual text tokens.
        """
        kwargs = self._build_kwargs(messages, tools)
        kwargs["stream"] = True
        response = await litellm.acompletion(**kwargs)

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build keyword arguments for litellm acompletion.

        Args:
            messages: Chat messages.
            tools: Formatted tool schemas.

        Returns:
            Dict of keyword arguments.
        """
        litellm_model = f"{_LITELLM_PREFIX}{self.model}"
        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if tools:
            kwargs["tools"] = tools
        return kwargs


def _parse_litellm_response(response: Any) -> AIMessage:
    """Convert a litellm response to ``AIMessage``.

    Args:
        response: Raw litellm response object.

    Returns:
        Unified ``AIMessage``.
    """
    choice = response.choices[0]
    msg = choice.message
    usage = _extract_usage(response)
    tool_calls = _extract_tool_calls(msg)

    return AIMessage(
        content=msg.content or "",
        tool_calls=tool_calls,
        usage=usage,
        model=response.model or "",
        finish_reason=choice.finish_reason or "stop",
    )


def _extract_usage(response: Any) -> TokenUsage | None:
    """Extract token usage from litellm response.

    Args:
        response: Raw litellm response.

    Returns:
        ``TokenUsage`` or ``None``.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0),
        output_tokens=getattr(usage, "completion_tokens", 0),
        total_tokens=getattr(usage, "total_tokens", 0),
    )


def _extract_tool_calls(msg: Any) -> list[ToolCall]:
    """Extract tool calls from a litellm message.

    Args:
        msg: Litellm message object.

    Returns:
        List of ``ToolCall`` objects.
    """
    raw_calls = getattr(msg, "tool_calls", None)
    if not raw_calls:
        return []

    result: list[ToolCall] = []
    for tc in raw_calls:
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, TypeError, AttributeError):
            args = {}
        result.append(
            ToolCall(
                id=getattr(tc, "id", ""),
                name=tc.function.name,
                args=args,
            )
        )
    return result
