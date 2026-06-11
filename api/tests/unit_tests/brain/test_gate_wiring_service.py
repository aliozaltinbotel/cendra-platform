"""Gate-wiring introspection: enumeration, posture and graph classification (CEN-41).

The enumeration of which node types dispatch through the gate chain is
*verified against the touchpoint sources* here, so the read API and the
T1/T3 hooks cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.brain.runtime_gateway import (
    GATES_MODE_ENV,
    GATES_TENANTS_ENV,
    governance_posture,
)
from services.brain_gate_wiring_service import (
    GATE_WIRED_NODE_TYPES,
    classify_node_type,
    inspect_graph,
    node_type_enumeration,
)

TENANT = "11111111-1111-1111-1111-111111111111"
_API_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    monkeypatch.delenv(GATES_MODE_ENV, raising=False)
    monkeypatch.delenv(GATES_TENANTS_ENV, raising=False)


def _graph(*node_types: str) -> list[dict]:
    return [{"id": f"n{i}", "data": {"type": t, "title": t.title()}} for i, t in enumerate(node_types)]


# ── enumeration ─────────────────────────────────────────────────── #


def test_enumeration_is_tool_t1_and_agent_t3():
    assert GATE_WIRED_NODE_TYPES == {"tool": "T1", "agent": "T3"}


def test_node_type_enumeration_payload():
    payload = node_type_enumeration()
    assert {entry["node_type"] for entry in payload} == {"tool", "agent"}
    assert {entry["touchpoint"] for entry in payload} == {"T1", "T3"}


@pytest.mark.parametrize(
    ("node_type", "gate_wired", "touchpoint"),
    [
        ("tool", True, "T1"),
        ("agent", True, "T3"),
        ("llm", False, None),
        ("code", False, None),
        ("http-request", False, None),
        ("knowledge-retrieval", False, None),
        ("if-else", False, None),
        ("", False, None),
    ],
)
def test_classify_node_type(node_type, gate_wired, touchpoint):
    assert classify_node_type(node_type) == (gate_wired, touchpoint)


def test_enumeration_is_verified_against_touchpoints():
    """Lock the enumeration to the actual T1/T3 hook sources on this branch.

    T1 wraps the Tool-node runtime (``DifyToolNodeRuntime``) and T3 the
    agent_v2 node (``DifyAgentNode``); the only gate-wired node types are
    therefore ``tool`` and ``agent``.
    """
    node_runtime = (_API_ROOT / "core/workflow/node_runtime.py").read_text(encoding="utf-8")
    assert "CENDRA-HOOK(T1)" in node_runtime
    assert "class DifyToolNodeRuntime" in node_runtime
    # T1 dispatches the gate chain from inside the tool runtime.
    assert ("evaluate_tool_dispatch" in node_runtime) or ("evaluate_dispatch_with_shadow" in node_runtime)

    agent_node = (_API_ROOT / "core/workflow/nodes/agent_v2/agent_node.py").read_text(encoding="utf-8")
    assert "CENDRA-HOOK(T3)" in agent_node
    assert "gate_agent_run" in agent_node

    # The enumeration must cover exactly those two node types — nothing more.
    assert set(GATE_WIRED_NODE_TYPES) == {"tool", "agent"}


# ── posture ─────────────────────────────────────────────────────── #


def test_posture_off_is_inactive(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "off")
    posture = governance_posture(TENANT)
    assert posture.mode == "off"
    assert posture.active is False


def test_posture_observe_is_active(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    posture = governance_posture(TENANT)
    assert posture.mode == "observe"
    assert posture.tenant_enabled is True
    assert posture.active is True


def test_posture_allowlist_excludes_tenant(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    monkeypatch.setenv(GATES_TENANTS_ENV, "some-other-tenant")
    posture = governance_posture(TENANT)
    assert posture.tenant_enabled is False
    assert posture.active is False


def test_posture_empty_tenant_id_inactive(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "enforce")
    posture = governance_posture("")
    assert posture.active is False


# ── graph classification ────────────────────────────────────────── #


def test_inspect_graph_governed_when_posture_active(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    report = inspect_graph(_graph("start", "tool", "llm", "agent", "end"), governance_posture(TENANT))

    assert report["gate_wired"] is True
    assert report["governed"] is True
    assert report["summary"] == {
        "total_nodes": 5,
        "gate_wired_nodes": 2,
        "governed_nodes": 2,
        "by_touchpoint": {"T1": 1, "T3": 1},
    }
    markers = {n["node_type"]: n for n in report["nodes"]}
    assert markers["tool"]["touchpoint"] == "T1"
    assert markers["tool"]["governed"] is True
    assert markers["agent"]["touchpoint"] == "T3"
    assert markers["llm"]["gate_wired"] is False
    assert markers["llm"]["governed"] is False
    assert markers["llm"]["touchpoint"] is None


def test_inspect_graph_gate_wired_but_not_governed_when_off(monkeypatch):
    """§6 label-integrity: gate-wired flow at off posture is NOT governed."""
    monkeypatch.setenv(GATES_MODE_ENV, "off")
    report = inspect_graph(_graph("start", "tool", "end"), governance_posture(TENANT))

    assert report["gate_wired"] is True
    assert report["governed"] is False
    assert report["summary"]["governed_nodes"] == 0
    assert all(n["governed"] is False for n in report["nodes"])


def test_inspect_graph_non_gate_wired_flow_never_governed(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    report = inspect_graph(_graph("start", "llm", "code", "end"), governance_posture(TENANT))

    assert report["gate_wired"] is False
    assert report["governed"] is False
    assert report["summary"]["gate_wired_nodes"] == 0


def test_inspect_graph_tolerates_malformed_nodes(monkeypatch):
    monkeypatch.setenv(GATES_MODE_ENV, "observe")
    report = inspect_graph([{"id": "n0"}, {"id": "n1", "data": None}], governance_posture(TENANT))
    assert report["gate_wired"] is False
    assert report["summary"]["total_nodes"] == 2
