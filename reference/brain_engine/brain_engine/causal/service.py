"""Causal navigation service (Gap #3 part 2).

Given a :class:`CausalGraph` and an anchor event key, walks the graph
forward or backward and returns the chains discovered up to a bounded
depth.  Keeps navigation deterministic: edges are walked in descending
confidence order so the highest-signal chain always appears first.
"""

from __future__ import annotations

from collections import deque
from typing import Sequence

import structlog

from brain_engine.causal.errors import CausalNavigationError
from brain_engine.causal.graph import CausalGraphBuilder
from brain_engine.causal.models import CausalChain, CausalEdge, CausalGraph
from brain_engine.narrative.models import TimelineEvent

__all__ = ["CausalNavigationService"]


_LOGGER = structlog.get_logger(__name__)

_DIRECTIONS: frozenset[str] = frozenset({"ancestors", "descendants"})
_MAX_DEPTH: int = 10


class CausalNavigationService:
    """Stateless orchestrator around a :class:`CausalGraphBuilder`.

    The service owns the graph-construction path (``build_graph``) and
    the read-side navigation primitives (``walk``).  Keeping both in
    one place means API handlers hold exactly one dependency.
    """

    def __init__(
        self,
        builder: CausalGraphBuilder,
        *,
        max_depth: int = 4,
    ) -> None:
        if max_depth < 1 or max_depth > _MAX_DEPTH:
            raise CausalNavigationError(
                f"max_depth must be within [1, {_MAX_DEPTH}]"
            )
        self._builder = builder
        self._max_depth = int(max_depth)

    async def build_graph(
        self,
        events: Sequence[TimelineEvent],
    ) -> CausalGraph:
        return await self._builder.build(events)

    def walk(
        self,
        graph: CausalGraph,
        *,
        anchor_key: str,
        direction: str,
        depth: int | None = None,
    ) -> tuple[CausalChain, ...]:
        """Return every chain reachable from ``anchor_key``.

        Raises:
            CausalNavigationError: if ``direction`` is unsupported, the
                depth is out of range, or the anchor is missing from
                the graph.
        """
        if direction not in _DIRECTIONS:
            raise CausalNavigationError(
                f"direction must be one of {sorted(_DIRECTIONS)}"
            )
        limit = self._resolve_depth(depth)
        if graph.event(anchor_key) is None:
            raise CausalNavigationError(
                f"anchor {anchor_key!r} is not in the graph"
            )
        return _walk(graph, anchor_key, direction, limit)

    def _resolve_depth(self, depth: int | None) -> int:
        if depth is None:
            return self._max_depth
        if depth < 1 or depth > self._max_depth:
            raise CausalNavigationError(
                f"depth must be within [1, {self._max_depth}]"
            )
        return int(depth)


def _walk(
    graph: CausalGraph,
    anchor_key: str,
    direction: str,
    depth: int,
) -> tuple[CausalChain, ...]:
    """BFS over the graph, collecting one chain per discovered leaf."""
    chains: list[CausalChain] = []
    visited: set[str] = {anchor_key}
    frontier: deque[tuple[str, tuple[CausalEdge, ...]]] = deque(
        [(anchor_key, ())]
    )

    while frontier:
        key, trail = frontier.popleft()
        edges = _next_edges(graph, key, direction)
        if not edges or len(trail) >= depth:
            if trail:
                chains.append(
                    CausalChain(
                        anchor_key=anchor_key,
                        direction=direction,
                        edges=trail,
                    )
                )
            continue
        for edge in edges:
            next_key = edge.source_key if direction == "ancestors" else edge.target_key
            if next_key in visited:
                continue
            visited.add(next_key)
            frontier.append((next_key, trail + (edge,)))

    return tuple(chains)


def _next_edges(
    graph: CausalGraph,
    key: str,
    direction: str,
) -> tuple[CausalEdge, ...]:
    raw = graph.incoming(key) if direction == "ancestors" else graph.outgoing(key)
    return tuple(sorted(raw, key=lambda e: e.confidence, reverse=True))
