"""Tests for the shared :class:`MemoryFanOut` service (PR #F).

The fan-out wires every DecisionCase write path (bootstrap, live
conversation, regenerate, V1 onboarding, …) into the same three
high-level memory tiers (Episodic / Semantic / Knowledge Graph),
so the timeline / semantic recall / KG entity panel reflect every
source uniformly.

This module pins the central contract:

* ``record_case`` fans one persisted :class:`DecisionCase` out to
  every wired tier — no tier left silent.
* Each tier's write carries the ``source`` tag so operators can
  filter by origin (bootstrap vs live vs regenerate).
* The fan-out is *best-effort*: a broken tier is logged but
  never aborts the caller.
* :class:`NullMemoryFanOut` is a silent no-op for environments
  without high-level memory backends.
* ``record_id`` on the semantic tier equals ``case.case_id`` so a
  re-extract overwrites the stale row instead of duplicating.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from brain_engine.memory.fanout import (
    MemoryFanOut,
    NullMemoryFanOut,
)
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

_BASE_TS = datetime(2026, 5, 13, 12, tzinfo=UTC)


def _case(
    *,
    case_id: str = "case-1",
    scenario: Scenario = Scenario.ACCESS_CODE_RELEASE,
    decision_type: DecisionType = DecisionType.INFORM,
    message_text: str = "Door code please",
    property_id: str = "323133",
    guest_id: str | None = None,
    reservation_id: str | None = None,
) -> DecisionCase:
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.PRE_ARRIVAL,
        scenario=scenario,
        property_id=property_id,
        owner_id="",
        message_text=message_text,
        decision=DecisionAction(action_type=decision_type),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.LIVE,
        created_at=_BASE_TS,
        guest_id=guest_id,
        reservation_id=reservation_id,
    )


# ── Recording fakes ───────────────────────────────────────────────


class _RecEpisodic:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add_episode(
        self,
        event: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {"event": event, "content": content, "metadata": metadata or {}}
        )
        return None


class _RecSemantic:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def store(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "text": text,
                "metadata": metadata or {},
                "record_id": record_id or "",
            },
        )
        return record_id or "auto"


class _RecKG:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add_knowledge(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return None


# ── Contract: every tier receives one write ───────────────────────


@pytest.mark.asyncio
async def test_record_case_writes_all_three_tiers() -> None:
    ep, sem, kg = _RecEpisodic(), _RecSemantic(), _RecKG()
    fanout = MemoryFanOut(
        episodic=cast(Any, ep),
        semantic=cast(Any, sem),
        knowledge_graph=cast(Any, kg),
    )
    case = _case()
    await fanout.record_case(case, source="live")
    assert len(ep.calls) == 1
    assert len(sem.calls) == 1
    assert len(kg.calls) == 1


@pytest.mark.asyncio
async def test_semantic_record_carries_conversation_scope_key() -> None:
    """The semantic record must carry a ``conversation_id`` so recall
    can isolate one guest's facts from another's on the same property
    (the cross-guest WhatsApp-number leak, tester 2026-06-10).  The
    key mirrors the episodic tier: ``reservation_id or guest_id``."""
    ep, sem, kg = _RecEpisodic(), _RecSemantic(), _RecKG()
    fanout = MemoryFanOut(
        episodic=cast(Any, ep),
        semantic=cast(Any, sem),
        knowledge_graph=cast(Any, kg),
    )
    await fanout.record_case(
        _case(guest_id="guest-thread-7"), source="live",
    )
    assert sem.calls[0]["metadata"]["conversation_id"] == "guest-thread-7"


@pytest.mark.asyncio
async def test_semantic_conversation_key_prefers_reservation_id() -> None:
    """When both are set, ``reservation_id`` wins — same precedence as
    the episodic conversation key."""
    ep, sem, kg = _RecEpisodic(), _RecSemantic(), _RecKG()
    fanout = MemoryFanOut(
        episodic=cast(Any, ep),
        semantic=cast(Any, sem),
        knowledge_graph=cast(Any, kg),
    )
    await fanout.record_case(
        _case(guest_id="g-1", reservation_id="res-9"), source="live",
    )
    assert sem.calls[0]["metadata"]["conversation_id"] == "res-9"


@pytest.mark.asyncio
async def test_source_tag_propagates_to_every_tier() -> None:
    """Every backend payload carries the same ``source`` string."""
    ep, sem, kg = _RecEpisodic(), _RecSemantic(), _RecKG()
    fanout = MemoryFanOut(
        episodic=cast(Any, ep),
        semantic=cast(Any, sem),
        knowledge_graph=cast(Any, kg),
    )
    await fanout.record_case(_case(), source="regenerate")
    assert ep.calls[0]["metadata"]["source"] == "regenerate"
    assert sem.calls[0]["metadata"]["source"] == "regenerate"
    assert kg.calls[0]["source"] == "regenerate"
    assert "regenerate" in kg.calls[0]["tags"]


@pytest.mark.asyncio
async def test_semantic_record_id_equals_case_id_for_dedup() -> None:
    """Re-extract overwrites the existing vector row, never duplicates."""
    sem = _RecSemantic()
    fanout = MemoryFanOut(semantic=cast(Any, sem))
    await fanout.record_case(_case(case_id="dup-1"), source="bootstrap")
    await fanout.record_case(_case(case_id="dup-1"), source="bootstrap")
    assert {c["record_id"] for c in sem.calls} == {"dup-1"}


@pytest.mark.asyncio
async def test_kg_uses_fact_knowledge_type_and_property_entity() -> None:
    """KG node is anchored on the property entity as a FACT."""
    kg = _RecKG()
    fanout = MemoryFanOut(knowledge_graph=cast(Any, kg))
    await fanout.record_case(_case(property_id="prop-x"), source="live")
    call = kg.calls[0]
    assert call["entity_type"] == "property"
    assert call["entity_id"] == "prop-x"
    assert call["knowledge_type"] is KnowledgeType.FACT


# ── Temporal anchor: event_time vs extraction wall-clock ──────────


@pytest.mark.asyncio
async def test_kg_event_time_uses_decision_at_when_set() -> None:
    """Historical replay anchors the KG node on the message time.

    ``decision_at`` (the source guest message's ``sent_at``) is the
    event time the temporal KG must use — not ``created_at``, which is
    the extraction wall-clock.  Regression guard for the KG-audit Step
    3 bug: before ``DecisionCase`` carried a ``decision_at`` field the
    fan-out's ``getattr(case, "decision_at", None)`` always fell back to
    ``created_at``, collapsing every archive fact onto "today" and
    breaking bi-temporal time-travel.
    """
    event_ts = datetime(2026, 2, 10, 9, 0, tzinfo=UTC)
    extraction_ts = datetime(2026, 5, 24, 14, 0, tzinfo=UTC)
    kg = _RecKG()
    fanout = MemoryFanOut(knowledge_graph=cast(Any, kg))
    case = dataclasses.replace(
        _case(),
        decision_at=event_ts,
        created_at=extraction_ts,
    )
    await fanout.record_case(case, source="bootstrap")
    assert kg.calls[0]["event_time"] == event_ts.isoformat()


@pytest.mark.asyncio
async def test_kg_event_time_falls_back_to_created_at() -> None:
    """Live cases without a source timestamp use ``created_at``."""
    created_ts = datetime(2026, 5, 24, 14, 0, tzinfo=UTC)
    kg = _RecKG()
    fanout = MemoryFanOut(knowledge_graph=cast(Any, kg))
    case = dataclasses.replace(
        _case(),
        decision_at=None,
        created_at=created_ts,
    )
    await fanout.record_case(case, source="live")
    assert kg.calls[0]["event_time"] == created_ts.isoformat()


# ── Best-effort behaviour ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_broken_episodic_never_aborts_other_tiers() -> None:
    class _BrokenEp:
        async def add_episode(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("episodic down")

    sem, kg = _RecSemantic(), _RecKG()
    fanout = MemoryFanOut(
        episodic=cast(Any, _BrokenEp()),
        semantic=cast(Any, sem),
        knowledge_graph=cast(Any, kg),
    )
    await fanout.record_case(_case(), source="bootstrap")
    assert len(sem.calls) == 1
    assert len(kg.calls) == 1


@pytest.mark.asyncio
async def test_broken_semantic_never_aborts_other_tiers() -> None:
    class _BrokenSem:
        async def store(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("qdrant down")

    ep, kg = _RecEpisodic(), _RecKG()
    fanout = MemoryFanOut(
        episodic=cast(Any, ep),
        semantic=cast(Any, _BrokenSem()),
        knowledge_graph=cast(Any, kg),
    )
    await fanout.record_case(_case(), source="bootstrap")
    assert len(ep.calls) == 1
    assert len(kg.calls) == 1


@pytest.mark.asyncio
async def test_broken_kg_never_aborts_other_tiers() -> None:
    class _BrokenKG:
        async def add_knowledge(self, **kw: Any) -> Any:
            raise RuntimeError("kg down")

    ep, sem = _RecEpisodic(), _RecSemantic()
    fanout = MemoryFanOut(
        episodic=cast(Any, ep),
        semantic=cast(Any, sem),
        knowledge_graph=cast(Any, _BrokenKG()),
    )
    await fanout.record_case(_case(), source="bootstrap")
    assert len(ep.calls) == 1
    assert len(sem.calls) == 1


@pytest.mark.asyncio
async def test_null_fanout_is_total_noop() -> None:
    fanout = NullMemoryFanOut()
    await fanout.record_case(_case(), source="live")  # must not raise


@pytest.mark.asyncio
async def test_empty_message_skips_semantic_only() -> None:
    """Empty text must not corrupt the vector index."""
    ep, sem, kg = _RecEpisodic(), _RecSemantic(), _RecKG()
    fanout = MemoryFanOut(
        episodic=cast(Any, ep),
        semantic=cast(Any, sem),
        knowledge_graph=cast(Any, kg),
    )
    await fanout.record_case(_case(message_text=""), source="live")
    assert len(ep.calls) == 1  # episodic still records the event row
    assert len(sem.calls) == 0  # semantic skipped — no text
    assert len(kg.calls) == 1  # KG still records the node


# ── Optional tier omission ────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_episodic_wired() -> None:
    ep = _RecEpisodic()
    fanout = MemoryFanOut(episodic=cast(Any, ep))
    await fanout.record_case(_case(), source="live")
    assert len(ep.calls) == 1


@pytest.mark.asyncio
async def test_no_tiers_wired_is_silent_noop() -> None:
    fanout = MemoryFanOut()
    await fanout.record_case(_case(), source="live")  # must not raise
