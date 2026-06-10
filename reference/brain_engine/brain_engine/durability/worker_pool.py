"""Worker Pool — async task consumers replacing Cendra's multi-agent.

Replaces separate Guest Agent, Cleaner Agent, Vendor Agent with a
single pool of async workers consuming tasks from TaskQueue.

Architecture:
    WorkerPool(concurrency=5)
        ├─ Worker 1: processing "send_welcome" task
        ├─ Worker 2: processing "create_access_code" task
        ├─ Worker 3: processing "schedule_cleaning" task
        ├─ Worker 4: idle (waiting for task)
        └─ Worker 5: idle (waiting for task)

Each worker:
    1. Dequeues a task from TaskQueue
    2. Looks up handler by task_type in registry
    3. Executes handler with retry
    4. On failure: requeue or dead-letter
    5. Loops until shutdown

Usage:
    pool = WorkerPool(queue, concurrency=5)
    pool.register("send_welcome", handle_welcome)
    pool.register("create_access_code", handle_access_code)

    await pool.start()   # Starts 5 workers in background
    await pool.stop()    # Graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

from brain_engine.durability.retry import RetryPolicy, retry_async
from brain_engine.durability.task_queue import Task, TaskQueue

logger = logging.getLogger(__name__)

TaskHandler = Callable[[Task], Coroutine[Any, Any, dict[str, Any]]]


class WorkerPool:
    """Pool of async workers consuming from a TaskQueue.

    Workers run as background asyncio tasks. Each worker loops:
    dequeue → execute handler → repeat. Handlers are registered
    by task_type.

    Args:
        queue: Task queue to consume from.
        concurrency: Number of parallel workers.
        poll_interval: Seconds between empty queue polls.
        default_retry: Default retry policy for handlers.
    """

    def __init__(
        self,
        queue: TaskQueue,
        concurrency: int = 5,
        poll_interval: float = 1.0,
        default_retry: RetryPolicy | None = None,
    ) -> None:
        self._queue = queue
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._default_retry = default_retry
        self._handlers: dict[str, TaskHandler] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._processed_count = 0
        self._failed_count = 0

    def register(
        self,
        task_type: str,
        handler: TaskHandler,
    ) -> None:
        """Register a handler for a task type.

        Args:
            task_type: Task type identifier.
            handler: Async function that processes the task.
        """
        self._handlers[task_type] = handler
        logger.info("Registered handler for task type '%s'", task_type)

    async def start(self) -> None:
        """Start all workers as background asyncio tasks."""
        if self._running:
            logger.warning("WorkerPool already running")
            return

        self._running = True
        self._workers = [
            asyncio.create_task(
                self._worker_loop(worker_id=i),
                name=f"worker-{i}",
            )
            for i in range(self._concurrency)
        ]

        logger.info(
            "WorkerPool started with %d workers", self._concurrency,
        )

    async def stop(self, timeout: float = 10.0) -> None:
        """Gracefully stop all workers.

        Args:
            timeout: Max seconds to wait for workers to finish.
        """
        self._running = False

        if not self._workers:
            return

        done, pending = await asyncio.wait(
            self._workers, timeout=timeout,
        )
        for task in pending:
            task.cancel()

        self._workers.clear()
        logger.info(
            "WorkerPool stopped. Processed: %d, Failed: %d",
            self._processed_count,
            self._failed_count,
        )

    @property
    def is_running(self) -> bool:
        """Whether the worker pool is active."""
        return self._running

    @property
    def stats(self) -> dict[str, int]:
        """Worker pool statistics."""
        return {
            "concurrency": self._concurrency,
            "active_workers": len(self._workers),
            "processed": self._processed_count,
            "failed": self._failed_count,
            "handlers": len(self._handlers),
        }

    async def _worker_loop(self, worker_id: int) -> None:
        """Main worker loop: dequeue → execute → repeat.

        Args:
            worker_id: Worker identifier for logging.
        """
        logger.debug("Worker %d started", worker_id)

        while self._running:
            task = await self._queue.dequeue()
            if task is None:
                await asyncio.sleep(self._poll_interval)
                continue

            await self._process_task(task, worker_id)

        logger.debug("Worker %d stopped", worker_id)

    async def _process_task(
        self,
        task: Task,
        worker_id: int,
    ) -> None:
        """Execute a single task with error handling.

        Args:
            task: Task to process.
            worker_id: Worker identifier for logging.
        """
        handler = self._handlers.get(task.task_type)
        if handler is None:
            logger.error(
                "No handler for task type '%s' (task %s)",
                task.task_type,
                task.task_id,
            )
            await self._queue.requeue_or_dead_letter(task)
            self._failed_count += 1
            return

        try:
            await self._execute_handler(handler, task)
            self._processed_count += 1
            logger.debug(
                "Worker %d completed task %s (%s)",
                worker_id, task.task_id, task.task_type,
            )
        except Exception as exc:
            self._failed_count += 1
            logger.error(
                "Worker %d failed task %s: %s",
                worker_id, task.task_id, exc,
            )
            await self._queue.requeue_or_dead_letter(task)

    async def _execute_handler(
        self,
        handler: TaskHandler,
        task: Task,
    ) -> dict[str, Any]:
        """Execute handler with optional retry.

        Args:
            handler: Task handler function.
            task: Task to process.

        Returns:
            Handler result.
        """
        if self._default_retry:
            return await retry_async(
                self._default_retry, handler, task,
            )
        return await handler(task)
