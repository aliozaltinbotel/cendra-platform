"""Gate-wiring introspection — per-workflow / per-node governed status read.

A console-consumable read that answers, for one workflow, *which of its
action nodes execute through the Cendra gate chain* and whether the
tenant's posture makes that wiring active ("governed").  It powers the
guest-journey-builder PRD's §4.3 label rule ("Guest Journey Automation"
reserved for gate-wired flows at posture ≥ observe) and the §6
label-integrity guardrail (zero governed indicators on non-gate-wired
flows) — see ``docs/product/prd/guest-journey-builder-and-automation-hub.md``
and CEN-41 / CEN-39 / CEN-24.

**Authoritative dispatch enumeration.**  A node is *gate-wired* iff its
node type dispatches through one of the registered runtime touchpoints:

- **T1** — ``core/workflow/node_runtime.py``'s ``DifyToolNodeRuntime.invoke``
  wraps every Tool-node dispatch with ``evaluate_dispatch_with_shadow`` /
  ``evaluate_tool_dispatch``.  That runtime serves exactly
  ``NodeType.TOOL`` (``"tool"``) — *all* tool providers (builtin, API,
  workflow-as-tool, and plugin tools such as the PMS-adapter / channel
  tools) share that single node type, so the gate wiring is provider-
  agnostic.
- **T3** — ``core/workflow/nodes/agent_v2/agent_node.py``'s
  ``DifyAgentNode`` gates the agent loop via ``gate_agent_run`` in
  ``cendra_brain_layer.py``.  That node is ``NodeType.AGENT`` (``"agent"``).

Every other node type (``llm``, ``code``, ``http-request``,
``knowledge-retrieval``, ``if-else`` …) has no touchpoint and never
dispatches through the chain.  The enumeration is verified against the
touchpoint sources by ``api/tests/unit_tests/brain/test_gate_wiring_service.py``
(``test_enumeration_is_verified_against_touchpoints``).

This is a **read-only** introspection surface: it observes posture, it
never mutates it, and nothing here can enable enforce mode or autonomy
(CEN-41 is observe-posture only).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.runtime_gateway import GovernancePosture, governance_posture
from extensions.ext_database import db
from models.workflow import Workflow

try:  # graphon owns the node-type vocabulary; bind to it, do not stringly-type.
    from graphon.enums import BuiltinNodeTypes

    _TOOL_NODE_TYPE: str = str(BuiltinNodeTypes.TOOL)
    _AGENT_NODE_TYPE: str = str(BuiltinNodeTypes.AGENT)
except Exception:  # pragma: no cover - fallback keeps the enumeration self-describing
    _TOOL_NODE_TYPE = "tool"
    _AGENT_NODE_TYPE = "agent"


# Authoritative node-type → touchpoint enumeration (see module docstring).
GATE_WIRED_NODE_TYPES: Mapping[str, str] = {
    _TOOL_NODE_TYPE: "T1",
    _AGENT_NODE_TYPE: "T3",
}


def classify_node_type(node_type: str) -> tuple[bool, str | None]:
    """Return ``(gate_wired, touchpoint)`` for a single node type string."""
    touchpoint = GATE_WIRED_NODE_TYPES.get(node_type)
    return touchpoint is not None, touchpoint


def inspect_graph(graph_nodes: Iterable[Mapping[str, Any]], posture: GovernancePosture) -> dict[str, Any]:
    """Classify each node in a workflow graph against the gate-wiring enumeration.

    ``graph_nodes`` are the raw ``graph["nodes"]`` entries (each shaped
    ``{"id": ..., "data": {"type": ..., "title": ...}}``).  Classification is
    purely node-type based — no canvas-shape heuristics (CEN-41 acceptance #1).
    A node is ``governed`` only when it is gate-wired *and* the tenant's
    posture is active.
    """
    nodes: list[dict[str, Any]] = []
    by_touchpoint: dict[str, int] = {}
    gate_wired_count = 0
    governed_count = 0

    for node in graph_nodes:
        data = node.get("data") or {}
        node_type = str(data.get("type", ""))
        gate_wired, touchpoint = classify_node_type(node_type)
        governed = gate_wired and posture.active
        if gate_wired:
            gate_wired_count += 1
            by_touchpoint[touchpoint] = by_touchpoint.get(touchpoint, 0) + 1  # type: ignore[index]
        if governed:
            governed_count += 1
        nodes.append(
            {
                "node_id": node.get("id"),
                "node_type": node_type,
                "title": data.get("title", ""),
                "gate_wired": gate_wired,
                "touchpoint": touchpoint,
                "governed": governed,
            }
        )

    return {
        "posture": {
            "mode": posture.mode,
            "tenant_enabled": posture.tenant_enabled,
            "active": posture.active,
        },
        # A flow is gate-wired if it has ≥1 gate-wired action node; it is
        # governed (eligible for the "Guest Journey Automation" label) only
        # when posture is also active (PRD §4.3 / §6).
        "gate_wired": gate_wired_count > 0,
        "governed": gate_wired_count > 0 and posture.active,
        "summary": {
            "total_nodes": len(nodes),
            "gate_wired_nodes": gate_wired_count,
            "governed_nodes": governed_count,
            "by_touchpoint": by_touchpoint,
        },
        "nodes": nodes,
    }


def node_type_enumeration() -> list[dict[str, str]]:
    """Return the authoritative gate-wired node-type → touchpoint list.

    Surfaced as its own endpoint so the console can render a legend and so
    the enumeration is documented through the API (CEN-41 acceptance #2).
    """
    return [
        {"node_type": node_type, "touchpoint": touchpoint} for node_type, touchpoint in GATE_WIRED_NODE_TYPES.items()
    ]


def _session_maker() -> sessionmaker:
    return sessionmaker(bind=db.engine, expire_on_commit=False)


class BrainGateWiringService:
    """Tenant-scoped read facade for gate-wiring introspection."""

    def __init__(self, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id
        self._sessions = _session_maker()

    def node_type_enumeration(self) -> list[dict[str, str]]:
        return node_type_enumeration()

    def inspect_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        """Return the per-node / per-workflow gate-wiring report, or ``None``.

        ``None`` signals the workflow does not exist in this tenant (the
        controller maps it to 404).  Tenant scoping is enforced in the query.
        """
        with self._sessions() as session:
            workflow = session.execute(
                select(Workflow).where(
                    Workflow.id == workflow_id,
                    Workflow.tenant_id == self._tenant_id,
                )
            ).scalar_one_or_none()
            if workflow is None:
                return None
            graph = workflow.graph_dict or {}
            graph_nodes = graph.get("nodes") or []
            report = inspect_graph(graph_nodes, governance_posture(self._tenant_id))
            report["workflow_id"] = workflow.id
            report["app_id"] = workflow.app_id
            report["tenant_id"] = self._tenant_id
            return report
