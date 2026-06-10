"""Durable Pipeline Runner — orchestrates checkpointed execution.

Wraps Brain Engine's linear pipeline steps with checkpointing,
retry, and interrupt/resume capabilities. Each step is:
1. Checked for cached result (from previous interrupted run)
2. Executed with retry policy
3. Checkpointed to Redis

Usage:
    pipeline = DurablePipeline(checkpointer, interrupt_mgr)

    async with pipeline.run("thread_123", steps=7) as ctx:
        # Step 1: classify
        classification = await ctx.step("classify", classify_fn, request)
        # Step 2: route
        level = await ctx.step("route", route_fn, request, classification)
        # ...if requires approval:
        await ctx.interrupt("Cost exceeds threshold", {"cost": 150})
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Coroutine

from brain_engine.durability.checkpointer import (
    PipelineCheckpointer,
    PipelineState,
    StepResult,
)
from brain_engine.durability.interrupt import InterruptResume, PipelineInterrupt
from brain_engine.durability.retry import RetryPolicy, retry_async

logger = logging.getLogger(__name__)


class PipelineContext:
    """Execution context for a single pipeline run.

    Tracks current step, handles caching from previous runs,
    and provides step() and interrupt() methods.

    Args:
        state: Current pipeline state.
        checkpointer: State persistence layer.
        interrupt_mgr: Interrupt/resume manager.
        default_retry: Default retry policy for steps.
    """

    def __init__(
        self,
        state: PipelineState,
        checkpointer: PipelineCheckpointer,
        interrupt_mgr: InterruptResume,
        default_retry: RetryPolicy | None = None,
    ) -> None:
        self._state = state
        self._checkpointer = checkpointer
        self._interrupt = interrupt_mgr
        self._default_retry = default_retry

    @property
    def state(self) -> PipelineState:
        """Current pipeline state."""
        return self._state

    @property
    def is_resuming(self) -> bool:
        """Whether this is a resumed pipeline run."""
        return "resume_data" in self._state.metadata

    @property
    def resume_data(self) -> dict[str, Any]:
        """Human decision data from resume (empty if not resuming)."""
        return self._state.metadata.get("resume_data", {})

    async def step(
        self,
        name: str,
        func: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
        *args: Any,
        retry: RetryPolicy | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute a pipeline step with checkpointing and retry.

        If the step was already completed in a previous run
        (resumed pipeline), returns the cached result.

        Args:
            name: Step name (e.g., 'classify', 'generate').
            func: Async function to execute.
            *args: Positional arguments for func.
            retry: Override retry policy for this step.
            **kwargs: Keyword arguments for func.

        Returns:
            Step result data dict.
        """
        cached = self._get_cached(name)
        if cached is not None:
            logger.debug("Step '%s' cached, skipping execution", name)
            return cached

        return await self._execute_step(name, func, args, kwargs, retry)

    async def interrupt(
        self,
        reason: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Interrupt pipeline for human review.

        Args:
            reason: Why human input is needed.
            data: Context for the reviewer.

        Raises:
            PipelineInterrupt: Always raised to halt execution.
        """
        self._state = await self._interrupt.interrupt(
            self._state, reason, data,
        )
        raise PipelineInterrupt(reason=reason, data=data or {})

    def _get_cached(self, name: str) -> dict[str, Any] | None:
        """Get cached step result from previous run.

        Args:
            name: Step name.

        Returns:
            Cached data dict, or None if not cached.
        """
        step_data = self._state.steps.get(name)
        if step_data is None:
            return None
        return step_data.get("data")

    async def _execute_step(
        self,
        name: str,
        func: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        retry: RetryPolicy | None,
    ) -> dict[str, Any]:
        """Execute step with retry and checkpoint result.

        Args:
            name: Step name.
            func: Async function to execute.
            args: Positional arguments.
            kwargs: Keyword arguments.
            retry: Retry policy override.

        Returns:
            Step result data.
        """
        policy = retry or self._default_retry
        start = time.monotonic()

        try:
            if policy:
                data = await retry_async(policy, func, *args, **kwargs)
            else:
                data = await func(*args, **kwargs)
        except PipelineInterrupt:
            raise
        except Exception as exc:
            await self._checkpointer.mark_failed(
                self._state, f"{name}: {exc}",
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        result = StepResult(name=name, data=data, duration_ms=duration_ms)
        self._state = await self._checkpointer.save_step(
            self._state, result,
        )

        logger.debug("Step '%s' completed in %dms", name, duration_ms)
        return data


class DurablePipeline:
    """Factory for durable pipeline executions.

    Creates PipelineContext instances that wrap step execution
    with checkpointing, retry, and interrupt/resume.

    Args:
        checkpointer: State persistence layer.
        interrupt_mgr: Interrupt/resume manager.
        default_retry: Default retry policy for all steps.
    """

    def __init__(
        self,
        checkpointer: PipelineCheckpointer,
        interrupt_mgr: InterruptResume | None = None,
        default_retry: RetryPolicy | None = None,
    ) -> None:
        self._checkpointer = checkpointer
        self._interrupt = interrupt_mgr or InterruptResume(checkpointer)
        self._default_retry = default_retry

    @asynccontextmanager
    async def run(
        self,
        thread_id: str,
        total_steps: int,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[PipelineContext]:
        """Start or resume a pipeline execution.

        If an interrupted pipeline exists for this thread,
        resumes from the interruption point. Otherwise starts fresh.

        Args:
            thread_id: Conversation/request thread identifier.
            total_steps: Number of pipeline steps.
            metadata: Additional execution context.

        Yields:
            PipelineContext for executing steps.
        """
        state = await self._get_or_create(thread_id, total_steps, metadata)
        ctx = PipelineContext(
            state=state,
            checkpointer=self._checkpointer,
            interrupt_mgr=self._interrupt,
            default_retry=self._default_retry,
        )
        yield ctx
        # Pipeline completed if we got here without interrupt

    async def _get_or_create(
        self,
        thread_id: str,
        total_steps: int,
        metadata: dict[str, Any] | None,
    ) -> PipelineState:
        """Load existing interrupted state or create new.

        Args:
            thread_id: Thread identifier.
            total_steps: Number of steps.
            metadata: Execution metadata.

        Returns:
            PipelineState (existing or new).
        """
        existing = await self._checkpointer.load_by_thread(thread_id)
        if existing and existing.is_resumable():
            logger.info(
                "Resuming pipeline %s at step %d",
                existing.pipeline_id,
                existing.current_step,
            )
            return existing

        return await self._checkpointer.create(
            thread_id, total_steps, metadata,
        )
