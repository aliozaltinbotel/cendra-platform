"""LLM Router — Model selection and unified call interface.

Routes LLM calls to the appropriate provider based on CognitiveLevel.
Uses litellm for unified access to GPT-4o Mini, GPT-4o, and Claude.
Includes fallback logic and per-request cost tracking.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import litellm

from brain_engine.reasoning.complexity_router import CognitiveLevel, ModelConfig
from brain_engine.reasoning.provider_tier import (
    CognitiveLevel as _ProviderTierLevel,
)
from brain_engine.reasoning.provider_tier import (
    ProviderTier,
    tier_for,
)

logger = logging.getLogger(__name__)

# Fallback chain: if primary model fails, try these in order
_FALLBACK_MODELS: dict[str, list[str]] = {
    "gpt-4o-mini": ["gpt-4o"],
    "gpt-4o": ["gpt-4o-mini"],
}

# Approximate costs per 1K tokens (USD)
_COST_PER_1K_INPUT: dict[str, float] = {
    "gpt-4o-mini": 0.00015,
    "gpt-4o": 0.0025,
}
_COST_PER_1K_OUTPUT: dict[str, float] = {
    "gpt-4o-mini": 0.0006,
    "gpt-4o": 0.01,
}


@dataclass
class LLMResponse:
    """Unified response from any LLM provider.

    Attributes:
        content: Generated text content.
        model_used: Actual model that produced the response.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
        latency_ms: Wall-clock time in milliseconds.
        cost_usd: Estimated cost in USD.
        is_fallback: Whether a fallback model was used.
    """

    content: str
    model_used: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    is_fallback: bool = False


@dataclass
class CostTracker:
    """Tracks cumulative LLM costs for the current session.

    Attributes:
        total_usd: Total cost in USD.
        total_input_tokens: Total input tokens consumed.
        total_output_tokens: Total output tokens generated.
        call_count: Number of LLM calls made.
        cost_by_model: Cost breakdown by model.
    """

    total_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    call_count: int = 0
    cost_by_model: dict[str, float] = field(default_factory=dict)

    def record(self, response: LLMResponse) -> None:
        """Record costs from an LLM response.

        Args:
            response: The LLM response to record.
        """
        self.total_usd += response.cost_usd
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        self.call_count += 1

        model = response.model_used
        self.cost_by_model[model] = (
            self.cost_by_model.get(model, 0.0) + response.cost_usd
        )


class LLMRouter:
    """Routes LLM calls to the appropriate model with fallback support.

    Provides a unified interface for calling any supported LLM through
    litellm, with automatic fallback on failure and cost tracking.

    Args:
        max_retries: Number of retry attempts per model before fallback.
    """

    def __init__(self, max_retries: int = 2) -> None:
        self._max_retries = max_retries
        self._cost_tracker = CostTracker()

    @property
    def costs(self) -> CostTracker:
        """Access cumulative cost tracker."""
        return self._cost_tracker

    @staticmethod
    def tier_for_level(level: CognitiveLevel) -> ProviderTier:
        """Resolve the provider-tier fallback chain for a cognitive level.

        Bridges the live :class:`CognitiveLevel` enum used by
        :class:`ComplexityRouter` (values ``"instinct"`` /
        ``"situation"`` / ``"experience"`` / ``"strategy"``) to the
        :class:`brain_engine.reasoning.provider_tier.CognitiveLevel`
        the tier table is keyed by (values ``"L1"`` / ``"L2"`` /
        ``"L3"`` / ``"L4"``).  Advisory §3 + ADR-0016 keep the four
        tier names (``primary`` → ``fallback`` → ``emergency`` →
        ``eu_resident``) so future multi-provider expansions
        (Anthropic, Google, EU residency) only touch the table.

        The bridge is by **name prefix** (``L1_``, ``L2_``, …) so
        the two enums can disagree on member suffixes
        (``L2_SITUATION`` vs ``L2_REFLEX``) without breaking the
        lookup.

        Args:
            level: Cognitive level emitted by ComplexityRouter.

        Returns:
            The ProviderTier whose ``chain()`` method yields the
            ordered failover sequence for ``level``.

        Raises:
            ValueError: If ``level.name`` does not start with a
                recognised ``L1`` / ``L2`` / ``L3`` / ``L4``
                prefix — surfaces a future enum drift loudly
                instead of silently picking the wrong tier.
        """
        prefix = level.name.split("_", 1)[0]
        return tier_for(_ProviderTierLevel(prefix))

    async def call(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> LLMResponse:
        """Call an LLM with the given messages and config.

        Tries the primary model first, then fallbacks on failure.

        Args:
            messages: Chat messages in OpenAI format.
            config: Model configuration from ComplexityRouter.

        Returns:
            LLMResponse with content and metadata.

        Raises:
            RuntimeError: If all models (primary + fallbacks) fail.
        """
        models_to_try = self._build_model_chain(config.model)

        for i, model in enumerate(models_to_try):
            response = await self._try_model(
                model=model,
                messages=messages,
                config=config,
                is_fallback=i > 0,
            )
            if response is not None:
                self._cost_tracker.record(response)
                return response

        raise RuntimeError(
            f"All LLM models failed: {models_to_try}"
        )

    async def _try_model(
        self,
        model: str,
        messages: list[dict[str, str]],
        config: ModelConfig,
        is_fallback: bool,
    ) -> LLMResponse | None:
        """Attempt a single model call with retries.

        Args:
            model: Model identifier.
            messages: Chat messages.
            config: Model configuration.
            is_fallback: Whether this is a fallback attempt.

        Returns:
            LLMResponse on success, None on failure.
        """
        for attempt in range(self._max_retries):
            try:
                return await self._execute_call(
                    model, messages, config, is_fallback,
                )
            except Exception:
                logger.warning(
                    "LLM call failed: model=%s attempt=%d/%d",
                    model, attempt + 1, self._max_retries,
                    exc_info=True,
                )
        return None

    async def _execute_call(
        self,
        model: str,
        messages: list[dict[str, str]],
        config: ModelConfig,
        is_fallback: bool,
    ) -> LLMResponse:
        """Execute a single LLM API call via litellm.

        Args:
            model: Model identifier.
            messages: Chat messages.
            config: Model configuration.
            is_fallback: Whether this is a fallback attempt.

        Returns:
            LLMResponse with content and metadata.
        """
        start = time.monotonic()

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        content = response.choices[0].message.content or ""
        usage = response.usage or {}

        input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "completion_tokens", 0)
        cost = _estimate_cost(model, input_tokens, output_tokens)

        logger.info(
            "LLM call: model=%s tokens=%d+%d latency=%dms cost=$%.4f",
            model, input_tokens, output_tokens, elapsed_ms, cost,
        )

        return LLMResponse(
            content=content,
            model_used=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms,
            cost_usd=cost,
            is_fallback=is_fallback,
        )

    @staticmethod
    def _build_model_chain(primary: str) -> list[str]:
        """Build ordered list of models to try (primary + fallbacks).

        Args:
            primary: Primary model identifier.

        Returns:
            List of model identifiers in priority order.
        """
        fallbacks = _FALLBACK_MODELS.get(primary, [])
        return [primary, *fallbacks]


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate USD cost for a given model call.

    Args:
        model: Model identifier.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Estimated cost in USD.
    """
    input_rate = _COST_PER_1K_INPUT.get(model, 0.003)
    output_rate = _COST_PER_1K_OUTPUT.get(model, 0.015)
    return (input_tokens / 1000 * input_rate) + (output_tokens / 1000 * output_rate)
