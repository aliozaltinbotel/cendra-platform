"""Ollama (local models) chat model provider.

Connects to a local Ollama instance via its OpenAI-compatible API.
Ideal for development, testing, and air-gapped environments.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import openai

from brain_engine.models.base import BaseChatModel
from brain_engine.models.messages import AIMessage, TokenUsage

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaChatModel(BaseChatModel):
    """Ollama-backed chat model using the OpenAI-compatible endpoint.

    Args:
        model: Ollama model name (e.g. ``llama3``, ``mistral``).
        base_url: Ollama server URL. Defaults to ``localhost:11434/v1``.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
    """

    def __init__(
        self,
        model: str = "llama3",
        *,
        base_url: str = _DEFAULT_BASE_URL,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize OllamaChatModel."""
        super().__init__(
            model=model,
            provider="ollama",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key="ollama",
        )

    async def _do_invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AIMessage:
        """Call the local Ollama model.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Formatted tool schemas (ignored if unsupported).

        Returns:
            ``AIMessage`` with content.
        """
        kwargs = self._build_kwargs(messages)
        response = await self._client.chat.completions.create(**kwargs)
        return _parse_response(response)

    async def _do_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream tokens from the local Ollama model.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Formatted tool schemas (ignored).

        Yields:
            Individual text tokens.
        """
        kwargs = self._build_kwargs(messages)
        kwargs["stream"] = True
        stream = await self._client.chat.completions.create(**kwargs)

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build keyword arguments for the Ollama-compatible API.

        Args:
            messages: Chat messages.

        Returns:
            Dict of keyword arguments.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        return kwargs


def _parse_response(response: Any) -> AIMessage:
    """Convert an Ollama/OpenAI-compatible response to ``AIMessage``.

    Args:
        response: Raw API response.

    Returns:
        Unified ``AIMessage``.
    """
    choice = response.choices[0]
    msg = choice.message
    usage = _extract_usage(response)

    return AIMessage(
        content=msg.content or "",
        tool_calls=[],
        usage=usage,
        model=response.model or "",
        finish_reason=choice.finish_reason or "stop",
    )


def _extract_usage(response: Any) -> TokenUsage | None:
    """Extract token usage from response.

    Args:
        response: Raw API response.

    Returns:
        ``TokenUsage`` or ``None``.
    """
    usage = response.usage
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=usage.prompt_tokens or 0,
        output_tokens=usage.completion_tokens or 0,
        total_tokens=usage.total_tokens or 0,
    )
