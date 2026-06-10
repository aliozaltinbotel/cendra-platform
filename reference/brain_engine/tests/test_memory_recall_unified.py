"""Unit tests for :mod:`brain_engine.conversation.memory_recall`.

Covers the three guarantees the property-scoped recall must hold:

* **Rendering** — knowledge-graph facts are dated from ``event_time``,
  beliefs are phrased tentatively, low-confidence nodes are dropped,
  and the per-tier order / dedup / cap hold.
* **PII** — a sensitive value recalled while the reservation is
  pre-booking is redacted by the shared status-aware filter.
* **Resilience** — ``recall_property_scoped`` fails open to ``[]`` on a
  tier error or a timeout, and never reads an unscoped tier.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from brain_engine.conversation.memory_recall import (
    _resolve_timeout,
    _semantic_filter,
    assemble_facts,
    format_belief_node,
    format_fact_node,
    format_semantic_record,
    recall_property_scoped,
)
from brain_engine.memory.knowledge_graph import KnowledgeType


@dataclass
class _Node:
    """Minimal :class:`KnowledgeNode` stand-in for the renderer."""

    content: str = ""
    confidence: float = 1.0
    event_time: str = ""
    source: str = ""


@dataclass
class _Record:
    """Minimal semantic ``MemoryRecord`` stand-in."""

    text: str = ""
    score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# -- renderers ----------------------------------------------------------


def test_fact_node_is_dated_from_event_time() -> None:
    line = format_fact_node(
        _Node(content="guest WhatsApp +456172218", event_time="2026-06-07T10:00:00"),
    )
    assert line == "From an earlier interaction (2026-06-07): guest WhatsApp +456172218"


def test_fact_node_without_date_falls_back() -> None:
    line = format_fact_node(_Node(content="prefers late checkout", event_time=""))
    assert line == "Known from an earlier interaction: prefers late checkout"


def test_low_confidence_fact_is_dropped() -> None:
    assert format_fact_node(_Node(content="maybe", confidence=0.2)) is None


def test_empty_fact_is_dropped() -> None:
    assert format_fact_node(_Node(content="   ")) is None


def test_belief_is_tentative_and_gated() -> None:
    assert format_belief_node(_Node(content="tends to ask early", confidence=0.7)) == (
        "Likely (from past behaviour): tends to ask early"
    )
    # Below the stricter belief floor (0.6) → dropped.
    assert format_belief_node(_Node(content="weak", confidence=0.55)) is None


def test_semantic_record_renders_text() -> None:
    assert format_semantic_record(_Record(text="cleaning fee is 40 EUR")) == (
        "cleaning fee is 40 EUR"
    )
    assert format_semantic_record(_Record(text="")) is None


# -- provenance: where a recalled value came from ----------------------


def test_fact_node_live_source_says_from_your_messages() -> None:
    """A live-conversation fact is labelled as coming from the guest's
    own messages so the agent can attribute it."""
    line = format_fact_node(
        _Node(
            content="WhatsApp +905559876543",
            event_time="2026-06-10T10:00:00",
            source="live",
        ),
    )
    assert line == (
        "From your messages with us (2026-06-10): WhatsApp +905559876543"
    )


def test_fact_node_bootstrap_source_says_from_booking_records() -> None:
    """A harvested (bootstrap) fact is labelled as a booking record —
    this is how the PMS-sourced +39 is distinguished from a chat
    number (tester 2026-06-10)."""
    line = format_fact_node(
        _Node(
            content="WhatsApp +39 371 5211257",
            event_time="2026-05-12T09:00:00",
            source="bootstrap",
        ),
    )
    assert line == (
        "From your booking records (2026-05-12): WhatsApp +39 371 5211257"
    )


def test_fact_node_unknown_source_keeps_legacy_phrasing() -> None:
    """No / unrecognised source ⇒ the neutral legacy wording, so
    nothing regresses for callers that do not set a source."""
    assert format_fact_node(
        _Node(content="x", event_time="2026-06-07T00:00:00"),
    ) == "From an earlier interaction (2026-06-07): x"


def test_semantic_record_carries_provenance_from_metadata() -> None:
    """The semantic tier labels origin from its metadata source."""
    assert format_semantic_record(
        _Record(text="WhatsApp +90", metadata={"source": "live"}),
    ) == "From your messages with us: WhatsApp +90"
    assert format_semantic_record(
        _Record(text="WhatsApp +39", metadata={"source": "bootstrap"}),
    ) == "From your booking records: WhatsApp +39"


# -- assemble: order, dedup, cap, PII -----------------------------------


def test_assemble_orders_facts_then_beliefs_then_semantic() -> None:
    out = assemble_facts(
        fact_nodes=[_Node(content="F", event_time="2026-06-07T00:00:00")],
        belief_nodes=[_Node(content="B", confidence=0.9)],
        semantic_records=[_Record(text="S")],
        status="confirmed",
    )
    assert out == [
        "From an earlier interaction (2026-06-07): F",
        "Likely (from past behaviour): B",
        "S",
    ]


def test_assemble_dedupes_normalised_lines() -> None:
    out = assemble_facts(
        fact_nodes=[],
        belief_nodes=[],
        semantic_records=[_Record(text="WhatsApp +1"), _Record(text="whatsapp  +1")],
        status="confirmed",
    )
    assert out == ["WhatsApp +1"]


def test_assemble_caps_total() -> None:
    records = [_Record(text=f"fact-{i}") for i in range(20)]
    out = assemble_facts(
        fact_nodes=[], belief_nodes=[], semantic_records=records,
        status="confirmed", max_facts=3,
    )
    assert len(out) == 3


def test_questions_are_dropped_so_the_answer_survives() -> None:
    """The fan-out stores the guest's own questions too; a relevance
    search ranks those echoes above the one answer.  Interrogative
    lines must be filtered so the declarative fact surfaces."""
    out = assemble_facts(
        fact_nodes=[],
        belief_nodes=[],
        semantic_records=[
            _Record(text="what is the whatsapp number?"),
            _Record(text="what is WhatsApp number?"),
            _Record(text="My whatsapp number is +4560171018"),
        ],
        status="confirmed",
    )
    assert out == ["My whatsapp number is +4560171018"]


def test_question_fact_node_is_dropped() -> None:
    """A KG fact whose content is a stored guest question is dropped."""
    out = assemble_facts(
        fact_nodes=[_Node(content="what is the wifi password?", event_time="2026-06-07T00:00:00")],
        belief_nodes=[],
        semantic_records=[],
        status="confirmed",
    )
    assert out == []


def test_pre_booking_redacts_sensitive_line() -> None:
    out = assemble_facts(
        fact_nodes=[],
        belief_nodes=[],
        semantic_records=[_Record(text="WiFi password: LaFrenchCasa2023*")],
        status="inquiry",
    )
    assert out
    assert "LaFrenchCasa2023" not in out[0]
    assert "REDACTED" in out[0]


def test_post_booking_keeps_sensitive_line() -> None:
    out = assemble_facts(
        fact_nodes=[],
        belief_nodes=[],
        semantic_records=[_Record(text="WiFi password: LaFrenchCasa2023*")],
        status="confirmed",
    )
    assert out == ["WiFi password: LaFrenchCasa2023*"]


# -- recall_property_scoped: scoping, fail-open, timeout ----------------


class _FakeKG:
    def __init__(self) -> None:
        self.facts_calls: list[str] = []

    async def get_facts(self, entity_id: str) -> list[_Node]:
        self.facts_calls.append(entity_id)
        return [_Node(content="guest WhatsApp +456172218", event_time="2026-06-07T09:00:00")]

    async def get_beliefs(self, entity_id: str) -> list[_Node]:
        return []


class _FakeSemantic:
    def __init__(self) -> None:
        self.filters: list[Any] = []

    async def search(
        self, *, query: str, top_k: int, score_threshold: float,
        metadata_filter: Any,
    ) -> list[_Record]:
        self.filters.append(metadata_filter)
        return [_Record(text="cleaning fee is 40 EUR")]


class _FakeMemory:
    def __init__(self) -> None:
        self.knowledge_graph = _FakeKG()
        self.semantic = _FakeSemantic()


@pytest.mark.asyncio
async def test_recall_returns_scoped_facts() -> None:
    mem = _FakeMemory()
    out = await recall_property_scoped(
        memory_system=mem, property_id="598829",
        query="what is my whatsapp", status="confirmed",
    )
    assert any("+456172218" in line for line in out)
    assert any("40 EUR" in line for line in out)
    # KG was queried by property id; semantic filter scoped to it.
    assert mem.knowledge_graph.facts_calls == ["598829"]
    assert mem.semantic.filters == [{"property_id": "598829"}]


def test_semantic_filter_property_only_when_no_conversation() -> None:
    """No conversation id ⇒ legacy property-only scope (back-compat)."""
    assert _semantic_filter("598829", "") == {"property_id": "598829"}


def test_semantic_filter_adds_conversation_scope() -> None:
    """A conversation id narrows the filter to this one guest."""
    assert _semantic_filter("598829", "thread-42") == {
        "property_id": "598829",
        "conversation_id": "thread-42",
    }


@pytest.mark.asyncio
async def test_recall_scopes_semantic_by_conversation() -> None:
    """The conversation id reaches the semantic filter so another
    guest's records on the same property never surface — the
    cross-guest WhatsApp-number leak (tester 2026-06-10)."""
    mem = _FakeMemory()
    out = await recall_property_scoped(
        memory_system=mem, property_id="598829",
        query="what whatsapp number did I give you", status="confirmed",
        conversation_id="guest-thread-7",
    )
    assert out  # recall still works
    assert mem.semantic.filters == [
        {"property_id": "598829", "conversation_id": "guest-thread-7"},
    ]


@pytest.mark.asyncio
async def test_recall_without_property_is_empty() -> None:
    assert await recall_property_scoped(
        memory_system=_FakeMemory(), property_id="",
        query="q", status="confirmed",
    ) == []


@pytest.mark.asyncio
async def test_recall_none_memory_is_empty() -> None:
    assert await recall_property_scoped(
        memory_system=None, property_id="598829", query="q", status="confirmed",
    ) == []


@pytest.mark.asyncio
async def test_recall_fails_open_on_tier_error() -> None:
    class _BoomKG:
        async def get_facts(self, entity_id: str) -> list[_Node]:
            raise RuntimeError("kg down")

        async def get_beliefs(self, entity_id: str) -> list[_Node]:
            raise RuntimeError("kg down")

    class _Mem:
        knowledge_graph = _BoomKG()
        semantic = None

    # A failing KG and an absent semantic tier degrade to empty, not raise.
    assert await recall_property_scoped(
        memory_system=_Mem(), property_id="598829", query="q", status="confirmed",
    ) == []


class _SinglePassKG:
    """KG exposing the single-pass ``get_entity_knowledge`` (preferred
    over separate get_facts/get_beliefs)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_entity_knowledge(self, entity_id: str) -> list[Any]:
        self.calls.append(entity_id)
        fact = _Node(content="guest WhatsApp +456172218", event_time="2026-06-07T09:00:00")
        belief = _Node(content="tends to arrive late", confidence=0.8)
        fact.knowledge_type = KnowledgeType.FACT  # type: ignore[attr-defined]
        belief.knowledge_type = KnowledgeType.BELIEF  # type: ignore[attr-defined]
        return [fact, belief]


