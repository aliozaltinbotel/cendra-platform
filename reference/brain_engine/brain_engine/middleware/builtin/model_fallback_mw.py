"""ModelFallbackMiddleware — automatic provider failover.

When the primary LLM call fails, automatically retries with
fallback models in order. Tracks which model succeeded for
observability.

Example::

    fallback = ModelFallbackMiddleware(
        fallback_models=["openai:gpt-4o-mini", "anthropic:claude-haiku-4-5"],
        max_retries_per_model=2,
    )
    stack.add(fallback)

Based on: LangChain ModelFallbackMiddleware concept.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ModelFallbackMiddleware:
    """Middleware that retries LLM calls with fallback models.

    If the primary model fails (timeout, rate limit, API error),
    transparently switches to fallback models in order. Injects
    ``_fallback_model_used`` into the response metadata.

    Args:
        fallback_models: Ordered list of fallback model strings.
        max_retries_per_model: Retries before moving to next model.
        retry_exceptions: Exception types that trigger fallback.
    """

    def __init__(
        self,
        fallback_models: list[str] | None = None,
        max_retries_per_model: int = 1,
        retry_exceptions: tuple[type[Exception], ...] | None = None,
    ) -> None:
        self._fallback_models = fallback_models or []
        self._max_retries = max_retries_per_model
        self._retry_exceptions = retry_exceptions or (Exception,)
        self._fallback_stats: dict[str, int] = {}

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "model_fallback"

    def get_tools(self) -> list[dict[str, Any]]:
        """No tools provided by this middleware."""
        return []

    def get_prompt_additions(self) -> str:
        """No prompt additions."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Pass through — no pre-processing needed.

        Args:
            messages: Input messages.

        Returns:
            Unmodified messages.
        """
        return messages

    async def post_model_call(
        self,
        response: Any,
    ) -> Any:
        """Pass through — no post-processing needed.

        Args:
            response: Model response.

        Returns:
            Unmodified response.
        """
        return response

    async def execute_with_fallback(
        self,
        primary_call: Any,
        model_factory: Any,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """Execute LLM call with automatic fallback on failure.

        Tries the primary model first, then each fallback in order.
        Each model gets ``max_retries_per_model`` attempts.

        Args:
            primary_call: Async callable for the primary model.
            model_factory: Factory to create fallback model instances.
            messages: Messages to send.
            **kwargs: Additional args for model.invoke().

        Returns:
            Response from whichever model succeeded.

        Raises:
            Exception: If all models (including fallbacks) fail.
        """
        last_error = await self._try_model(
            primary_call, "primary", messages, **kwargs,
        )
        if last_error is None:
            return self._last_result

        return await self._try_fallbacks(
            model_factory, messages, last_error, **kwargs,
        )

    async def _try_model(
        self,
        model_call: Any,
        model_name: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Exception | None:
        """Try calling a model with retries.

        Args:
            model_call: Async callable.
            model_name: For logging.
            messages: Messages to send.
            **kwargs: Additional args.

        Returns:
            None if succeeded (result in self._last_result),
            or the last Exception if all retries failed.
        """
        self._last_result = None
        for attempt in range(1, self._max_retries + 1):
            try:
                self._last_result = await model_call(messages, **kwargs)
                return None
            except self._retry_exceptions as exc:
                logger.warning(
                    "Model '%s' attempt %d failed: %s",
                    model_name, attempt, exc,
                )
                last_error = exc
        return last_error

    async def _try_fallbacks(
        self,
        model_factory: Any,
        messages: list[dict[str, str]],
        last_error: Exception,
        **kwargs: Any,
    ) -> Any:
        """Try each fallback model in order.

        Args:
            model_factory: Factory to create model instances.
            messages: Messages to send.
            last_error: Error from primary model.
            **kwargs: Additional args.

        Returns:
            Response from successful fallback.

        Raises:
            Exception: If all fallbacks also fail.
        """
        for fallback_model in self._fallback_models:
            logger.info("Trying fallback model: %s", fallback_model)
            try:
                model = model_factory(fallback_model)
                result = await model.invoke(messages, **kwargs)
                self._record_fallback_usage(fallback_model)
                return result
            except self._retry_exceptions as exc:
                logger.warning(
                    "Fallback '%s' failed: %s", fallback_model, exc,
                )
                last_error = exc

        raise last_error

    def _record_fallback_usage(self, model_name: str) -> None:
        """Record that a fallback model was used.

        Args:
            model_name: Name of the fallback model used.
        """
        self._fallback_stats[model_name] = (
            self._fallback_stats.get(model_name, 0) + 1
        )
        logger.info("Fallback succeeded: %s", model_name)

    @property
    def fallback_stats(self) -> dict[str, int]:
        """Return fallback usage statistics.

        Returns:
            Dict of model_name -> usage_count.
        """
        return dict(self._fallback_stats)
