"""Graph visualization — Mermaid diagram generation.

Produces Mermaid flowchart syntax from a compiled graph's structure.
Can be rendered in GitHub markdown, Jupyter notebooks, or exported
to PNG via the Mermaid CLI.

Example::

    graph = state_graph.compile()
    drawable = get_graph(graph)
    print(drawable.draw_mermaid())

Based on: LangGraph get_graph / draw_mermaid
(langgraph/graph/graph.py, langgraph/utils/mermaid.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DrawableNode:
    """A node in the drawable graph.

    Attributes:
        id: Node identifier.
        name: Display name.
        is_entry: Whether this is the entry point.
        is_end: Whether this is a terminal node.
    """

    id: str
    name: str
    is_entry: bool = False
    is_end: bool = False


@dataclass
class DrawableEdge:
    """An edge in the drawable graph.

    Attributes:
        source: Source node ID.
        target: Target node ID.
        label: Optional edge label (for conditional routing).
        is_conditional: Whether this edge has a router function.
    """

    source: str
    target: str
    label: str = ""
    is_conditional: bool = False


@dataclass
class DrawableGraph:
    """Graph structure ready for visualization.

    Attributes:
        nodes: All drawable nodes.
        edges: All drawable edges.
    """

    nodes: list[DrawableNode] = field(default_factory=list)
    edges: list[DrawableEdge] = field(default_factory=list)

    def draw_mermaid(self, direction: str = "TB") -> str:
        """Generate Mermaid flowchart syntax.

        Args:
            direction: Flow direction (TB, LR, BT, RL).

        Returns:
            Complete Mermaid diagram string.
        """
        lines = [f"graph {direction}"]
        lines.extend(self._render_nodes())
        lines.extend(self._render_edges())
        return "\n".join(lines)

    def _render_nodes(self) -> list[str]:
        """Render node definitions in Mermaid syntax.

        Returns:
            List of Mermaid node definition lines.
        """
        rendered: list[str] = []
        for node in self.nodes:
            rendered.append(_format_node(node))
        return rendered

    def _render_edges(self) -> list[str]:
        """Render edge definitions in Mermaid syntax.

        Returns:
            List of Mermaid edge definition lines.
        """
        rendered: list[str] = []
        for edge in self.edges:
            rendered.append(_format_edge(edge))
        return rendered

    def draw_ascii(self) -> str:
        """Generate a simple ASCII representation.

        Returns:
            ASCII text showing graph structure.
        """
        lines: list[str] = ["Graph:"]
        for node in self.nodes:
            prefix = ">> " if node.is_entry else "   "
            suffix = " [END]" if node.is_end else ""
            lines.append(f"{prefix}{node.name}{suffix}")
        lines.append("")
        lines.append("Edges:")
        for edge in self.edges:
            arrow = "-.->|" if edge.is_conditional else "-->"
            label = f"{edge.label}" if edge.label else ""
            if edge.is_conditional and label:
                lines.append(f"  {edge.source} {arrow}{label}| {edge.target}")
            else:
                lines.append(f"  {edge.source} {arrow} {edge.target}")
        return "\n".join(lines)


def get_graph(compiled_graph: Any) -> DrawableGraph:
    """Extract a drawable graph from a CompiledGraph.

    Inspects the compiled graph's internal structure to produce
    a visualization-ready representation.

    Args:
        compiled_graph: A CompiledGraph instance.

    Returns:
        DrawableGraph with nodes and edges.
    """
    nodes = _extract_nodes(compiled_graph)
    edges = _extract_edges(compiled_graph)
    return DrawableGraph(nodes=nodes, edges=edges)


def _extract_nodes(graph: Any) -> list[DrawableNode]:
    """Extract drawable nodes from a compiled graph.

    Args:
        graph: CompiledGraph instance.

    Returns:
        List of DrawableNode objects.
    """
    nodes: list[DrawableNode] = []
    entry = getattr(graph, "_entry_point", "")

    nodes.append(DrawableNode(
        id="__start__",
        name="START",
        is_entry=True,
    ))

    for name in _get_node_names(graph):
        nodes.append(DrawableNode(
            id=name,
            name=name,
            is_entry=(name == entry),
        ))

    nodes.append(DrawableNode(
        id="__end__",
        name="END",
        is_end=True,
    ))

    return nodes


def _get_node_names(graph: Any) -> list[str]:
    """Get sorted node names from a compiled graph.

    Args:
        graph: CompiledGraph instance.

    Returns:
        Sorted list of node names.
    """
    node_dict = getattr(graph, "_nodes", {})
    return sorted(node_dict.keys())


def _extract_edges(graph: Any) -> list[DrawableEdge]:
    """Extract drawable edges from a compiled graph.

    Args:
        graph: CompiledGraph instance.

    Returns:
        List of DrawableEdge objects.
    """
    edges: list[DrawableEdge] = []
    entry = getattr(graph, "_entry_point", "")

    if entry:
        edges.append(DrawableEdge(
            source="__start__",
            target=entry,
        ))

    edges.extend(_extract_static_edges(graph))
    edges.extend(_extract_conditional_edges(graph))

    return edges


def _extract_static_edges(graph: Any) -> list[DrawableEdge]:
    """Extract static (unconditional) edges.

    Args:
        graph: CompiledGraph instance.

    Returns:
        List of static DrawableEdge objects.
    """
    edge_dict = getattr(graph, "_edges", {})
    edges: list[DrawableEdge] = []

    for source, targets in edge_dict.items():
        for target in targets:
            target_id = "__end__" if target == "__end__" else target
            edges.append(DrawableEdge(
                source=source,
                target=target_id,
            ))

    return edges


def _extract_conditional_edges(graph: Any) -> list[DrawableEdge]:
    """Extract conditional edges with labels.

    Args:
        graph: CompiledGraph instance.

    Returns:
        List of conditional DrawableEdge objects.
    """
    cond_dict = getattr(graph, "_conditional_edges", {})
    edges: list[DrawableEdge] = []

    for source, cond in cond_dict.items():
        mapping = getattr(cond, "mapping", {})
        for label, target in mapping.items():
            target_id = "__end__" if target == "__end__" else target
            edges.append(DrawableEdge(
                source=source,
                target=target_id,
                label=str(label),
                is_conditional=True,
            ))

    return edges


def _format_node(node: DrawableNode) -> str:
    """Format a node as Mermaid syntax.

    Args:
        node: DrawableNode to format.

    Returns:
        Mermaid node definition string.
    """
    if node.is_entry and node.id == "__start__":
        return f"    {node.id}([{node.name}])"
    if node.is_end:
        return f"    {node.id}([{node.name}])"
    return f"    {node.id}[{node.name}]"


def _format_edge(edge: DrawableEdge) -> str:
    """Format an edge as Mermaid syntax.

    Args:
        edge: DrawableEdge to format.

    Returns:
        Mermaid edge definition string.
    """
    if edge.is_conditional and edge.label:
        return f"    {edge.source} -.->|{edge.label}| {edge.target}"
    return f"    {edge.source} --> {edge.target}"
