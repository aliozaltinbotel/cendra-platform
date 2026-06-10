"""CompiledGraph — executable graph with state management.

Produced by ``StateGraph.compile()``. Supports sync ``invoke``,
async ``ainvoke``, and step-by-step ``stream`` execution.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from typing import Any, AsyncIterator, Callable

from brain_engine.graph.constants import END
from brain_engine.graph.state import apply_update, initialize_state

logger = logging.getLogger(__name__)


class CompiledGraph:
    """Immutable compiled graph ready for execution.

    Created by ``StateGraph.compile()`` — not instantiated directly.

    Args:
        nodes: Mapping of node names to their callables.
        edges: Static edges as source → [targets].
        conditional_edges: Conditional routing definitions.
        entry_point: Name of the first node to execute.
        state_schema: TypedDict class for state shape.
        checkpointer: Optional checkpointer for persistence.
        interrupt_before: Nodes to pause before executing.
        interrupt_after: Nodes to pause after executing.
    """

    def __init__(
        self,
        nodes: dict[str, Callable[..., Any]],
        edges: dict[str, list[str]],
        conditional_edges: dict[str, Any],
        entry_point: str,
        state_schema: type,
        checkpointer: Any = None,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
    ) -> None:
        """Initialize CompiledGraph."""
        self._nodes = nodes
        self._edges = edges
        self._conditional_edges = conditional_edges
        self._entry_point = entry_point
        self._state_schema = state_schema
        self._checkpointer = checkpointer
        self._interrupt_before = set(interrupt_before or [])
        self._interrupt_after = set(interrupt_after or [])

    @property
    def entry_point(self) -> str:
        """Return the graph's entry node name."""
        return self._entry_point

    @property
    def node_names(self) -> list[str]:
        """Return sorted list of all node names."""
        return sorted(self._nodes)

    # ── Public execution API ───────────────────────────────────────────

    def invoke(self, input_data: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Synchronous graph execution (convenience wrapper).

        Args:
            input_data: Initial state values.
            config: Optional execution config (thread_id, etc.).

        Returns:
            Final state dict after graph completes.
        """
        return asyncio.get_event_loop().run_until_complete(
            self.ainvoke(input_data, config),
        )

    async def ainvoke(
        self,
        input_data: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Asynchronous graph execution — runs to completion.

        Enforces interrupt_before and interrupt_after. When a node
        is in the interrupt sets, raises GraphInterrupt and saves
        the checkpoint so execution can be resumed.

        Args:
            input_data: Initial state values.
            config: Optional config with ``thread_id`` for persistence.

        Returns:
            Final state dict after graph completes.

        Raises:
            GraphInterrupt: When an interrupt point is reached.
        """
        state = await self._restore_or_init(input_data, config)
        resumed_node = self._extract_resumed_node(config)
        current_nodes = [self._entry_point]

        while current_nodes:
            state, current_nodes = await self._execute_super_step_with_interrupts(
                state, current_nodes, config, resumed_node,
            )
            resumed_node = None

        await self._save_checkpoint(state, config, next_nodes=())
        return state

    async def stream(
        self,
        input_data: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute the graph and yield state after each super-step.

        Supports interrupt_before/after — raises GraphInterrupt
        when an interrupt point is reached.

        Args:
            input_data: Initial state values.
            config: Optional execution config.

        Yields:
            State snapshot after each node execution.
        """
        state = await self._restore_or_init(input_data, config)
        resumed_node = self._extract_resumed_node(config)
        current_nodes = [self._entry_point]

        while current_nodes:
            state, current_nodes = await self._execute_super_step_with_interrupts(
                state, current_nodes, config, resumed_node,
            )
            resumed_node = None
            yield state

    # ── State API ──────────────────────────────────────────────────────

    async def get_state(
        self,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Retrieve the last checkpointed state for a thread.

        Args:
            config: Must contain ``thread_id``.

        Returns:
            Saved state dict or ``None`` if no checkpoint exists.
        """
        if not self._checkpointer:
            return None
        return await self._checkpointer.get(config)

    async def update_state(
        self,
        config: dict[str, Any],
        values: dict[str, Any],
        as_node: str | None = None,
    ) -> None:
        """Manually update the checkpointed state.

        Args:
            config: Must contain ``thread_id``.
            values: Partial state update to apply.
            as_node: Optional node name to record as the update source.
        """
        if not self._checkpointer:
            return

        current = await self._checkpointer.get(config)
        if current is None:
            current = initialize_state(self._state_schema, {})

        updated = apply_update(current, values)
        metadata = {"source": "update", "as_node": as_node}
        await self._checkpointer.put(config, updated, metadata)

    # ── Execution internals ────────────────────────────────────────────

    async def _execute_super_step_with_interrupts(
        self,
        state: dict[str, Any],
        nodes_to_run: list[str],
        config: dict[str, Any] | None,
        resumed_node: str | None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Execute one super-step with interrupt enforcement.

        Checks interrupt_before before each node and interrupt_after
        after each node. Saves checkpoint before raising so execution
        can resume.

        Args:
            state: Current graph state.
            nodes_to_run: Nodes to execute in this step.
            config: Execution config for checkpointing.
            resumed_node: Node being resumed (skip its interrupt_before).

        Returns:
            Tuple of (updated_state, next_nodes_to_run).
        """
        next_nodes: list[str] = []

        for node_name in nodes_to_run:
            self._enforce_interrupt_before(
                node_name, state, config, resumed_node,
            )
            state = await self._execute_node(state, node_name)
            await self._save_checkpoint(state, config, next_nodes=())
            self._enforce_interrupt_after(node_name, state, config)
            successors = self._resolve_next(node_name, state)
            next_nodes.extend(successors)

        return state, next_nodes

    def _enforce_interrupt_before(
        self,
        node_name: str,
        state: dict[str, Any],
        config: dict[str, Any] | None,
        resumed_node: str | None,
    ) -> None:
        """Raise GraphInterrupt if node is in interrupt_before set.

        Args:
            node_name: Node about to execute.
            state: Current state (saved to checkpoint).
            config: Config for checkpointing.
            resumed_node: Skip interrupt if resuming this node.

        Raises:
            GraphInterrupt: Pauses before the node runs.
        """
        if node_name not in self._interrupt_before:
            return
        if resumed_node == node_name:
            return

        from brain_engine.pregel.types import GraphInterrupt

        logger.info("Interrupt BEFORE node: %s", node_name)
        asyncio.get_event_loop().run_until_complete(
            self._save_checkpoint(state, config, next_nodes=(node_name,)),
        ) if config else None
        raise GraphInterrupt(
            value={"node": node_name, "state": state},
            node=node_name,
            interrupt_type="before",
        )

    def _enforce_interrupt_after(
        self,
        node_name: str,
        state: dict[str, Any],
        config: dict[str, Any] | None,
    ) -> None:
        """Raise GraphInterrupt if node is in interrupt_after set.

        Args:
            node_name: Node that just finished.
            state: Current state after node execution.
            config: Config for checkpointing.

        Raises:
            GraphInterrupt: Pauses after the node completes.
        """
        if node_name not in self._interrupt_after:
            return

        from brain_engine.pregel.types import GraphInterrupt

        logger.info("Interrupt AFTER node: %s", node_name)
        raise GraphInterrupt(
            value={"node": node_name, "state": state},
            node=node_name,
            interrupt_type="after",
        )

    def _extract_resumed_node(
        self,
        config: dict[str, Any] | None,
    ) -> str | None:
        """Extract the resumed node name from config.

        Args:
            config: Execution config, may contain ``resumed_node``.

        Returns:
            Node name being resumed, or None.
        """
        if not config:
            return None
        return config.get("resumed_node")

    async def _execute_node(
        self,
        state: dict[str, Any],
        node_name: str,
    ) -> dict[str, Any]:
        """Execute a single node and apply its update to state.

        Args:
            state: Current graph state.
            node_name: Name of the node to execute.

        Returns:
            Updated state after applying the node's partial update.
        """
        func = self._nodes[node_name]
        logger.debug("Executing node: %s", node_name)

        update = await self._call_node_func(func, state)

        if update and isinstance(update, dict):
            state = apply_update(state, update)

        return state

    @staticmethod
    async def _call_node_func(
        func: Callable[..., Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call a node function, handling both sync and async.

        Args:
            func: The node's callable.
            state: Current state passed as the first argument.

        Returns:
            Partial state update dict (or None).
        """
        if inspect.iscoroutinefunction(func):
            return await func(state)
        return func(state)

    def _resolve_next(
        self,
        node_name: str,
        state: dict[str, Any],
    ) -> list[str]:
        """Determine successor nodes after executing a node.

        Checks conditional edges first, then static edges.
        Filters out ``END`` nodes.

        Args:
            node_name: Just-executed node name.
            state: Current state (for conditional routing).

        Returns:
            List of next node names to execute (empty if terminal).
        """
        if node_name in self._conditional_edges:
            return self._resolve_conditional(node_name, state)
        return self._resolve_static(node_name)

    def _resolve_conditional(
        self,
        node_name: str,
        state: dict[str, Any],
    ) -> list[str]:
        """Resolve conditional edges using the router function.

        Args:
            node_name: Source node with conditional edges.
            state: Current state to pass to the router.

        Returns:
            List of target nodes (empty if routed to END).
        """
        cond = self._conditional_edges[node_name]
        route_key = cond.router(state)
        target = cond.mapping.get(route_key, END)

        logger.debug(
            "Conditional: %s -> route=%s -> target=%s",
            node_name, route_key, target,
        )
        if target == END:
            return []
        return [target]

    def _resolve_static(self, node_name: str) -> list[str]:
        """Resolve static edges for a node.

        Args:
            node_name: Source node name.

        Returns:
            List of target node names (excluding END).
        """
        targets = self._edges.get(node_name, [])
        return [t for t in targets if t != END]

    # ── Checkpoint helpers ─────────────────────────────────────────────

    async def _restore_or_init(
        self,
        input_data: dict[str, Any],
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Restore from checkpoint or initialize fresh state.

        Args:
            input_data: Initial state values from caller.
            config: Execution config with optional ``thread_id``.

        Returns:
            State dict to start execution from.
        """
        if self._checkpointer and config:
            saved = await self._checkpointer.get(config)
            if saved is not None:
                return saved

        return initialize_state(self._state_schema, input_data)

    async def _save_checkpoint(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None,
        next_nodes: tuple[str, ...],
    ) -> None:
        """Save a checkpoint if a checkpointer is configured.

        Args:
            state: Current state to persist.
            config: Execution config with ``thread_id``.
            next_nodes: Nodes scheduled for the next super-step.
        """
        if not self._checkpointer or not config:
            return

        checkpoint_id = str(uuid.uuid4())
        metadata = {
            "checkpoint_id": checkpoint_id,
            "next": next_nodes,
            "source": "graph",
        }
        await self._checkpointer.put(config, state, metadata)
