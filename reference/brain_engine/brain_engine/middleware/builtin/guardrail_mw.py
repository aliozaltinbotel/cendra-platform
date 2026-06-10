"""Guardrail middleware — validates LLM outputs through the guardrail pipeline.

Runs post-model checks for hallucination, format, repetition, and
lexical safety. Triggers regeneration when checks fail.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from brain_engine.middleware.protocol import Tool, ToolRequest

logger = logging.getLogger(__name__)


class GuardrailMiddleware:
    """Middleware that validates LLM output via guardrail pipeline.

    Intercepts post-model responses and runs configured checks.
    If checks fail, optionally regenerates the response.

    Args:
        pipeline: Guardrail pipeline with a ``run`` method.
        auto_regenerate: Whether to auto-fix failed outputs.
    """

    def __init__(
        self,
        pipeline: Any,
        auto_regenerate: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._auto_regen = auto_regenerate
        self._violations: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        """Return middleware name."""
        return "guardrail"

    @property
    def violation_count(self) -> int:
        """Number of violations detected this session."""
        return len(self._violations)

    def get_tools(self) -> list[Tool]:
        """No tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """No prompt additions."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Pass through — no pre-processing needed."""
        return messages

    async def post_model_call(self, response: Any) -> Any:
        """Validate LLM response through guardrail pipeline.

        Args:
            response: Raw model response.

        Returns:
            Original or regenerated response.
        """
        if not self._pipeline:
            return response

        text = _extract_text(response)
        if not text:
            return response

        result = await self._run_checks(text)
        if result.get("passed", True):
            return response

        self._record_violation(text, result)
        if self._auto_regen and "fixed" in result:
            return _replace_text(response, result["fixed"])
        return response

    async def wrap_tool_call(
        self,
        request: ToolRequest,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Pass through tool calls unchanged."""
        return await handler(request)

    # ── Internal ──────────────────────────────────────────────────────

    async def _run_checks(self, text: str) -> dict[str, Any]:
        """Run guardrail checks on text.

        Args:
            text: Text to validate.

        Returns:
            Result dict with 'passed' and optionally 'fixed'.
        """
        try:
            if hasattr(self._pipeline, "run"):
                return await self._pipeline.run(text)
            if hasattr(self._pipeline, "check"):
                return await self._pipeline.check(text)
            return {"passed": True}
        except Exception:
            logger.warning("Guardrail check failed", exc_info=True)
            return {"passed": True}

    def _record_violation(
        self, text: str, result: dict[str, Any],
    ) -> None:
        """Record a guardrail violation.

        Args:
            text: Original text that failed.
            result: Check result dict.
        """
        self._violations.append({
            "text_preview": text[:100],
            "failures": result.get("failures", []),
        })
        logger.warning(
            "[GuardrailMW] violation detected: %s",
            result.get("failures", "unknown"),
        )


def _extract_text(response: Any) -> str:
    """Extract text content from an LLM response.

    Args:
        response: Model response object.

    Returns:
        Extracted text or empty string.
    """
    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        return str(response.content)
    if hasattr(response, "choices"):
        choices = response.choices
        if choices:
            return str(choices[0].message.content or "")
    return ""


def _replace_text(response: Any, new_text: str) -> Any:
    """Replace text content in an LLM response.

    Args:
        response: Original response.
        new_text: Replacement text.

    Returns:
        Modified response or new_text if replacement not possible.
    """
    if isinstance(response, str):
        return new_text
    if hasattr(response, "content"):
        response.content = new_text
        return response
    return new_text
