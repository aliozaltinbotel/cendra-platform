"""Tests for transaction-time as-of reconstruction on the knowledge graph.

Two layers:

* pure :func:`reconstruct_as_of` over hand-built :class:`KnowledgeNode`s
  (full control of timestamps) — visibility gate, value reconstruction
  across updates, invalidation, naive-datetime handling;
* one integration test of :meth:`TemporalKnowledgeGraph.get_entity_knowledge`
  with ``as_of`` over a fakeredis backend — proves the query calls the
  reconstruction and filters by it, and that ``as_of=None`` is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.brain.memory.kg_as_of import reconstruct_as_of
from core.brain.memory.knowledge_graph import (
    KnowledgeNode,
    KnowledgeType,
    TemporalKnowledgeGraph,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)  # born / recorded
_C1 = datetime(2026, 2, 1, tzinfo=UTC)  # first content change
_C2 = datetime(2026, 3, 1, tzinfo=UTC)  # second content change


def _node(**overrides: object) -> KnowledgeNode:
    base: dict[str, object] = {
        "node_id": "n1",
        "content": "CUR",
        "entity_id": "g1",
        "knowledge_type": KnowledgeType.FACT,
        "confidence": 0.9,
        "event_time": _T0.isoformat(),
        "record_time": _T0.isoformat(),
        "valid_from": _T0.isoformat(),
    }
    base.update(overrides)
    return KnowledgeNode(**base)  # type: ignore[arg-type]


# ── visibility gate (transaction time) ──────────────────────────────


def test_not_yet_recorded_returns_none() -> None:
    node = _node(record_time=_C1.isoformat())
    assert reconstruct_as_of(node, _T0) is None


def test_recorded_no_history_returns_current() -> None:
    node = _node()
    out = reconstruct_as_of(node, _C2)
    assert out is not None
    assert out.content == "CUR"
    assert out.confidence == 0.9


def test_missing_record_time_is_not_hidden() -> None:
    node = _node(record_time="", valid_from="")
    out = reconstruct_as_of(node, _T0)
    assert out is not None
    assert out.content == "CUR"


# ── value reconstruction across updates ─────────────────────────────


def _two_update_node() -> KnowledgeNode:
    # Born V0; changed to V1 at C1; changed to V2 (current) at C2.
    return _node(
        content="V2",
        confidence=0.9,
        previous_values=[
            {"content": "V0", "confidence": 0.5, "changed_at": _C1.isoformat()},
            {"content": "V1", "confidence": 0.7, "changed_at": _C2.isoformat()},
        ],
    )


def test_value_in_first_segment() -> None:
    out = reconstruct_as_of(_two_update_node(), datetime(2026, 1, 15, tzinfo=UTC))
    assert out is not None
    assert out.content == "V0"
    assert out.confidence == 0.5


def test_value_in_middle_segment() -> None:
    out = reconstruct_as_of(_two_update_node(), datetime(2026, 2, 15, tzinfo=UTC))
    assert out is not None
    assert out.content == "V1"
    assert out.confidence == 0.7


def test_value_in_current_segment() -> None:
    out = reconstruct_as_of(_two_update_node(), datetime(2026, 4, 1, tzinfo=UTC))
    assert out is not None
    assert out.content == "V2"
    assert out.confidence == 0.9


def test_at_exactly_change_takes_new_value() -> None:
    # ``at == C1``: the change took effect at C1, so the value live at C1
    # is V1 (the segment [C1, C2)), not V0.
    out = reconstruct_as_of(_two_update_node(), _C1)
    assert out is not None
    assert out.content == "V1"


def test_reconstruction_does_not_mutate_original() -> None:
    node = _two_update_node()
    reconstruct_as_of(node, _C1)
    assert node.content == "V2"
    assert node.confidence == 0.9


# ── invalidation ────────────────────────────────────────────────────


def _invalidated_node() -> KnowledgeNode:
    # Mirrors invalidate_knowledge: valid_until set, pre-invalidation value
    # archived with changed_at == valid_until, current confidence zeroed.
    return _node(
        content="REAL",
        confidence=0.0,
        valid_until=_C2.isoformat(),
        previous_values=[
            {"content": "REAL", "confidence": 0.8, "changed_at": _C2.isoformat()},
        ],
    )


def test_value_before_invalidation_reconstructed() -> None:
    out = reconstruct_as_of(_invalidated_node(), datetime(2026, 2, 15, tzinfo=UTC))
    assert out is not None
    assert out.content == "REAL"
    assert out.confidence == 0.8


def test_invalidated_at_or_after_returns_none() -> None:
    assert reconstruct_as_of(_invalidated_node(), _C2) is None
    assert reconstruct_as_of(_invalidated_node(), datetime(2026, 4, 1, tzinfo=UTC)) is None


def test_naive_datetime_treated_as_utc() -> None:
    node = _two_update_node()
    out = reconstruct_as_of(node, datetime(2026, 1, 15))  # naive
    assert out is not None
    assert out.content == "V0"


# ── integration: get_entity_knowledge(as_of=...) over fakeredis ─────


def test_get_entity_knowledge_as_of_wiring() -> None:
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    graph = TemporalKnowledgeGraph(workspace_id="ws1")
    graph._redis = fakeredis.FakeRedis(decode_responses=True)  # type: ignore[attr-defined]

    graph.add_knowledge(
        content="guest prefers late checkout",
        knowledge_type=KnowledgeType.FACT,
        entity_id="guest-1",
    )

    # as_of=None → current view (node visible).
    current = graph.get_entity_knowledge("guest-1")
    assert len(current) == 1
    assert current[0].content == "guest prefers late checkout"

    # as_of far in the future → still visible (recorded before then).
    future = graph.get_entity_knowledge(
        "guest-1",
        as_of=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert len(future) == 1

    # as_of before it was recorded → not yet known → filtered out.
    past = graph.get_entity_knowledge(
        "guest-1",
        as_of=datetime(2020, 1, 1, tzinfo=UTC),
    )
    assert past == []
