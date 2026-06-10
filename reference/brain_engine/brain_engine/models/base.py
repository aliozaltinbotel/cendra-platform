"""Abstract base class for all LLM chat model providers.

Every provider (OpenAI, Anthropic, Google, Ollama) inherits from
``BaseChatModel`` and implements the three core methods:
``invoke``, ``stream``, and ``invoke_structured``.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic import BaseModel

from brain_engine.models.messages import AIMessage
from brain_engine.models.profiles import ModelProfile, get_profile
from brain_engine.observability.exporters.prometheus_exporter import (
    build_default_exporter,
)
from brain_engine.observability.langfuse_client import (
    get_default_tracer,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _build_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """Normalize a tool dict into OpenAI-compatible function schema.

    Args:
        tool: Raw tool definition with at least ``name`` and ``parameters``.

    Returns:
        OpenAI-style function tool schema.
    """
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {}),
        },
    }


def _format_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert a list of raw tool dicts into provider-ready schemas.

    Args:
        tools: Raw tool definitions, or ``None``.

    Returns:
        List of formatted tool schemas (empty if ``tools`` is None).
    """
    if not tools:
        return []
    return [_build_tool_schema(t) for t in tools]


class BaseChatModel(ABC):
    """Unified interface for LLM chat model providers.

    Subclasses must implement ``_do_invoke`` and ``_do_stream``.
    The public ``invoke`` / ``stream`` / ``invoke_structured`` methods
    add logging, tool formatting, and structured-output parsing on top.

    Args:
        model: Model identifier within the provider.
        provider: Provider name (openai, anthropic, etc.).
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens (``None`` = provider default).
    """

    def __init__(
        self,
        model: str,
        provider: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize BaseChatModel."""
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.max_tokens = max_tokens

    @property
    def model_profile(self) -> ModelProfile:
        """Return the capability profile for this model."""
        return get_profile(self.provider, self.model)

    @property
    def full_name(self) -> str:
        """Return ``provider:model`` string."""
        return f"{self.provider}:{self.model}"

    # ── Public API ─────────────────────────────────────────────────────

    async def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AIMessage:
        """Send messages to the model and return a complete response.

        Wraps the provider call in a Langfuse generation span and a
        Prometheus ``record_llm_call`` so every LLM round-trip lands
        on the dashboards Mümin watches.  Both observability surfaces
        are best-effort: failures are caught and logged, never
        propagated, so an instrumentation bug cannot break inference.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Optional tool definitions for function calling.

        Returns:
            ``AIMessage`` with content and/or tool_calls.
        """
        formatted = _format_tools(tools)
        logger.debug(
            "invoke model=%s tools=%d msgs=%d",
            self.full_name, len(formatted), len(messages),
        )
        tracer = get_default_tracer()
        start = time.perf_counter()
        async with tracer.trace_llm(
            provider=self.provider,
            model=self.model,
            prompt=messages,
        ) as span:
            try:
                response = await self._do_invoke(messages, formatted)
            except BaseException as exc:
                span.record_error(exc)
                _emit_llm_error(self.provider, self.model)
                raise
            latency = time.perf_counter() - start
            tokens_in, tokens_out, cost = _usage_and_cost(
                response, self.model_profile,
            )
            span.record_response(
                completion=response.content,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                cost_usd=cost,
            )
            _emit_llm_metrics(
                provider=self.provider,
                model=self.model,
                cognitive_level=getattr(
                    self.model_profile, "cognitive_level", "",
                ),
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                cost_usd=cost,
                latency_seconds=latency,
            )
            return response

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream token-by-token output from the model.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Optional tool definitions.

        Yields:
            Individual text tokens as they arrive.
        """
        formatted = _format_tools(tools)
        async for token in self._do_stream(messages, formatted):
            yield token

    async def invoke_structured(
        self,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
    ) -> BaseModel:
        """Invoke the model and parse output into a Pydantic model.

        Uses ``response_format`` where supported, otherwise falls back
        to prompt-based JSON extraction with validation.

        Args:
            messages: Chat messages in OpenAI format.
            output_schema: Pydantic model class for the expected output.

        Returns:
            Validated instance of ``output_schema``.

        Raises:
            ValueError: If the model output cannot be parsed.
        """
        schema_json = json.dumps(
            output_schema.model_json_schema(), indent=2,
        )
        system_addition = (
            f"\n\nRespond ONLY with valid JSON matching this schema:\n"
            f"```json\n{schema_json}\n```"
        )
        augmented = self._inject_system_suffix(messages, system_addition)
        response = await self._do_invoke(augmented, [])
        return self._parse_structured(response.content, output_schema)

    # ── Abstract methods for subclasses ────────────────────────────────

    @abstractmethod
    async def _do_invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AIMessage:
        """Provider-specific invoke implementation."""

    @abstractmethod
    async def _do_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Provider-specific streaming implementation."""
        yield ""  # pragma: no cover — abstract

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _inject_system_suffix(
        messages: list[dict[str, Any]],
        suffix: str,
    ) -> list[dict[str, Any]]:
        """Append text to the system message (or create one).

        Args:
            messages: Original message list.
            suffix: Text to append.

        Returns:
            New message list with updated system message.
        """
        result = list(messages)
        for i, msg in enumerate(result):
            if msg.get("role") == "system":
                result[i] = {**msg, "content": msg["content"] + suffix}
                return result
        return [{"role": "system", "content": suffix.strip()}] + result

    @staticmethod
    def _parse_structured(
        raw: str,
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract JSON from model output and validate against schema.

        Args:
            raw: Raw model output text.
            schema: Pydantic model class to validate against.

        Returns:
            Validated Pydantic model instance.

        Raises:
            ValueError: If JSON extraction or validation fails.
        """
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1]
            text = text.split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1]
            text = text.split("```", 1)[0]

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse JSON from model output: {exc}"
            ) from exc

        return schema.model_validate(data)