@pytest.mark.asyncio
async def test_recall_uses_single_pass_and_splits_by_type() -> None:
    class _Mem:
        knowledge_graph = _SinglePassKG()
        semantic = None

    mem = _Mem()
    out = await recall_property_scoped(
        memory_system=mem, property_id="598829", query="q", status="confirmed",
    )
    # One KG read (not two), split into the dated fact + tentative belief.
    assert mem.knowledge_graph.calls == ["598829"]
    assert any("+456172218" in line for line in out)
    assert any(line.startswith("Likely") for line in out)


def test_resolve_timeout_prefers_explicit_then_env(monkeypatch: Any) -> None:
    assert _resolve_timeout(2.5) == 2.5  # explicit wins
    monkeypatch.setenv("BRAIN_RECALL_TIMEOUT_S", "9")
    assert _resolve_timeout(None) == 9.0  # env override
    monkeypatch.setenv("BRAIN_RECALL_TIMEOUT_S", "oops")
    assert _resolve_timeout(None) == 6.0  # bad value → default
    monkeypatch.delenv("BRAIN_RECALL_TIMEOUT_S", raising=False)
    assert _resolve_timeout(None) == 6.0  # unset → default


@pytest.mark.asyncio
async def test_recall_times_out_to_empty() -> None:
    class _SlowKG:
        async def get_facts(self, entity_id: str) -> list[_Node]:
            await asyncio.sleep(10)
            return []

        async def get_beliefs(self, entity_id: str) -> list[_Node]:
            await asyncio.sleep(10)
            return []

    class _Mem:
        knowledge_graph = _SlowKG()
        semantic = None

    out = await recall_property_scoped(
        memory_system=_Mem(), property_id="598829", query="q",
        status="confirmed", timeout_s=0.05,
    )
    assert out == []
