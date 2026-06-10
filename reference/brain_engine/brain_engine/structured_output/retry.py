"""Output Retry — error recovery for structured output parsing.

When parsing fails, constructs a retry prompt that includes the
original output and the parsing error, asking the LLM to fix it.
Integrates with any OutputParser implementation.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from brain_engine.structured_output.protocol import OutputParsingError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class OutputRetryParser:
    """Wraps an OutputParser with retry-on-failure via LLM re-prompting.

    On parse failure, constructs a correction prompt with the error
    details and the LLM's original output, then re-parses the
    corrected response.

    Args:
        parser: The underlying parser to retry with.
        max_retries: Maximum retry attempts.
    """

    def __init__(
        self,
        parser: Any,
        max_retries: int = 1,
    ) -> None:
        self._parser = parser
        self._max_retries = max_retries

    async def parse_with_retry(
        self,
        text: str,
        llm_call: Any,
        original_prompt: str = "",
    ) -> Any:
        """Parse text with retry on failure.

        On first failure, calls the LLM again with the error context.

        Args:
            text: Raw LLM output.
            llm_call: Async callable(messages) -> str for LLM re-prompting.
            original_prompt: Original prompt for context in retry.

        Returns:
            Successfully parsed output.

        Raises:
            OutputParsingError: If all retries exhausted.
        """
        last_error: OutputParsingError | None = None

        for attempt in range(1 + self._max_retries):
            try:
                return self._parser.parse(text)
            except OutputParsingError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    text = await self._retry_with_llm(
                        exc, llm_call, original_prompt,
                    )

        assert last_error is not None
        raise last_error

    async def _retry_with_llm(
        self,
        error: OutputParsingError,
        llm_call: Any,
        original_prompt: str,
    ) -> str:
        """Request LLM to fix its output based on parsing error.

        Args:
            error: The parsing error with raw output.
            llm_call: Async callable for LLM re-prompting.
            original_prompt: Original prompt context.

        Returns:
            New LLM response text.
        """
        retry_prompt = _build_retry_prompt(error, original_prompt)
        logger.debug("Retrying structured output parsing")
        response = await llm_call(retry_prompt)
        return str(response)


def _build_retry_prompt(
    error: OutputParsingError,
    original_prompt: str,
) -> str:
    """Build a retry prompt with error context.

    Args:
        error: The parsing error.
        original_prompt: Original user prompt.

    Returns:
        Retry prompt string.
    """
    parts = [
        "Your previous response could not be parsed.",
        f"Error: {error.reason}",
        "",
        "Your previous output was:",
        error.raw_output[:500],
        "",
        "Please fix your response to match the required format.",
    ]

    if original_prompt:
        parts.extend(["", "Original request:", original_prompt[:300]])

    return "\n".join(parts)
