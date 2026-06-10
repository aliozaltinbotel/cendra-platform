"""Regenerator - Re-runs LLM with modified prompts when guardrails fail.

When a response fails quality checks (format, hallucination, repeat),
the regenerator re-prompts the LLM with explicit feedback about what
went wrong, requesting a corrected response. Implements exponential
backoff between retries to avoid rate limiting.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegenerationResult:
    """Result of a regeneration cycle.

    Attributes:
        response: The final response text.
        attempts: Number of generation attempts made.
        passed: Whether all checks passed on the final response.
        issues: Any remaining issues on the final response.
    """

    response: str
    attempts: int
    passed: bool
    issues: list[str] = field(default_factory=list)


class Regenerator:
    """Re-runs LLM generation when guardrail checks fail.

    Wraps an async generation function and applies a list of check
    functions. When checks fail, the generator is re-invoked with
    feedback about what went wrong. Uses exponential backoff between
    retries.

    Args:
        max_retries: Maximum number of regeneration attempts (not counting
            the initial generation).
        base_delay: Initial delay in seconds before the first retry.
        backoff_factor: Multiplier applied to the delay after each retry.
        max_delay: Maximum delay cap in seconds.
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        backoff_factor: float = 2.0,
        max_delay: float = 10.0,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay

    async def generate_with_checks(
        self,
        generate_fn: Callable[..., Awaitable[str]],
        check_fns: list[Callable[[str], list[str]]],
        on_retry: Callable[[int, list[str]], Awaitable[None]] | None = None,
        **generate_kwargs: Any,
    ) -> RegenerationResult:
        """Generate a response, retrying with feedback if checks fail.

        The generate_fn is called with **generate_kwargs. On failure, a
        'feedback' keyword argument is added containing the check issues,
        and the function is called again. The feedback is cumulative across
        retries.

        Args:
            generate_fn: Async function that produces a response string.
                Must accept **kwargs and optionally a 'feedback' kwarg.
            check_fns: List of synchronous check functions. Each takes a
                response string and returns a list of issue descriptions.
                An empty list means the check passed.
            on_retry: Optional async callback invoked before each retry.
                Receives (attempt_number, issues_list).
            **generate_kwargs: Arguments passed through to generate_fn.

        Returns:
            A RegenerationResult with the final response and metadata.
        """
        last_response = ""
        last_issues: list[str] = []

        for attempt in range(self.max_retries + 1):
            # Generate response
            last_response = await generate_fn(**generate_kwargs)

            # Run all checks
            last_issues = []
            for check_fn in check_fns:
                last_issues.extend(check_fn(last_response))

            # All checks passed
            if not last_issues:
                logger.info(
                    "Generation passed all checks on attempt %d", attempt + 1
                )
                return RegenerationResult(
                    response=last_response,
                    attempts=attempt + 1,
                    passed=True,
                    issues=[],
                )

            # Max retries reached
            if attempt >= self.max_retries:
                break

            # Prepare for retry
            logger.info(
                "Generation attempt %d/%d failed checks: %s",
                attempt + 1,
                self.max_retries + 1,
                last_issues,
            )

            # Notify retry callback
            if on_retry is not None:
                await on_retry(attempt + 1, last_issues)

            # Apply backoff delay
            delay = min(
                self.base_delay * (self.backoff_factor ** attempt),
                self.max_delay,
            )
            logger.debug("Backoff delay: %.2fs before retry", delay)
            await asyncio.sleep(delay)

            # Inject feedback for the next attempt
            feedback_text = self._build_feedback(last_issues, attempt + 1)
            generate_kwargs["feedback"] = feedback_text

        # Exhausted retries
        logger.warning(
            "Max retries (%d) exhausted. Returning last response with issues: %s",
            self.max_retries,
            last_issues,
        )
        return RegenerationResult(
            response=last_response,
            attempts=self.max_retries + 1,
            passed=False,
            issues=last_issues,
        )

    async def generate_with_async_checks(
        self,
        generate_fn: Callable[..., Awaitable[str]],
        check_fns: list[Callable[[str], Awaitable[list[str]]]],
        **generate_kwargs: Any,
    ) -> RegenerationResult:
        """Generate with async check functions.

        Same as generate_with_checks but accepts async check functions,
        useful for checks that need to query external services (e.g.,
        semantic memory for hallucination checks).

        Args:
            generate_fn: Async generation function.
            check_fns: List of async check functions.
            **generate_kwargs: Arguments for generate_fn.

        Returns:
            A RegenerationResult.
        """
        last_response = ""
        last_issues: list[str] = []

        for attempt in range(self.max_retries + 1):
            last_response = await generate_fn(**generate_kwargs)

            last_issues = []
            for check_fn in check_fns:
                last_issues.extend(await check_fn(last_response))

            if not last_issues:
                return RegenerationResult(
                    response=last_response,
                    attempts=attempt + 1,
                    passed=True,
                    issues=[],
                )

            if attempt >= self.max_retries:
                break

            delay = min(
                self.base_delay * (self.backoff_factor ** attempt),
                self.max_delay,
            )
            await asyncio.sleep(delay)

            generate_kwargs["feedback"] = self._build_feedback(
                last_issues, attempt + 1
            )

        return RegenerationResult(
            response=last_response,
            attempts=self.max_retries + 1,
            passed=False,
            issues=last_issues,
        )

    @staticmethod
    def _build_feedback(issues: list[str], attempt: int) -> str:
        """Build a feedback string for the LLM from check issues.

        Args:
            issues: List of issue descriptions from check functions.
            attempt: Current attempt number.

        Returns:
            A formatted feedback string to inject into the next prompt.
        """
        issues_text = "\n".join(f"  - {issue}" for issue in issues)
        return (
            f"[REGENERATION FEEDBACK - Attempt {attempt}]\n"
            f"Your previous response had the following issues:\n"
            f"{issues_text}\n\n"
            f"Please generate a corrected response that addresses "
            f"all of the above issues."
        )
