"""Pregel algorithms — apply_writes and prepare_next_tasks.

Core BSP algorithms that manage channel state transitions between
supersteps. apply_writes() atomically updates channels from all
task outputs. prepare_next_tasks() determines which nodes to
execute in the next superstep based on channel changes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from brain_engine.channels.base import BaseChannel
from brain_engine.pregel.types import PregelTask, Send, TaskWrites

logger = logging.getLogger(__name__)


def apply_writes(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    task_results: list[TaskWrites],
    channel_versions: dict[str, int],
) -> set[str]:
    """Apply all writes from a superstep to channels atomically.

    Groups writes by channel, calls channel.update(), increments
    versions for changed channels, and returns the set of updated
    channel names.

    Args:
        channels: All channels in the graph.
        task_results: Writes collected from each task.
        channel_versions: Mutable version tracker.

    Returns:
        Set of channel names that changed.
    """
    pending = _group_writes_by_channel(task_results)
    updated = _apply_pending(channels, pending, channel_versions)
    _consume_triggers(channels, task_results, channel_versions, updated)
    return updated


def prepare_next_tasks(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    trigger_map: dict[str, list[str]],
    updated_channels: set[str],
    task_results: list[TaskWrites],
    step: int,
    max_steps: int,
) -> list[PregelTask]:
    """Determine which nodes execute in the next superstep.

    Maps updated channels to triggered nodes, adds Send fan-outs,
    and creates PregelTask objects for each.

    Args:
        channels: All channels.
        trigger_map: channel_name -> [node_names] mapping.
        updated_channels: Channels changed in last superstep.
        task_results: Previous step results (for Send extraction).
        step: Current step number.
        max_steps: Maximum allowed steps.

    Returns:
        List of tasks to execute next.
    """
    if step >= max_steps:
        return []

    triggered = _find_triggered_nodes(trigger_map, updated_channels)
    send_tasks = _collect_send_tasks(task_results, step)
    node_tasks = _build_node_tasks(triggered, channels, step)

    return node_tasks + send_tasks


def finish_channels(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    channel_versions: dict[str, int],
) -> set[str]:
    """Call finish() on all channels at end of execution.

    Args:
        channels: All channels.
        channel_versions: Version tracker.

    Returns:
        Set of channels that became newly available.
    """
    newly_available: set[str] = set()
    for name, ch in channels.items():
        if ch.finish():
            channel_versions[name] = channel_versions.get(name, 0) + 1
            newly_available.add(name)
    return newly_available


# ── Internal: apply_writes helpers ────────────────────────────────── #


def _group_writes_by_channel(
    task_results: list[TaskWrites],
) -> dict[str, list[Any]]:
    """Group all writes by target channel.

    Args:
        task_results: Writes from all tasks.

    Returns:
        Dict of channel_name -> [values].
    """
    pending: dict[str, list[Any]] = defaultdict(list)
    for result in task_results:
        for channel_name, value in result.writes:
            pending[channel_name].append(value)
    return dict(pending)


def _apply_pending(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    pending: dict[str, list[Any]],
    channel_versions: dict[str, int],
) -> set[str]:
    """Apply grouped writes to channels.

    Args:
        channels: All channels.
        pending: Grouped writes.
        channel_versions: Version tracker.

    Returns:
        Set of changed channel names.
    """
    updated: set[str] = set()
    for chan_name, values in pending.items():
        ch = channels.get(chan_name)
        if ch is None:
            logger.warning("Write to unknown channel: %s", chan_name)
            continue
        if ch.update(values):
            channel_versions[chan_name] = (
                channel_versions.get(chan_name, 0) + 1
            )
            updated.add(chan_name)
    return updated


def _consume_triggers(
    channels: dict[str, BaseChannel[Any, Any, Any]],
    task_results: list[TaskWrites],
    channel_versions: dict[str, int],
    updated: set[str],
) -> None:
    """Consume channels that were read by tasks (one-time triggers).

    Args:
        channels: All channels.
        task_results: Tasks that ran (to find their triggers).
        channel_versions: Version tracker.
        updated: Set to add newly consumed channels to.
    """
    consumed_names: set[str] = set()
    for result in task_results:
        for name in _get_trigger_channels(result):
            consumed_names.add(name)

    for name in consumed_names:
        ch = channels.get(name)
        if ch and ch.consume():
            channel_versions[name] = channel_versions.get(name, 0) + 1
            updated.add(name)


def _get_trigger_channels(result: TaskWrites) -> list[str]:
    """Extract channel names that triggered a task.

    Args:
        result: Task write result.

    Returns:
        List of channel names (from write targets as proxy).
    """
    return [ch for ch, _ in result.writes]


# ── Internal: prepare_next_tasks helpers ──────────────────────────── #


def _find_triggered_nodes(
    trigger_map: dict[str, list[str]],
    updated_channels: set[str],
) -> set[str]:
    """Find nodes triggered by channel updates.

    Args:
        trigger_map: channel -> [nodes] mapping.
        updated_channels: Changed channels.

    Returns:
        Set of node names to execute.
    """
    triggered: set[str] = set()
    for chan in updated_channels:
        for node in trigger_map.get(chan, []):
            triggered.add(node)
    return triggered


def _collect_send_tasks(
    task_results: list[TaskWrites],
    step: int,
) -> list[PregelTask]:
    """Create tasks from Send fan-outs.

    Args:
        task_results: Previous step results.
        step: Current step number.

    Returns:
        Tasks for each Send.
    """
    tasks: list[PregelTask] = []
    for result in task_results:
        for i, send in enumerate(result.sends):
            tasks.append(PregelTask(
                task_id=f"send_{send.node}_{step}_{i}",
                node=send.node,
                input_data=send.arg,
                triggers=[f"send_from_{result.node}"],
            ))
    return tasks


def _build_node_tasks(
    triggered: set[str],
    channels: dict[str, BaseChannel[Any, Any, Any]],
    step: int,
) -> list[PregelTask]:
    """Build tasks for triggered nodes.

    Args:
        triggered: Node names to execute.
        channels: For reading input.
        step: Current step number.

    Returns:
        List of PregelTasks.
    """
    return [
        PregelTask(
            task_id=f"{node}_{step}",
            node=node,
            triggers=[],
        )
        for node in sorted(triggered)
    ]
