"""Subgraph composition — nest compiled graphs as nodes.

Allows using a compiled graph as a node in a parent graph.
The subgraph receives the parent state, runs to completion,
and returns its output as the node's state update.

Example::

    inner = StateGraph(MyState)
    inner.add_node("process", process_fn)
    inner.add_edge(START, "process")
    inner.add_edge("process", END)
    inner_compiled = inner.compile()

    outer = StateGraph(MyState)
    outer.add_node("sub", subgraph_node(inner_compiled))
    outer.add_edge(START, "sub")
    outer.add_edge("sub", END)

Based on: LangGraph subgraph compilation
(langgraph/graph/state.py subgraph support).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def subgraph_node(
    compiled_graph: Any,
    *,
    input_mapper: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    output_mapper: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Wrap a compiled graph as a node function.

    Creates an async function that invokes the subgraph with the
    parent state and returns the subgraph's output as a state update.

    Args:
        compiled_graph: A ``CompiledGraph`` instance.
        input_mapper: Optional function to transform parent state
            before passing to subgraph.
        output_mapper: Optional function to transform subgraph
            output before returning to parent.

    Returns:
        Async node function compatible with ``StateGraph.add_node()``.
    """

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        """Execute the subgraph with parent state.

        Args:
            state: Parent graph state.

        Returns:
            State update from subgraph output.
        """
        subgraph_input = _apply_input_mapper(state, input_mapper)
        result = await _invoke_subgraph(compiled_graph, subgraph_input)
        return _apply_output_mapper(result, output_mapper)

    _node.__name__ = _get_subgraph_name(compiled_graph)
    _node.__qualname__ = _node.__name__
    return _node


def _apply_input_mapper(
    state: dict[str, Any],
    mapper: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply input transformation if mapper is provided.

    Args:
        state: Parent state.
        mapper: Optional transformation function.

    Returns:
        Transformed or original state.
    """
    if mapper is not None:
        return mapper(state)
    return dict(state)


def _apply_output_mapper(
    result: dict[str, Any],
    mapper: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply output transformation if mapper is provided.

    Args:
        result: Subgraph output.
        mapper: Optional transformation function.

    Returns:
        Transformed or original output.
    """
    if mapper is not None:
        return mapper(result)
    return result


async def _invoke_subgraph(
    graph: Any,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Invoke a compiled graph, handling sync/async.

    Args:
        graph: CompiledGraph instance.
        input_data: Input state for the subgraph.

    Returns:
        Subgraph output state.
    """
    if hasattr(graph, "ainvoke"):
        return await graph.ainvoke(input_data)
    if hasattr(graph, "invoke"):
        result = graph.invoke(input_data)
        if asyncio.iscoroutine(result):
            return await result
        return result
    raise TypeError(
        f"Graph {type(graph).__name__} has no invoke/ainvoke method"
    )


def _get_subgraph_name(graph: Any) -> str:
    """Extract a name for the subgraph node.

    Args:
        graph: CompiledGraph instance.

    Returns:
        Descriptive name string.
    """
    entry = getattr(graph, "_entry_point", "")
    if entry:
        return f"subgraph_{entry}"
    return "subgraph"


class SubgraphSpec:
    """Declarative specification for subgraph embedding.

    Holds configuration for how a subgraph connects to the
    parent graph, including state mapping and checkpointing.

    Args:
        graph: The compiled subgraph.
        input_keys: Parent state keys to pass as subgraph input.
        output_keys: Subgraph output keys to merge into parent.
        inherit_checkpointer: Whether to share parent's checkpointer.
    """

    def __init__(
        self,
        graph: Any,
        *,
        input_keys: list[str] | None = None,
        output_keys: list[str] | None = None,
        inherit_checkpointer: bool = True,
    ) -> None:
        self.graph = graph
        self.input_keys = input_keys
        self.output_keys = output_keys
        self.inherit_checkpointer = inherit_checkpointer

    def build_node(self) -> Callable[[dict[str, Any]], Any]:
        """Build a node function from this spec.

        Returns:
            Async node function with input/output mapping.
        """
        input_mapper = _build_key_mapper(self.input_keys)
        output_mapper = _build_key_mapper(self.output_keys)
        return subgraph_node(
            self.graph,
            input_mapper=input_mapper,
            output_mapper=output_mapper,
        )


def _build_key_mapper(
    keys: list[str] | None,
) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    """Build a mapper that filters state to specific keys.

    Args:
        keys: Keys to keep. None means pass everything.

    Returns:
        Mapper function or None.
    """
    if keys is None:
        return None

    def _mapper(state: dict[str, Any]) -> dict[str, Any]:
        return {k: state[k] for k in keys if k in state}

    return _mapper
