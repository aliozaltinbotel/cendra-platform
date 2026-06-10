"""End-to-end contract: a fact WRITTEN by the fan-out is RECALLED.

This closes the read/write asymmetry that left the Sandbox guest agent
unable to recall what a guest had said earlier.  The write half
(:meth:`MemoryFanOut.record_case`) and the read half
(:func:`recall_property_scoped`) are both exercised with their real
code; only the storage backends are in-memory doubles that implement
the exact contract both halves use (``add_knowledge``/``get_facts`` for
the knowledge graph, ``store``/``search`` for semantic memory).

The proof: write "my WhatsApp is +456172218" against property ``598829``
via the fan-out, then recall it on a later turn scoped to the same
property — and confirm it does NOT surface for a different property.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from brain_engine.conversation.memory_recall import recall_property_scoped
from brain_engine.memory.fanout import MemoryFanOut
from brain_engine.memory.knowledge_graph import KnowledgeType
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)

_TS = datetime(2026, 6, 7, 9, tzinfo=UTC)


def _case(*, message_text: str, property_id: str) -> DecisionCase:
    return DecisionCase(
        case_id=f"case-{property_id}",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        property_id=property_id,
        owner_id="",
        message_text=message_text,
        decision=DecisionAction(action_type=DecisionType.INFORM),
        outcome=CaseOutcome(
            successful=True, resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.LIVE,
        created_at=_TS,
    )


# -- in-memory backends implementing BOTH sides of the contract --------


@dataclass
class _Node:
    content: str
    confidence: float
    event_time: str
    knowledge_type: str
    entity_id: str


class _InMemoryKG:
    """Honours ``add_knowledge`` (fan-out) + ``get_facts``/``get_beliefs``
    (recall), keyed by ``entity_id`` like the real graph."""

    def __init__(self) -> None:
        self._nodes: list[_Node] = []

    async def add_knowledge(
        self, *, content: str, knowledge_type: str, entity_id: str,
        confidence: float = 1.0, event_time: str = "", **_: Any,
    ) -> None:
        self._nodes.append(
            _Node(content, confidence, event_time, knowledge_type, entity_id),
        )

    async def get_facts(self, entity_id: str) -> list[_Node]:
        return [
            n for n in self._nodes
            if n.entity_id == entity_id and n.knowledge_type == KnowledgeType.FACT
        ]

    async def get_beliefs(self, entity_id: str) -> list[_Node]:
        return [
            n for n in self._nodes
            if n.entity_id == entity_id and n.knowledge_type == KnowledgeType.BELIEF
        ]


@dataclass
class _Stored:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class _InMemorySemantic:
    """Honours ``store`` (fan-out) + ``search`` (recall) with a
    substring match and the same ``property_id`` metadata filter the
    recall passes."""

    def __init__(self) -> None:
        self._rows: list[_Stored] = []

    async def store(
        self, text: str, metadata: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> str:
        self._rows.append(_Stored(text, metadata or {}))
        return record_id or "auto"

    async def search(
        self, *, query: str, top_k: int, score_threshold: float,
        metadata_filter: dict[str, Any] | None,
    ) -> list[_Stored]:
        out: list[_Stored] = []
        for row in self._rows:
            if metadata_filter and any(
                row.metadata.get(k) != v for k, v in metadata_filter.items()
            ):
                continue
            out.append(row)
        return out[:top_k]


class _Memory:
    def __init__(self) -> None:
        self.knowledge_graph = _InMemoryKG()
        self.semantic = _InMemorySemantic()


@pytest.mark.asyncio
async def test_fact_written_by_fanout_is_recalled_scoped() -> None:
    mem = _Memory()
    fanout = MemoryFanOut(
        episodic=None,
        semantic=cast(Any, mem.semantic),
        knowledge_graph=cast(Any, mem.knowledge_graph),
    )

    # Turn 1: the guest shares their WhatsApp number → fan-out persists.
    await fanout.record_case(
        _case(message_text="my WhatsApp is +456172218", property_id="598829"),
    )

    # Turn 2 (same property): recall must surface the number, dated.
    recalled = await recall_property_scoped(
        memory_system=mem, property_id="598829",
        query="what is my whatsapp number", status="confirmed",
    )
    blob = "\n".join(recalled)
    assert "+456172218" in blob
    assert "2026-06-07" in blob  # dated from the KG node's event_time

    # A different property must NOT see it (scoping guarantee).
    other = await recall_property_scoped(
        memory_system=mem, property_id="111111",
        query="what is my whatsapp number", status="confirmed",
    )
    assert all("+456172218" not in line for line in other)
