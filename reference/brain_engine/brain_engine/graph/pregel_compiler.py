"""Pregel Compiler — compiles StateGraph into channel-based Pregel executor.

Converts a StateGraph's nodes and edges into PregelNodes with channel
wiring, creates typed channels from the state schema, and produces
a PregelExecutor ready for BSP execution.

This is the bridge between the graph builder API and the Pregel engine.
"""

from __future__ import annotations

import logging
import operator
from typing import Any, Callable, get_type_hints

from brain_engine.channels.base import BaseChannel
from brain_engine.channels.binop import BinaryOperatorAggregate
from brain_engine.channels.last_value import LastValue
from brain_engine.graph.constants import END
from brain_engine.pregel.executor import PregelExecutor, PregelNode
from brain_engine.pregel.write import ChannelWriteEntry

logger = logging.getLogger(__name__)


def compile_to_pregel(
    nodes: dict[str, Callable[..., Any]],
    edges: dict[str, list[str]],
    conditional_edges: dict[str, Any],
    entry_point: str,
    state_schema: type,
    max_steps: int = 25,
) -> PregelExecutor:
    """Compile a StateGraph definition into a PregelExecutor.

    Creates channels from state_schema, wires nodes to channels
    based on edges, and returns a ready-to-run executor.

    Args:
        nodes: Node name -> callable mapping.
        edges: Static edges.
        conditional_edges: Conditional routing.
        entry_point: First node to execute.
        state_schema: TypedDict defining state shape.
        max_steps: Maximum supersteps.

    Returns:
        Configured PregelExecutor.
    """
    channels = _create_channels(state_schema)

    # Add __done_ signal channels for each node
    for name in nodes:
        done_ch = LastValue(bool)
        done_ch.key = f"__done_{name}"
        channels[f"__done_{name}"] = done_ch

    pregel_nodes = _create_pregel_nodes(
        nodes, edges, conditional_edges, entry_point, channels,
    )

    # Add entry trigger: __input__ channel triggers entry node
    input_ch = LastValue(Any)
    input_ch.key = "__input__"
    channels["__input__"] = input_ch
    if entry_point in pregel_nodes:
        pregel_nodes[entry_point].triggers.append("__input__")

    return PregelExecutor(pregel_nodes, channels, max_steps=max_steps)


# ── Channel creation ──────────────────────────────────────────────── #


def _create_channels(
    state_schema: type,
) -> dict[str, BaseChannel[Any, Any, Any]]:
    """Create channels from a TypedDict state schema.

    List-typed fields get BinaryOperatorAggregate (concat).
    All others get LastValue.

    Args:
        state_schema: TypedDict class.

    Returns:
        Dict of channel_name -> channel.
    """
    channels: dict[str, BaseChannel[Any, Any, Any]] = {}
    hints = _get_hints(state_schema)

    for name, typ in hints.items():
        ch = _channel_for_type(typ)
        ch.key = name
        channels[name] = ch

    return channels


def _channel_for_type(typ: Any) -> BaseChannel[Any, Any, Any]:
    """Create the appropriate channel for a type hint.

    Args:
        typ: Type annotation.

    Returns:
        Channel instance.
    """
    if _is_list_type(typ):
        return BinaryOperatorAggregate(list, operator.add, default=list)
    return LastValue(typ, default=None)


def _is_list_type(typ: Any) -> bool:
    """Check if a type hint is a list type.

    Args:
        typ: Type annotation.

    Returns:
        True if it's list, list[X], or similar.
    """
    origin = getattr(typ, "__origin__", None)
    if origin is list:
        return True
    if typ is list:
        return True
    return False


def _get_hints(schema: type) -> dict[str, Any]:
    """Extract type hints from a TypedDict or dataclass.

    Args:
        schema: Schema class.

    Returns:
        Dict of field_name -> type.
    """
    try:
        return get_type_hints(schema)
    except Exception:
        annotations = getattr(schema, "__annotations__", {})
        return dict(annotations)


# ── Node wiring ───────────────────────────────────────────────────── #


def _create_pregel_nodes(
    nodes: dict[str, Callable[..., Any]],
    edges: dict[str, list[str]],
    conditional_edges: dict[str, Any],
    entry_point: str,
    channels: dict[str, BaseChannel[Any, Any, Any]],
) -> dict[str, PregelNode]:
    """Create PregelNodes with channel triggers and writers.

    Args:
        nodes: Original node functions.
        edges: Static edges.
        conditional_edges: Conditional routing.
        entry_point: Entry node name.
        channels: Created channels.

    Returns:
        Dict of PregelNode objects.
    """
    channel_names = list(channels.keys())
    predecessors = _build_predecessor_map(edges, conditional_edges)
    pregel_nodes: dict[str, PregelNode] = {}

    for name, func in nodes.items():
        triggers = _compute_triggers(name, predecessors, channel_names)
        wrapped = _wrap_with_routing(func, name, edges, conditional_edges)

        # Writers: state channels + done signal
        writers = [ChannelWriteEntry(channel=ch) for ch in channel_names]
        writers.append(ChannelWriteEntry(
            channel=f"__done_{name}", value=True,
        ))

        pregel_nodes[name] = PregelNode(
            name=name,
            func=wrapped,
            channels=channel_names,
            triggers=triggers,
            writers=writers,
        )

    return pregel_nodes


def _build_predecessor_map(
    edges: dict[str, list[str]],
    conditional_edges: dict[str, Any],
) -> dict[str, list[str]]:
    """Build node -> predecessor nodes mapping.

    Args:
        edges: Static edges.
        conditional_edges: Conditional edges.

    Returns:
        Dict of node_name -> [predecessor_names].
    """
    preds: dict[str, list[str]] = {}
    for source, targets in edges.items():
        for target in targets:
            if target != END:
                preds.setdefault(target, []).append(source)

    for source, cond in conditional_edges.items():
        mapping = getattr(cond, "mapping", {})
        for target in mapping.values():
            if target != END:
                preds.setdefault(target, []).append(source)

    return preds


def _compute_triggers(
    node_name: str,
    predecessors: dict[str, list[str]],
    channel_names: list[str],
) -> list[str]:
    """Compute which channels trigger a node.

    Each node gets a dedicated trigger channel named '__done_{pred}'
    that fires when its predecessor completes. This prevents
    false triggers from unrelated channel writes.

    Args:
        node_name: Node to compute triggers for.
        predecessors: Predecessor mapping.
        channel_names: All available channels.

    Returns:
        List of trigger channel names.
    """
    preds = predecessors.get(node_name, [])
    if not preds:
        return []
    return [f"__done_{pred}" for pred in preds]


def _wrap_with_routing(
    func: Callable[..., Any],
    node_name: str,
    edges: dict[str, list[str]],
    conditional_edges: dict[str, Any],
) -> Callable[..., Any]:
    """Wrap node func to handle conditional routing.

    If the node has conditional edges, wraps the function to
    evaluate the router and include routing info in output.

    Args:
        func: Original node function.
        node_name: Node name.
        edges: Static edges.
        conditional_edges: Conditional routing.

    Returns:
        Wrapped function.
    """
    if node_name not in conditional_edges:
        return func

    cond = conditional_edges[node_name]
    router = getattr(cond, "router", None)
    mapping = getattr(cond, "mapping", {})

    if router is None:
        return func

    def wrapped(state: Any) -> Any:
        result = func(state)
        if isinstance(result, dict):
            route_key = router(state if isinstance(state, dict) else result)
            result["__next__"] = mapping.get(route_key, END)
        return result

    return wrapped