# ---------------------------------------------------------------------------
# Observability helpers — pure functions, no I/O
# ---------------------------------------------------------------------------


def _usage_and_cost(
    response: AIMessage,
    profile: ModelProfile | None,
) -> tuple[int, int, float]:
    """Extract token usage from a response and compute USD cost.

    Falls back to ``(0, 0, 0.0)`` when the response carries no usage
    block (some providers omit it on streaming completions).  The
    cost calculation uses the profile's per-1k token prices and is
    silently zero when the profile is missing.

    Args:
        response: The :class:`AIMessage` returned by the provider.
        profile: The :class:`ModelProfile` for the active model.

    Returns:
        ``(tokens_input, tokens_output, cost_usd)``.  All three are
        non-negative.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0.0
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    if profile is None:
        return tokens_in, tokens_out, 0.0
    cost_in = (tokens_in / 1000.0) * profile.cost_per_1k_input
    cost_out = (tokens_out / 1000.0) * profile.cost_per_1k_output
    return tokens_in, tokens_out, round(cost_in + cost_out, 6)


def _emit_llm_metrics(
    *,
    provider: str,
    model: str,
    cognitive_level: str,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    latency_seconds: float,
) -> None:
    """Forward LLM call telemetry to the Prometheus exporter.

    Best-effort — any exporter exception is swallowed and logged so
    a misconfigured registry can never break the LLM call.
    """
    try:
        build_default_exporter().record_llm_call(
            provider=provider,
            model=model,
            cognitive_level=cognitive_level or "unknown",
            cost_usd=cost_usd,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            latency_seconds=latency_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — never break invoke
        logger.warning(
            "prometheus_llm_emit_failed provider=%s model=%s err=%s",
            provider, model, exc,
        )


def _emit_llm_error(provider: str, model: str) -> None:
    """Increment the LLM error series without raising on failure.

    The current ``PrometheusExporter`` does not expose a dedicated
    error counter, so we log structurally and leave the histogram /
    counter increments untouched.  Future work can add a real
    error-rate series; until then this hook documents the contract.
    """
    logger.warning(
        "llm_invoke_failed provider=%s model=%s",
        provider, model,
    )
