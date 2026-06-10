"""Pregel Executor — BSP (Bulk Synchronous Parallel) graph runner.

Executes a graph in discrete supersteps: Plan → Execute (parallel)
→ Apply Writes → Checkpoint. Nodes run in parallel within each
superstep and see consistent channel state from the previous step.

Based on: LangGraph Pregel (langgraph/pregel/__init__.py).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from brain_engine.channels.base import BaseChannel
from brain_engine.pregel.algo import (
    apply_writes,
    finish_channels,
    prepare_next_tasks,
)
from brain_engine.pregel.read import read_available
from brain_engine.pregel.types import (
    Command,
    GraphInterrupt,
    Interrupt,
    PregelTask,
    Send,
    TaskWrites,
)
from brain_engine.pregel.write import ChannelWriteEntry, collect_writes

logger = logging.getLogger(__name__)


@dataclass
class PregelNode:
    """A node in the Pregel graph with its channel wiring.

    Attributes:
        name: Node identifier.
        func: Async function to execute.
        channels: Channel names this node reads from.
        triggers: Channel names that trigger this node.
        writers: How to write output back to channels.
        interrupt_before: Pause execution before this node runs.
        interrupt_after: Pause execution after this node completes.
    """

    name: str = ""
    func: Callable[..., Awaitable[Any]] | Callable[..., Any] | None = None
    channels: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    writers: list[ChannelWriteEntry] = field(default_factory=list)
    interrupt_before: bool = False
    interrupt_after: bool = False


@dataclass
class PregelResult:
    """Result of a complete Pregel execution.

    Attributes:
        values: Final channel values.
        steps_executed: Number of supersteps completed.
        elapsed_ms: Total execution time.
        status: Outcome (completed, max_steps, interrupted).
        interrupt_value: Value from interrupt if paused.
    """

    values: dict[str, Any] = field(default_factory=dict)
    steps_executed: int = 0
    elapsed_ms: int = 0
    status: str = "completed"
    interrupt_value: Any = None


class PregelExecutor:
    """BSP execution engine for channel-based graphs.

    Runs the Plan → Execute → Update loop until no more nodes
    are triggered or max_steps is reached. Supports
    interrupt_before/after for human-in-the-loop workflows.

    Args:
        nodes: Dict of node_name -> PregelNode.
        channels: Dict of channel_name -> BaseChannel.
        max_steps: Maximum supersteps before stopping.
        interrupt_before: Global list of nodes to pause before.
        interrupt_after: Global list of nodes to pause after.
    """

    def __init__(
        self,
        nodes: dict[str, PregelNode],
        channels: dict[str, BaseChannel[Any, Any, Any]],
        max_steps: int = 25,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
    ) -> None:
        self._nodes = nodes
        self._channels = channels
        self._max_steps = max_steps
        self._trigger_map = _build_trigger_map(nodes)
        self._channel_versions: dict[str, int] = {
            k: 0 for k in channels
        }
        self._apply_interrupt_config(interrupt_before, interrupt_after)
        self._resumed_from: str | None = None

    def _apply_interrupt_config(
        self,
        interrupt_before: list[str] | None,
        interrupt_after: list[str] | None,
    ) -> None:
        """Apply global interrupt config to matching nodes.

        Args:
            interrupt_before: Node names to pause before execution.
            interrupt_after: Node names to pause after execution.
        """
        for name in (interrupt_before or []):
            if name in self._nodes:
                self._nodes[name].interrupt_before = True
        for name in (interrupt_after or []):
            if name in self._nodes:
                self._nodes[name].interrupt_after = True

    async def run(
        self,
        inputs: dict[str, Any] | None = None,
        *,
        command: Command | None = None,
    ) -> PregelResult:
        """Execute the graph to completion.

        Args:
            inputs: Initial values to write to channels.
            command: Resume command after an interrupt.

        Returns:
            PregelResult with final state and metadata.
        """
        start = time.monotonic()

        if command is not None:
            updated = self._handle_resume(command, inputs)
        else:
            updated = self._initialize(inputs or {})

        result = await self._run_loop(updated)

        result.elapsed_ms = int((time.monotonic() - start) * 1000)
        result.values = self._read_output()
        return result

    def _handle_resume(
        self,
        command: Command,
        inputs: dict[str, Any] | None,
    ) -> set[str]:
        """Handle resumption from an interrupted state.

        Writes the resume value to channels and marks the node
        to skip its interrupt_before check on re-entry.

        Args:
            command: Resume command with value and optional goto.
            inputs: Additional inputs to write.

        Returns:
            Set of updated channel names.
        """
        self._resumed_from = command.goto
        updated = self._initialize(inputs or {})
        if command.resume is not None and isinstance(command.resume, dict):
            for name, value in command.resume.items():
                ch = self._channels.get(name)
                if ch and ch.update([value]):
                    self._channel_versions[name] += 1
                    updated.add(name)
        return updated

    # ── Execution loop ────────────────────────────────────────────────

    async def _run_loop(
        self,
        updated_channels: set[str],
    ) -> PregelResult:
        """Core BSP loop: plan → execute → update.

        Args:
            updated_channels: Initially updated channels.

        Returns:
            PregelResult.
        """
        task_results: list[TaskWrites] = []

        for step in range(self._max_steps):
            tasks = prepare_next_tasks(
                self._channels, self._trigger_map,
                updated_channels, task_results,
                step, self._max_steps,
            )

            if not tasks:
                return PregelResult(
                    steps_executed=step,
                    status="completed",
                )

            try:
                task_results = await self._execute_step(tasks)
            except GraphInterrupt as exc:
                return PregelResult(
                    steps_executed=step + 1,
                    status="interrupted",
                    interrupt_value=exc.value,
                )

            updated_channels = apply_writes(
                self._channels, task_results,
                self._channel_versions,
            )

        finish_channels(self._channels, self._channel_versions)
        return PregelResult(
            steps_executed=self._max_steps,
            status="max_steps",
        )

    async def _execute_step(
        self,
        tasks: list[PregelTask],
    ) -> list[TaskWrites]:
        """Execute all tasks in parallel (one superstep).

        Args:
            tasks: Tasks to execute.

        Returns:
            Collected writes from all tasks.
        """
        coros = [self._execute_task(task) for task in tasks]
        return list(await asyncio.gather(*coros))

    async def _execute_task(
        self,
        task: PregelTask,
    ) -> TaskWrites:
        """Execute a single task with interrupt enforcement.

        Checks interrupt_before before execution and interrupt_after
        after execution. Raises GraphInterrupt to pause the graph
        when an interrupt point is reached.

        Args:
            task: Task to execute.

        Returns:
            TaskWrites with channel writes and sends.
        """
        node = self._nodes.get(task.node)
        if node is None:
            logger.warning("Unknown node in task: %s", task.node)
            return TaskWrites(node=task.node)

        self._check_interrupt_before(node)

        input_data = self._build_input(node, task)
        output = await _invoke_node(node, input_data)

        write_specs = node.writers or _default_writers(node.channels)
        writes = collect_writes(node.name, output, write_specs)

        self._check_interrupt_after(node, output)

        return writes

    def _check_interrupt_before(self, node: PregelNode) -> None:
        """Raise GraphInterrupt if node has interrupt_before set.

        Skips the check if this node is being resumed via Command.

        Args:
            node: Node about to execute.

        Raises:
            GraphInterrupt: If the node should pause before running.
        """
        if not node.interrupt_before:
            return
        if self._resumed_from == node.name:
            self._resumed_from = None
            return

        logger.info("Interrupt BEFORE node: %s", node.name)
        exc = GraphInterrupt(
            value={"node": node.name, "reason": "interrupt_before"},
            node=node.name,
            interrupt_type="before",
        )
        exc.add_interrupt(Interrupt(
            value=f"Paused before '{node.name}'",
            node=node.name,
            interrupt_type="before",
        ))
        raise exc

    def _check_interrupt_after(
        self,
        node: PregelNode,
        output: Any,
    ) -> None:
        """Raise GraphInterrupt if node has interrupt_after set.

        Args:
            node: Node that just finished executing.
            output: The node's output value.

        Raises:
            GraphInterrupt: If the node should pause after running.
        """
        if not node.interrupt_after:
            return

        logger.info("Interrupt AFTER node: %s", node.name)
        exc = GraphInterrupt(
            value={
                "node": node.name,
                "reason": "interrupt_after",
                "output": str(output)[:500],
            },
            node=node.name,
            interrupt_type="after",
        )
        exc.add_interrupt(Interrupt(
            value=f"Paused after '{node.name}'",
            node=node.name,
            interrupt_type="after",
        ))
        raise exc

    # ── Helpers ───────────────────────────────────────────────────────

    def _initialize(
        self,
        inputs: dict[str, Any],
    ) -> set[str]:
        """Write initial inputs to channels.

        Args:
            inputs: channel_name -> value pairs.

        Returns:
            Set of updated channel names.
        """
        updated: set[str] = set()
        for name, value in inputs.items():
            ch = self._channels.get(name)
            if ch and ch.update([value]):
                self._channel_versions[name] = 1
                updated.add(name)
        return updated

    def _build_input(
        self,
        node: PregelNode,
        task: PregelTask,
    ) -> Any:
        """Build input for a node from channels or Send arg.

        Args:
            node: Node definition.
            task: Current task.

        Returns:
            Input data for the node.
        """
        if task.input_data is not None:
            return task.input_data
        return read_available(self._channels, node.channels)

    def _read_output(self) -> dict[str, Any]:
        """Read final values from all available channels.

        Returns:
            Dict of channel values.
        """
        return read_available(
            self._channels, list(self._channels.keys()),
        )


# ── Module-level helpers ──────────────────────────────────────────── #


async def _invoke_node(node: PregelNode, input_data: Any) -> Any:
    """Invoke a node function (sync or async).

    Args:
        node: Node with func.
        input_data: Input to pass.

    Returns:
        Node output.
    """
    if node.func is None:
        return input_data

    result = node.func(input_data)
    if asyncio.iscoroutine(result):
        return await result
    return result


def _build_trigger_map(
    nodes: dict[str, PregelNode],
) -> dict[str, list[str]]:
    """Build channel -> triggered nodes mapping.

    Args:
        nodes: All graph nodes.

    Returns:
        Dict of channel_name -> [node_names].
    """
    trigger_map: dict[str, list[str]] = {}
    for name, node in nodes.items():
        for trigger in node.triggers:
            trigger_map.setdefault(trigger, []).append(name)
    return trigger_map


def _default_writers(
    channels: list[str],
) -> list[ChannelWriteEntry]:
    """Generate default write specs (output dict keys → channels).

    Args:
        channels: Channel names to write to.

    Returns:
        Write specs for each channel.
    """
    return [ChannelWriteEntry(channel=ch) for ch in channels]
