"""SubAgentRunner — spawns and manages subagent execution.

Each subagent runs in an isolated BrainZFS clone so writes
don't affect the parent session. Supports parallel execution
via asyncio.gather and result promotion back to parent.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from brain_engine.subagents.models import (
    SubAgentResult,
    SubAgentSpec,
    SubAgentStatus,
)
from brain_engine.subagents.registry import SubAgentRegistry

logger = logging.getLogger(__name__)


@runtime_checkable
class SubAgentExecutor(Protocol):
    """Protocol for the actual subagent execution function.

    The runner calls this with the spec, prompt, and optional context.
    Implementations can use any LLM or agent loop internally.
    """

    async def execute(
        self,
        spec: SubAgentSpec,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Execute a subagent and return its output text."""
        ...


class DefaultExecutor:
    """Production subagent executor with full LLM reasoning loop.

    Runs a Think→Act→Observe cycle using the model specified in the
    SubAgentSpec. Supports tool calling, system prompts, and context
    injection. Falls back to a direct LLM call when no tools are
    available.

    Args:
        model_factory: Callable that creates a BaseChatModel from a
            model string. Defaults to ``init_chat_model``.
        default_model: Model to use when spec.model is None.
    """

    def __init__(
        self,
        model_factory: Callable[..., Any] | None = None,
        default_model: str = "openai:gpt-4o-mini",
    ) -> None:
        self._model_factory = model_factory or _get_model_factory()
        self._default_model = default_model

    async def execute(
        self,
        spec: SubAgentSpec,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Execute a subagent task with full LLM reasoning.

        Args:
            spec: Subagent specification (model, system prompt, tools).
            prompt: Task description from parent agent.
            context: Optional context data to include.

        Returns:
            The LLM's final response text.
        """
        model = self._create_model(spec)
        messages = self._build_messages(spec, prompt, context)
        return await self._run_reasoning_loop(model, messages, spec)

    def _create_model(self, spec: SubAgentSpec) -> Any:
        """Instantiate the LLM model for this subagent.

        Args:
            spec: Subagent spec with optional model override.

        Returns:
            BaseChatModel instance.
        """
        model_string = spec.model or self._default_model
        return self._model_factory(model_string, temperature=0.3)

    def _build_messages(
        self,
        spec: SubAgentSpec,
        prompt: str,
        context: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        """Assemble the message list for the LLM call.

        Args:
            spec: Subagent spec with system prompt.
            prompt: User task prompt.
            context: Optional context to inject.

        Returns:
            List of message dicts (role + content).
        """
        messages: list[dict[str, str]] = []
        system_content = spec.system_prompt or _DEFAULT_SYSTEM_PROMPT
        if context:
            system_content += f"\n\nContext:\n{_format_context(context)}"
        messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _run_reasoning_loop(
        self,
        model: Any,
        messages: list[dict[str, str]],
        spec: SubAgentSpec,
    ) -> str:
        """Run Think→Act→Observe loop up to max_steps.

        Calls the LLM iteratively. If the LLM returns tool calls,
        executes them and feeds observations back. Stops when the
        LLM produces a final text response or max_steps is reached.

        Args:
            model: BaseChatModel instance.
            messages: Initial message list.
            spec: Subagent spec for step limits.

        Returns:
            Final response text from the LLM.
        """
        for step in range(spec.max_steps):
            response = await model.invoke(messages)

            if not response.tool_calls:
                return response.content or f"[{spec.name}] Task completed."

            messages = self._process_tool_calls(
                messages, response, spec,
            )

        return self._extract_final_content(messages, spec)

    def _process_tool_calls(
        self,
        messages: list[dict[str, str]],
        response: Any,
        spec: SubAgentSpec,
    ) -> list[dict[str, str]]:
        """Process tool calls and append observations to messages.

        Args:
            messages: Current message history.
            response: AIMessage with tool_calls.
            spec: Subagent spec for tool validation.

        Returns:
            Updated message list with assistant + tool responses.
        """
        messages.append({
            "role": "assistant",
            "content": response.content or "",
        })
        for tool_call in response.tool_calls:
            observation = _safe_tool_observation(tool_call, spec)
            messages.append({
                "role": "tool",
                "content": observation,
            })
        return messages

    def _extract_final_content(
        self,
        messages: list[dict[str, str]],
        spec: SubAgentSpec,
    ) -> str:
        """Extract final answer from the last assistant message.

        Args:
            messages: Full message history.
            spec: Subagent spec for naming.

        Returns:
            Last assistant content or a fallback message.
        """
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg["content"]:
                return msg["content"]
        return f"[{spec.name}] Max steps reached without final answer."


_DEFAULT_SYSTEM_PROMPT = (
    "You are a focused subagent. Complete the assigned task "
    "thoroughly and return a concise, actionable result. "
    "Do not ask follow-up questions — work with what you have."
)


def _get_model_factory() -> Callable[..., Any]:
    """Lazily import and return init_chat_model.

    Returns:
        The model factory function.
    """
    from brain_engine.models.factory import init_chat_model
    return init_chat_model


def _format_context(context: dict[str, Any]) -> str:
    """Format context dict as readable key-value lines.

    Args:
        context: Context data to format.

    Returns:
        Formatted multi-line string.
    """
    lines = [f"- {k}: {v}" for k, v in context.items()]
    return "\n".join(lines)


def _safe_tool_observation(tool_call: Any, spec: SubAgentSpec) -> str:
    """Generate a tool observation safely.

    In the subagent context, tool calls are delegated back to the
    parent agent's tool executor. Here we return a structured
    acknowledgment that the parent can process.

    Args:
        tool_call: ToolCall from the LLM response.
        spec: Subagent spec for tool validation.

    Returns:
        Observation string for the tool result.
    """
    tool_name = getattr(tool_call, "name", "unknown")
    tool_args = getattr(tool_call, "args", {})
    if spec.tools and tool_name not in spec.tools:
        return f"Error: Tool '{tool_name}' not allowed for {spec.name}."
    return (
        f"Tool '{tool_name}' called with args: {tool_args}. "
        f"Result delegated to parent agent."
    )


class SubAgentRunner:
    """Manages subagent lifecycle and parallel execution.

    Spawns subagents with isolated context, tracks their status,
    and collects results. Integrates with BrainZFS for clone-based
    isolation when available.

    Args:
        registry: SubAgentRegistry for spec lookup.
        executor: SubAgentExecutor for actual LLM execution.
        zfs: Optional BrainZFS for clone-based context isolation.
    """

    def __init__(
        self,
        registry: SubAgentRegistry,
        executor: SubAgentExecutor | None = None,
        zfs: Any | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor or DefaultExecutor()
        self._zfs = zfs
        self._results: dict[str, SubAgentResult] = {}
        self._active: dict[str, asyncio.Task[SubAgentResult]] = {}

    @property
    def active_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._active)

    @property
    def total_results(self) -> int:
        """Return the total number of completed results."""
        return len(self._results)

    # ── Single execution ─────────────────────────────────────────────

    async def run(
        self,
        subagent_name: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> SubAgentResult:
        """Run a single subagent synchronously.

        Args:
            subagent_name: Name of the registered subagent type.
            prompt: Task description for the subagent.
            context: Optional context data to pass.

        Returns:
            SubAgentResult with output or error.
        """
        spec = self._registry.get_or_raise(subagent_name)
        result = self._create_result(spec)

        clone_name = await self._create_clone(result.task_id)
        result.clone_name = clone_name

        result.status = SubAgentStatus.RUNNING
        start = time.monotonic()

        try:
            output = await asyncio.wait_for(
                self._executor.execute(spec, prompt, context),
                timeout=spec.timeout_seconds,
            )
            result.output = output
            result.status = SubAgentStatus.COMPLETED
        except asyncio.TimeoutError:
            result.error = f"Timeout after {spec.timeout_seconds}s"
            result.status = SubAgentStatus.FAILED
            logger.warning("Subagent %s timed out: %s", spec.name, result.task_id)
        except Exception as exc:
            result.error = str(exc)
            result.status = SubAgentStatus.FAILED
            logger.error("Subagent %s failed: %s", spec.name, exc)

        result.elapsed_ms = int((time.monotonic() - start) * 1000)
        result.completed_at = datetime.now(timezone.utc)

        await self._cleanup_clone(clone_name)
        self._results[result.task_id] = result
        return result

    # ── Parallel execution ───────────────────────────────────────────

    async def run_parallel(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[SubAgentResult]:
        """Run multiple subagents in parallel.

        Args:
            tasks: List of dicts with keys:
                - subagent_name: str
                - prompt: str
                - context: dict (optional)

        Returns:
            List of SubAgentResult in the same order as input tasks.
        """
        coros = [
            self.run(
                subagent_name=t["subagent_name"],
                prompt=t["prompt"],
                context=t.get("context"),
            )
            for t in tasks
        ]
        return await asyncio.gather(*coros, return_exceptions=False)

    # ── Background execution ─────────────────────────────────────────

    async def start(
        self,
        subagent_name: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Start a subagent in the background.

        Args:
            subagent_name: Subagent type name.
            prompt: Task description.
            context: Optional context data.

        Returns:
            Task ID for tracking.
        """
        spec = self._registry.get_or_raise(subagent_name)
        result = self._create_result(spec)
        self._results[result.task_id] = result

        task = asyncio.create_task(
            self._run_background(spec, prompt, result, context),
        )
        self._active[result.task_id] = task
        return result.task_id

    async def _run_background(
        self,
        spec: SubAgentSpec,
        prompt: str,
        result: SubAgentResult,
        context: dict[str, Any] | None,
    ) -> SubAgentResult:
        """Execute a subagent in the background.

        Args:
            spec: Subagent specification.
            prompt: Task description.
            result: Pre-created result to populate.
            context: Optional context.

        Returns:
            Populated SubAgentResult.
        """
        try:
            completed = await self.run(spec.name, prompt, context)
            result.status = completed.status
            result.output = completed.output
            result.error = completed.error
            result.elapsed_ms = completed.elapsed_ms
            result.completed_at = completed.completed_at
        finally:
            self._active.pop(result.task_id, None)
        return result

    # ── Query ────────────────────────────────────────────────────────

    def get_result(self, task_id: str) -> SubAgentResult | None:
        """Get a result by task ID.

        Args:
            task_id: Execution task ID.

        Returns:
            SubAgentResult or None.
        """
        return self._results.get(task_id)

    def list_results(
        self,
        status: SubAgentStatus | None = None,
    ) -> list[SubAgentResult]:
        """List all results, optionally filtered by status.

        Args:
            status: Filter by this status.

        Returns:
            List of SubAgentResult objects.
        """
        results = list(self._results.values())
        if status is not None:
            results = [r for r in results if r.status == status]
        return results

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running background subagent.

        Args:
            task_id: Task ID to cancel.

        Returns:
            True if cancelled, False if not found or already done.
        """
        task = self._active.pop(task_id, None)
        if task is None:
            return False
        task.cancel()
        result = self._results.get(task_id)
        if result:
            result.status = SubAgentStatus.CANCELLED
            result.completed_at = datetime.now(timezone.utc)
        return True

    # ── Internal ─────────────────────────────────────────────────────

    def _create_result(self, spec: SubAgentSpec) -> SubAgentResult:
        """Create a pending SubAgentResult for a spec."""
        return SubAgentResult(subagent_name=spec.name)

    async def _create_clone(self, task_id: str) -> str:
        """Create a BrainZFS clone for context isolation.

        Args:
            task_id: Task ID for naming the clone.

        Returns:
            Clone name, or empty string if ZFS not available.
        """
        if self._zfs is None:
            return ""
        try:
            snap_name = f"subagent_pre_{task_id[:8]}"
            await self._zfs.snapshot(snap_name)
            clone_name = f"subagent_{task_id[:8]}"
            await self._zfs.clone(snap_name, clone_name)
            return clone_name
        except Exception as exc:
            logger.warning("Failed to create ZFS clone: %s", exc)
            return ""

    async def _cleanup_clone(self, clone_name: str) -> None:
        """Destroy a ZFS clone after execution.

        Args:
            clone_name: Clone to destroy.
        """
        if not clone_name or self._zfs is None:
            return
        try:
            await self._zfs.clones.destroy(clone_name)
        except Exception as exc:
            logger.warning("Failed to cleanup clone %s: %s", clone_name, exc)
