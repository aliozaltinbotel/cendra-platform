"""Parallel Steps — concurrent execution within a pipeline.

Enables running independent pipeline steps simultaneously via
asyncio.gather. Steps that don't depend on each other execute
in parallel, reducing total pipeline latency.

Example:
    # Sequential: 100ms + 100ms = 200ms
    memory = await retrieve_memory(query)
    kb = await search_kb(query)

    # Parallel: max(100ms, 100ms) = 100ms
    memory, kb = await parallel(
        ("memory", retrieve_memory, query),
        ("kb", search_kb, query),
    )

Design:
    - Each parallel group runs via asyncio.gather
    - Individual step failures don't cancel siblings (return_exceptions=False by default)
    - Results are returned as a dict keyed by step name
    - Integrates with PipelineCheckpointer for durability
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParallelStep:
    """A single step to execute in parallel.

    Attributes:
        name: Step identifier for result mapping.
        func: Async function to execute.
        args: Positional arguments for func.
        kwargs: Keyword arguments for func.
    """

    name: str
    func: Callable[..., Coroutine[Any, Any, Any]]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ParallelResult:
    """Result of a parallel execution group.

    Attributes:
        results: Step name -> step output mapping.
        duration_ms: Total wall-clock time for the group.
        errors: Step name -> exception mapping (if any).
    """

    results: dict[str, Any]
    duration_ms: int = 0
    errors: dict[str, Exception] | None = None

    @property
    def success(self) -> bool:
        """True if all steps completed without errors."""
        return not self.errors


async def parallel(*steps: ParallelStep) -> ParallelResult:
    """Execute multiple steps concurrently via asyncio.gather.

    All steps start simultaneously. Total time equals the slowest step.
    If any step raises, it's captured in errors without cancelling others.

    Args:
        *steps: ParallelStep instances to execute concurrently.

    Returns:
        ParallelResult with all outputs keyed by step name.
    """
    if not steps:
        return ParallelResult(results={})

    start = time.monotonic()
    tasks = [_wrap_step(step) for step in steps]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    return _build_result(steps, raw_results, start)


async def parallel_map(
    func: Callable[..., Coroutine[Any, Any, Any]],
    items: list[Any],
    concurrency: int = 10,
) -> list[Any]:
    """Apply an async function to items with bounded concurrency.

    Like asyncio.gather but with a semaphore to limit parallelism.
    Prevents overwhelming external services (LLM API, Redis, etc.).

    Args:
        func: Async function to apply to each item.
        items: List of items to process.
        concurrency: Maximum simultaneous executions.

    Returns:
        List of results in same order as items.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(item: Any) -> Any:
        async with semaphore:
            return await func(item)

    return await asyncio.gather(*[bounded(item) for item in items])


async def _wrap_step(step: ParallelStep) -> Any:
    """Execute a single ParallelStep.

    Args:
        step: Step to execute.

    Returns:
        Step function result.
    """
    kwargs = step.kwargs or {}
    return await step.func(*step.args, **kwargs)


def _build_result(
    steps: tuple[ParallelStep, ...],
    raw_results: list[Any],
    start: float,
) -> ParallelResult:
    """Build ParallelResult from gather output.

    Separates successful results from exceptions.

    Args:
        steps: Original step definitions.
        raw_results: Raw asyncio.gather results.
        start: Start time for duration calculation.

    Returns:
        Structured ParallelResult.
    """
    results: dict[str, Any] = {}
    errors: dict[str, Exception] = {}

    for step, result in zip(steps, raw_results):
        if isinstance(result, Exception):
            errors[step.name] = result
            logger.error("Parallel step '%s' failed: %s", step.name, result)
        else:
            results[step.name] = result

    duration_ms = int((time.monotonic() - start) * 1000)

    return ParallelResult(
        results=results,
        duration_ms=duration_ms,
        errors=errors if errors else None,
    )
