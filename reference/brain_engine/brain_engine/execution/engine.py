"""ExecutionEngine — main agent loop with superstep execution.

Orchestrates the think→act→observe cycle. Handles tool execution,
interrupt detection, retry policies, max iteration guards, and
timeout enforcement.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from brain_engine.execution.models import (
    AgentAction,
    AgentFinish,
    AgentStep,
    ExecutionConfig,
    ExecutionResult,
    StepType,
)
from brain_engine.execution.policies import (
    RetryPolicy,
    StepCache,
    execute_with_retry,
)
from brain_engine.execution.runtime import ExecutionInfo, Runtime
from brain_engine.execution.steps import StepCollector
from brain_engine.interrupts.primitives import InterruptError

logger = logging.getLogger(__name__)


@runtime_checkable
class Planner(Protocol):
    """Protocol for the agent's planning function (LLM call).

    Given intermediate steps, decides the next action or finishes.
    """

    async def plan(
        self,
        intermediate_steps: list[tuple[dict[str, Any], str]],
        **kwargs: Any,
    ) -> AgentAction | AgentFinish:
        """Decide the next action based on history."""
        ...


@runtime_checkable
class ToolExecutor(Protocol):
    """Protocol for tool execution."""

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        """Execute a tool and return the observation."""
        ...


class ExecutionEngine:
    """Main agent execution loop.

    Runs the think→act→observe cycle until the agent finishes,
    hits max iterations, times out, or is interrupted.

    Args:
        planner: The LLM planning function.
        tool_executor: Tool execution handler.
        config: Execution configuration.
        retry_policy: Default retry policy for tool calls.
        runtime: Runtime context to inject.
    """

    def __init__(
        self,
        planner: Planner,
        tool_executor: ToolExecutor,
        config: ExecutionConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        runtime: Runtime[Any] | None = None,
    ) -> None:
        self._planner = planner
        self._tool_executor = tool_executor
        self._config = config or ExecutionConfig()
        self._retry_policy = retry_policy or RetryPolicy()
        self._runtime = runtime or Runtime()
        self._cache = StepCache()

    # ── Main execution ───────────────────────────────────────────────

    async def run(
        self,
        inputs: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute the full agent loop.

        Args:
            inputs: Initial inputs for the agent.

        Returns:
            ExecutionResult with output, steps, and metadata.
        """
        run_id = str(uuid.uuid4())
        self._runtime.execution_info.run_id = run_id
        collector = StepCollector()
        start_time = time.monotonic()
        inputs = inputs or {}

        result = await self._execute_loop(
            inputs, collector, start_time, run_id,
        )

        result.elapsed_ms = int((time.monotonic() - start_time) * 1000)
        result.run_id = run_id
        if self._config.return_intermediate_steps:
            result.intermediate_steps = collector.steps
        return result

    async def _execute_loop(
        self,
        inputs: dict[str, Any],
        collector: StepCollector,
        start_time: float,
        run_id: str,
    ) -> ExecutionResult:
        """Core execution loop.

        Args:
            inputs: Agent inputs.
            collector: Step collector.
            start_time: Loop start timestamp.
            run_id: Run identifier.

        Returns:
            ExecutionResult.
        """
        for iteration in range(1, self._config.max_iterations + 1):
            if self._is_timed_out(start_time):
                return self._timeout_result(collector, iteration)

            self._update_runtime(iteration, run_id)
            step_result = await self._execute_step(
                inputs, collector, iteration, start_time,
            )

            if step_result is not None:
                step_result.iterations = iteration
                return step_result

        return self._max_iterations_result(collector)

    async def _execute_step(
        self,
        inputs: dict[str, Any],
        collector: StepCollector,
        iteration: int,
        start_time: float = 0,
    ) -> ExecutionResult | None:
        """Execute a single think→act→observe step.

        Args:
            inputs: Agent inputs.
            collector: Step collector.
            iteration: Current iteration number.
            start_time: Loop start timestamp for timeout checks.

        Returns:
            ExecutionResult if finished/interrupted, None to continue.
        """
        trajectory = collector.to_trajectory()

        remaining_time = self._config.max_execution_time_seconds - (
            time.monotonic() - start_time
        )
        if remaining_time <= 0:
            return self._timeout_result(collector, iteration)

        try:
            decision = await asyncio.wait_for(
                self._planner.plan(
                    intermediate_steps=trajectory, **inputs,
                ),
                timeout=remaining_time,
            )
        except asyncio.TimeoutError:
            return self._timeout_result(collector, iteration)
        except InterruptError as exc:
            return self._interrupt_result(exc, collector)
        except Exception as exc:
            if self._config.handle_parsing_errors:
                return self._error_result(str(exc), collector)
            raise

        if isinstance(decision, AgentFinish):
            return self._finish_result(decision, collector)

        observation = await self._execute_tool(decision, collector)
        return None

    async def _execute_tool(
        self,
        action: AgentAction,
        collector: StepCollector,
    ) -> str:
        """Execute a tool call with retry policy.

        Args:
            action: The agent's tool call decision.
            collector: Step collector.

        Returns:
            Tool observation string.
        """
        step_start = time.monotonic()

        try:
            observation = await execute_with_retry(
                self._tool_executor.execute,
                self._retry_policy,
                action.tool,
                action.tool_input,
            )
        except InterruptError:
            raise
        except Exception as exc:
            observation = f"Error: {exc}"
            logger.warning("Tool %s failed: %s", action.tool, exc)

        elapsed = int((time.monotonic() - step_start) * 1000)
        collector.add(action, str(observation), StepType.ACTION, elapsed)
        return str(observation)

    # ── Result builders ──────────────────────────────────────────────

    def _finish_result(
        self,
        finish: AgentFinish,
        collector: StepCollector,
    ) -> ExecutionResult:
        """Build result for successful completion."""
        return ExecutionResult(
            output=finish.output,
            return_values=finish.return_values,
            status="completed",
            iterations=collector.count,
        )

    def _interrupt_result(
        self,
        error: InterruptError,
        collector: StepCollector,
    ) -> ExecutionResult:
        """Build result for an interrupt."""
        return ExecutionResult(
            status="interrupted",
            interrupt_value=error.value,
            iterations=collector.count,
        )

    def _timeout_result(
        self,
        collector: StepCollector,
        iteration: int,
    ) -> ExecutionResult:
        """Build result for timeout."""
        return ExecutionResult(
            status="timeout",
            error=f"Execution timed out after {self._config.max_execution_time_seconds}s",
            iterations=iteration,
        )

    def _max_iterations_result(
        self,
        collector: StepCollector,
    ) -> ExecutionResult:
        """Build result for max iterations reached."""
        if self._config.early_stopping_method == "force":
            return ExecutionResult(
                status="max_iterations",
                output="Agent stopped: max iterations reached.",
                error=f"Reached {self._config.max_iterations} iterations",
                iterations=self._config.max_iterations,
            )
        return ExecutionResult(
            status="max_iterations",
            error=f"Reached {self._config.max_iterations} iterations",
            iterations=self._config.max_iterations,
        )

    def _error_result(
        self,
        error_msg: str,
        collector: StepCollector,
    ) -> ExecutionResult:
        """Build result for a handled error."""
        return ExecutionResult(
            status="error",
            error=error_msg,
            iterations=collector.count,
        )

    # ── Helpers ───────────────────────────────────────────────────────

    def _is_timed_out(self, start_time: float) -> bool:
        """Check if execution has exceeded the time limit."""
        elapsed = time.monotonic() - start_time
        return elapsed > self._config.max_execution_time_seconds

    def _update_runtime(self, iteration: int, run_id: str) -> None:
        """Update runtime execution info for the current step."""
        remaining = self._config.max_iterations - iteration
        self._runtime.execution_info.step_number = iteration
        self._runtime.execution_info.remaining_steps = remaining
        self._runtime.execution_info.run_id = run_id
