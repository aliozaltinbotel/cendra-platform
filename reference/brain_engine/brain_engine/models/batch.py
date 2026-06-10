"""Model batch execution — parallel inference for multiple inputs.

Provides ``batch()`` and ``batch_as_completed()`` for running
multiple LLM calls in parallel with concurrency control.

Example::

    model = init_chat_model("openai:gpt-4o-mini")
    results = await batch(
        model,
        [
            [{"role": "user", "content": "Translate to Spanish: Hello"}],
            [{"role": "user", "content": "Translate to Spanish: Goodbye"}],
        ],
        max_concurrency=5,
    )

Based on: LangChain model.batch() / batch_as_completed().
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    """Result of a single batch item.

    Attributes:
        index: Position in the input batch.
        response: Model response (AIMessage or similar).
        error: Error message if the call failed.
        elapsed_ms: Time taken for this individual call.
    """

    index: int
    response: Any = None
    error: str = ""
    elapsed_ms: int = 0

    @property
    def succeeded(self) -> bool:
        """Whether this batch item completed successfully."""
        return self.error == ""


async def batch(
    model: Any,
    inputs: list[list[dict[str, str]]],
    *,
    max_concurrency: int = 10,
    return_exceptions: bool = True,
) -> list[BatchResult]:
    """Execute multiple model calls in parallel.

    Processes all inputs with bounded concurrency and returns
    results in the same order as inputs.

    Args:
        model: BaseChatModel instance with ``invoke`` method.
        inputs: List of message lists, one per call.
        max_concurrency: Maximum parallel calls.
        return_exceptions: If True, catch errors; else propagate.

    Returns:
        List of BatchResult in input order.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    tasks = [
        _invoke_with_semaphore(model, messages, i, semaphore)
        for i, messages in enumerate(inputs)
    ]
    return await asyncio.gather(*tasks)


async def batch_as_completed(
    model: Any,
    inputs: list[list[dict[str, str]]],
    *,
    max_concurrency: int = 10,
) -> AsyncIterator[BatchResult]:
    """Execute batch and yield results as they complete.

    Unlike ``batch()``, yields results in completion order
    (fastest first), not input order.

    Args:
        model: BaseChatModel instance.
        inputs: List of message lists.
        max_concurrency: Maximum parallel calls.

    Yields:
        BatchResult as each call completes.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    pending: set[asyncio.Task[BatchResult]] = set()

    for i, messages in enumerate(inputs):
        task = asyncio.create_task(
            _invoke_with_semaphore(model, messages, i, semaphore),
        )
        pending.add(task)

    while pending:
        done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            yield task.result()


async def _invoke_with_semaphore(
    model: Any,
    messages: list[dict[str, str]],
    index: int,
    semaphore: asyncio.Semaphore,
) -> BatchResult:
    """Invoke model with concurrency limit.

    Args:
        model: Model to invoke.
        messages: Message list for this call.
        index: Position in the batch.
        semaphore: Concurrency limiter.

    Returns:
        BatchResult with response or error.
    """
    async with semaphore:
        start = time.monotonic()
        try:
            response = await model.invoke(messages)
            elapsed = int((time.monotonic() - start) * 1000)
            return BatchResult(
                index=index,
                response=response,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.warning(
                "Batch item %d failed: %s", index, exc,
            )
            return BatchResult(
                index=index,
                error=str(exc),
                elapsed_ms=elapsed,
            )
