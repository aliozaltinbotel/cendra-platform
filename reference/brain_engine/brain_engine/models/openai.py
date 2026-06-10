"""OpenAI chat model provider.

Uses the official ``openai`` Python SDK for completions, streaming,
and tool calling. Supports gpt-4o, gpt-4o-mini, gpt-4-turbo, etc.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import openai

from brain_engine.models.base import BaseChatModel
from brain_engine.models.messages import AIMessage, TokenUsage, ToolCall

logger = logging.getLogger(__name__)


class OpenAIChatModel(BaseChatModel):
    """OpenAI-backed chat model.

    Args:
        model: OpenAI model identifier (e.g. ``gpt-4o-mini``).
        api_key: OpenAI API key. Falls back to ``OPENAI_API_KEY`` env var.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize OpenAIChatModel."""
        super().__init__(
            model=model,
            provider="openai",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._api_key = api_key
        self._client: openai.AsyncOpenAI | None = None

    @property
    def client(self) -> openai.AsyncOpenAI:
        """Lazily create the OpenAI client (avoids env-key check at init)."""
        if self._client is None:
            self._client = openai.AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def _do_invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AIMessage:
        """Call the OpenAI completions endpoint.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Formatted tool schemas.

        Returns:
            ``AIMessage`` with content, tool_calls, and usage.
        """
        kwargs = self._build_kwargs(messages, tools)
        response = await self.client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    async def _do_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream tokens from the OpenAI completions endpoint.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Formatted tool schemas.

        Yields:
            Individual text tokens.
        """
        kwargs = self._build_kwargs(messages, tools)
        kwargs["stream"] = True
        stream = await self.client.chat.completions.create(**kwargs)

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build keyword arguments for the OpenAI API call.

        Args:
            messages: Chat messages.
            tools: Formatted tool schemas.

        Returns:
            Dict of keyword arguments for ``chat.completions.create``.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
        return kwargs

    @staticmethod
    def _parse_response(response: Any) -> AIMessage:
        """Convert an OpenAI response object to ``AIMessage``.

        Args:
            response: Raw OpenAI API response.

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
    """Extract token usage from an OpenAI response.

    Args:
        response: Raw OpenAI API response.

    Returns:
        ``TokenUsage`` or ``None`` if unavailable.
    """
    usage = response.usage
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=usage.prompt_tokens or 0,
        output_tokens=usage.completion_tokens or 0,
        total_tokens=usage.total_tokens or 0,
    )


def _extract_tool_calls(msg: Any) -> list[ToolCall]:
    """Extract tool calls from an OpenAI message.

    Args:
        msg: OpenAI message object.

    Returns:
        List of ``ToolCall`` objects (empty if none requested).
    """
    if not msg.tool_calls:
        return []

    import json

    result: list[ToolCall] = []
    for tc in msg.tool_calls:
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}
        result.append(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                args=args,
            )
        )
    return result
