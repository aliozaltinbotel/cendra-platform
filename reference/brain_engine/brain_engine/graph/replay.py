"""Replay / Time Travel — re-execute graph from any checkpoint.

Enables debugging and auditing by replaying graph execution from
a saved checkpoint. Supports forking (branch from a past state)
and comparison (run same input with different config).

Example::

    # Get checkpoint history
    history = await graph.get_state_history(config)

    # Replay from 3rd checkpoint
    result = await replay_from(graph, history[2], config)

    # Fork: modify state and run from a past point
    result = await fork_from(
        graph, history[1],
        updates={"approved": True},
        config=config,
    )

Based on: LangGraph time travel / replay.
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    """Result of a replay or fork execution.

    Attributes:
        final_state: State after replay completes.
        steps_replayed: Number of steps executed.
        forked_from: Checkpoint ID this was forked from.
        original_state: State at the fork point.
        new_run_id: ID for this replay run.
    """

    final_state: dict[str, Any] = field(default_factory=dict)
    steps_replayed: int = 0
    forked_from: str = ""
    original_state: dict[str, Any] = field(default_factory=dict)
    new_run_id: str = field(
        default_factory=lambda: str(uuid.uuid4())[:12],
    )


async def replay_from(
    graph: Any,
    checkpoint_state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> ReplayResult:
    """Replay graph execution from a checkpoint state.

    Restarts execution using the checkpoint's state as input.
    The graph runs to completion from that point.

    Args:
        graph: CompiledGraph instance.
        checkpoint_state: State to start from.
        config: Execution config (new thread_id recommended).

    Returns:
        ReplayResult with final state.
    """
    replay_config = _make_replay_config(config)
    initial_state = copy.deepcopy(checkpoint_state)

    result_state = await graph.ainvoke(initial_state, replay_config)

    return ReplayResult(
        final_state=result_state,
        steps_replayed=_count_state_changes(
            initial_state, result_state,
        ),
        original_state=initial_state,
    )


async def fork_from(
    graph: Any,
    checkpoint_state: dict[str, Any],
    updates: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> ReplayResult:
    """Fork from a checkpoint with state modifications.

    Takes a past state, applies updates, then runs the graph
    from the modified state. Useful for "what if" analysis.

    Args:
        graph: CompiledGraph instance.
        checkpoint_state: State to fork from.
        updates: State modifications to apply before running.
        config: Execution config.

    Returns:
        ReplayResult with forked execution state.
    """
    forked_state = _apply_updates(checkpoint_state, updates)
    fork_config = _make_fork_config(config)

    result_state = await graph.ainvoke(forked_state, fork_config)

    checkpoint_id = _extract_checkpoint_id(config)
    return ReplayResult(
        final_state=result_state,
        steps_replayed=_count_state_changes(
            forked_state, result_state,
        ),
        forked_from=checkpoint_id,
        original_state=checkpoint_state,
    )


async def compare_runs(
    graph: Any,
    input_data: dict[str, Any],
    configs: list[dict[str, Any]],
) -> list[ReplayResult]:
    """Run the same input with different configs for comparison.

    Useful for A/B testing agent behavior with different settings.

    Args:
        graph: CompiledGraph instance.
        input_data: Common input state.
        configs: Different configs to test.

    Returns:
        List of ReplayResult, one per config.
    """
    results: list[ReplayResult] = []
    for cfg in configs:
        result = await replay_from(graph, input_data, cfg)
        results.append(result)
    return results


# ── Helpers ──────────────────────────────────────────────────────────── #


def _make_replay_config(
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create a config for replay with a new thread ID.

    Args:
        config: Original config.

    Returns:
        Config with new thread_id and replay marker.
    """
    new_config = dict(config or {})
    new_config["thread_id"] = f"replay_{uuid.uuid4().hex[:8]}"
    new_config["replay"] = True
    return new_config


def _make_fork_config(
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create a config for forked execution.

    Args:
        config: Original config.

    Returns:
        Config with new thread_id and fork marker.
    """
    new_config = dict(config or {})
    new_config["thread_id"] = f"fork_{uuid.uuid4().hex[:8]}"
    new_config["forked"] = True
    return new_config


def _apply_updates(
    state: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Apply updates to a deep copy of the state.

    Args:
        state: Original state.
        updates: Key-value updates to apply.

    Returns:
        New state with updates applied.
    """
    new_state = copy.deepcopy(state)
    new_state.update(updates)
    return new_state


def _count_state_changes(
    before: dict[str, Any],
    after: dict[str, Any],
) -> int:
    """Count how many state keys changed.

    Args:
        before: State before execution.
        after: State after execution.

    Returns:
        Number of changed keys.
    """
    changed = 0
    for key in set(list(before.keys()) + list(after.keys())):
        if before.get(key) != after.get(key):
            changed += 1
    return changed


def _extract_checkpoint_id(
    config: dict[str, Any] | None,
) -> str:
    """Extract checkpoint ID from config.

    Args:
        config: Config dict.

    Returns:
        Checkpoint ID or empty string.
    """
    if not config:
        return ""
    return config.get("checkpoint_id", "")
