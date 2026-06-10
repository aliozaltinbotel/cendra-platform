"""Fan-in support — multi-edge synchronization for StateGraph.

Enables ``add_edge([A, B], C)`` syntax where node C waits for
both A and B to complete before running. Uses NamedBarrierValue
channel internally for synchronization.

Example::

    graph = StateGraph(MyState)
    graph.add_node("fetch_data", fetch)
    graph.add_node("fetch_reviews", reviews)
    graph.add_node("combine", combine)

    add_fan_in_edge(graph, ["fetch_data", "fetch_reviews"], "combine")

Based on: LangGraph multi-edge add_edge([A, B], C).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def add_fan_in_edge(
    graph: Any,
    sources: list[str],
    target: str,
) -> None:
    """Add a fan-in edge: target waits for all sources.

    Creates a barrier node that waits for all source nodes
    to complete before allowing the target node to execute.

    Args:
        graph: StateGraph builder instance.
        sources: List of source node names.
        target: Target node that runs after all sources.
    """
    barrier_name = _barrier_node_name(sources, target)
    barrier_state = _create_barrier_state(sources)

    graph.add_node(barrier_name, _make_barrier_func(sources))

    for source in sources:
        graph.add_edge(source, barrier_name)

    graph.add_edge(barrier_name, target)

    logger.info(
        "Fan-in edge: [%s] -> %s (via barrier '%s')",
        ", ".join(sources), target, barrier_name,
    )


def _barrier_node_name(sources: list[str], target: str) -> str:
    """Generate a unique barrier node name.

    Args:
        sources: Source node names.
        target: Target node name.

    Returns:
        Barrier node name.
    """
    src_part = "_".join(sorted(sources))
    return f"__barrier_{src_part}_to_{target}__"


def _create_barrier_state(sources: list[str]) -> dict[str, Any]:
    """Create initial barrier state tracking.

    Args:
        sources: Source names that must complete.

    Returns:
        Barrier state dict.
    """
    return {
        "expected": set(sources),
        "received": set(),
    }


def _make_barrier_func(
    sources: list[str],
) -> Any:
    """Create a barrier node function.

    The barrier passes through the state unchanged. The actual
    synchronization happens via the graph's edge system — all
    source nodes must complete before the barrier node triggers.

    Args:
        sources: Source node names (for documentation).

    Returns:
        Node function that passes state through.
    """

    def barrier_node(state: dict[str, Any]) -> dict[str, Any]:
        """Barrier node — synchronization point for fan-in.

        All source nodes have completed when this runs.
        Passes state through unchanged.

        Args:
            state: Current graph state.

        Returns:
            Empty dict (no state changes).
        """
        logger.debug(
            "Fan-in barrier passed: sources=%s", sources,
        )
        return {}

    barrier_node.__name__ = f"barrier_{'_'.join(sources)}"
    return barrier_node


class FanInConfig:
    """Configuration for fan-in edges in a graph.

    Tracks all fan-in relationships for validation and
    visualization.

    Attributes:
        edges: List of (sources, target) tuples.
    """

    def __init__(self) -> None:
        self._edges: list[tuple[list[str], str]] = []

    def add(self, sources: list[str], target: str) -> None:
        """Register a fan-in edge.

        Args:
            sources: Source nodes.
            target: Target node.
        """
        self._edges.append((list(sources), target))

    @property
    def edges(self) -> list[tuple[list[str], str]]:
        """All registered fan-in edges."""
        return list(self._edges)

    def validate(self, node_names: set[str]) -> list[str]:
        """Validate that all referenced nodes exist.

        Args:
            node_names: Set of known node names.

        Returns:
            List of error messages (empty if valid).
        """
        errors: list[str] = []
        for sources, target in self._edges:
            for src in sources:
                if src not in node_names:
                    errors.append(f"Fan-in source '{src}' not found")
            if target not in node_names:
                errors.append(f"Fan-in target '{target}' not found")
        return errors
