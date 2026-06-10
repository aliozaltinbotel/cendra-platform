"""StateGraph — builder for directed execution graphs.

Nodes are added as named functions, edges connect them (including
conditional routing). Once built, ``compile()`` produces a
``CompiledGraph`` ready for execution.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from brain_engine.graph.constants import END, START

logger = logging.getLogger(__name__)


class StateGraph:
    """Mutable builder for constructing a directed execution graph.

    Args:
        state_schema: TypedDict class defining the state shape.
    """

    def __init__(self, state_schema: type) -> None:
        """Initialize StateGraph."""
        self._state_schema = state_schema
        self._nodes: dict[str, Callable[..., Any]] = {}
        self._edges: dict[str, list[str]] = {}
        self._conditional_edges: dict[str, _ConditionalEdge] = {}
        self._entry_point: str | None = None

    @property
    def state_schema(self) -> type:
        """Return the state schema TypedDict class."""
        return self._state_schema

    @property
    def nodes(self) -> dict[str, Callable[..., Any]]:
        """Return a copy of the registered nodes."""
        return dict(self._nodes)

    def add_node(self, name: str, func: Callable[..., Any]) -> None:
        """Register a node function by name.

        Args:
            name: Unique node identifier.
            func: Callable that receives state and returns partial update.

        Raises:
            ValueError: If node name is reserved or already exists.
        """
        self._validate_node_name(name)
        self._nodes[name] = func
        logger.debug("Added node: %s", name)

    def add_edge(self, source: str, target: str) -> None:
        """Add a static edge from source to target.

        Args:
            source: Source node name (or ``START``).
            target: Target node name (or ``END``).

        Raises:
            ValueError: If source or target are invalid.
        """
        self._validate_edge_endpoint(source, allow_start=True)
        self._validate_edge_endpoint(target, allow_end=True)

        if source == START:
            self._entry_point = target
        self._edges.setdefault(source, []).append(target)
        logger.debug("Added edge: %s -> %s", source, target)

    def add_conditional_edges(
        self,
        source: str,
        router: Callable[..., str],
        mapping: dict[str, str],
    ) -> None:
        """Add conditional routing from a source node.

        After the source node runs, ``router(state)`` is called and its
        return value is looked up in ``mapping`` to determine the next
        node.

        Args:
            source: Source node name.
            router: Function that takes state and returns a mapping key.
            mapping: Dict from router return values to target node names.

        Raises:
            ValueError: If source is invalid or targets don't exist.
        """
        self._validate_node_exists(source)
        for target in mapping.values():
            self._validate_edge_endpoint(target, allow_end=True)

        self._conditional_edges[source] = _ConditionalEdge(
            router=router, mapping=mapping,
        )
        logger.debug(
            "Added conditional edges: %s -> %s",
            source, list(mapping.values()),
        )

    def set_entry_point(self, node_name: str) -> None:
        """Explicitly set the graph's entry node.

        Equivalent to ``add_edge(START, node_name)``.

        Args:
            node_name: Name of the entry node.

        Raises:
            ValueError: If node doesn't exist.
        """
        self._validate_node_exists(node_name)
        self._entry_point = node_name

    def compile(
        self,
        checkpointer: Any = None,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
    ) -> "CompiledGraph":
        """Compile the graph into an executable ``CompiledGraph``.

        Validates graph structure (entry point, reachability) before
        producing the compiled form.

        Args:
            checkpointer: Optional checkpointer for state persistence.
            interrupt_before: Nodes to pause before executing.
            interrupt_after: Nodes to pause after executing.

        Returns:
            A ``CompiledGraph`` ready for invocation.

        Raises:
            ValueError: If the graph is structurally invalid.
        """
        self._validate_graph()

        from brain_engine.graph.compiled import CompiledGraph

        return CompiledGraph(
            nodes=dict(self._nodes),
            edges=dict(self._edges),
            conditional_edges=dict(self._conditional_edges),
            entry_point=self._entry_point or "",
            state_schema=self._state_schema,
            checkpointer=checkpointer,
            interrupt_before=interrupt_before or [],
            interrupt_after=interrupt_after or [],
        )

    def compile_pregel(
        self,
        max_steps: int = 25,
    ) -> "PregelExecutor":
        """Compile the graph into a channel-based Pregel executor.

        Uses BSP (Bulk Synchronous Parallel) execution with typed
        channels, parallel node execution, and atomic state updates.

        Args:
            max_steps: Maximum supersteps before stopping.

        Returns:
            A ``PregelExecutor`` ready for invocation.

        Raises:
            ValueError: If the graph is structurally invalid.
        """
        self._validate_graph()

        from brain_engine.graph.pregel_compiler import compile_to_pregel

        return compile_to_pregel(
            nodes=dict(self._nodes),
            edges=dict(self._edges),
            conditional_edges=dict(self._conditional_edges),
            entry_point=self._entry_point or "",
            state_schema=self._state_schema,
            max_steps=max_steps,
        )

    # ── Validation helpers ─────────────────────────────────────────────

    def _validate_node_name(self, name: str) -> None:
        """Check that a node name is valid and not taken.

        Args:
            name: Proposed node name.

        Raises:
            ValueError: If name is reserved or already registered.
        """
        if name in (START, END):
            raise ValueError(f"'{name}' is a reserved node name")
        if name in self._nodes:
            raise ValueError(f"Node '{name}' already exists")

    def _validate_node_exists(self, name: str) -> None:
        """Check that a node has been registered.

        Args:
            name: Node name to check.

        Raises:
            ValueError: If node is not registered.
        """
        if name not in self._nodes:
            raise ValueError(
                f"Node '{name}' not found. "
                f"Available: {list(self._nodes)}"
            )

    def _validate_edge_endpoint(
        self,
        name: str,
        *,
        allow_start: bool = False,
        allow_end: bool = False,
    ) -> None:
        """Validate an edge source or target name.

        Args:
            name: Endpoint name.
            allow_start: Whether START is valid here.
            allow_end: Whether END is valid here.

        Raises:
            ValueError: If the endpoint is invalid.
        """
        if name == START and allow_start:
            return
        if name == END and allow_end:
            return
        if name in self._nodes:
            return
        raise ValueError(
            f"Invalid endpoint '{name}'. Must be a registered node"
            f"{' or START' if allow_start else ''}"
            f"{' or END' if allow_end else ''}"
        )

    def _validate_graph(self) -> None:
        """Validate the graph is well-formed before compilation.

        Raises:
            ValueError: If entry point is missing or nodes unreachable.
        """
        if not self._entry_point:
            raise ValueError(
                "No entry point set. Use add_edge(START, node) "
                "or set_entry_point(node)"
            )
        if self._entry_point not in self._nodes:
            raise ValueError(
                f"Entry point '{self._entry_point}' is not a registered node"
            )
        self._check_reachability()

    def _check_reachability(self) -> None:
        """Verify all nodes are reachable from the entry point.

        Raises:
            ValueError: If any registered node is unreachable.
        """
        reachable = self._collect_reachable(self._entry_point)
        unreachable = set(self._nodes) - reachable
        if unreachable:
            logger.warning("Unreachable nodes: %s", unreachable)

    def _collect_reachable(self, start: str) -> set[str]:
        """BFS from start to find all reachable nodes.

        Args:
            start: Starting node name.

        Returns:
            Set of reachable node names.
        """
        visited: set[str] = set()
        queue = [start]

        while queue:
            current = queue.pop(0)
            if current in visited or current == END:
                continue
            visited.add(current)
            queue.extend(self._get_successors(current))

        return visited

    def _get_successors(self, node: str) -> list[str]:
        """Get all possible successor nodes for a given node.

        Args:
            node: Source node name.

        Returns:
            List of successor node names.
        """
        successors = list(self._edges.get(node, []))

        if node in self._conditional_edges:
            cond = self._conditional_edges[node]
            successors.extend(cond.mapping.values())

        return successors


class _ConditionalEdge:
    """Internal container for a conditional edge definition.

    Args:
        router: Function that takes state and returns a mapping key.
        mapping: Dict from router return values to target node names.
    """

    __slots__ = ("router", "mapping")

    def __init__(
        self,
        router: Callable[..., str],
        mapping: dict[str, str],
    ) -> None:
        """Initialize _ConditionalEdge."""
        self.router = router
        self.mapping = mapping
