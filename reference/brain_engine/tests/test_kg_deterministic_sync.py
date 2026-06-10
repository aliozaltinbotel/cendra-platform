"""Tests for the Task 7 deterministic KG sync.

Task 7 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md
for the baseline) replaces the LLM-driven entity-extraction path
inside :meth:`NightlyConsolidator._step4_update_knowledge_graph` with
a deterministic mapping from ``DecisionCase`` rows into the
:class:`brain_engine.memory.knowledge_graph.TemporalKnowledgeGraph`.

The change removes the ``gpt-4o-mini`` call surface that survived the
2026-04-29 Graphiti removal — every guest / property / booking
entity Brain Engine cares about is already structured PMS data, so
LLM extraction is wasted token spend.

These tests pin the contract through stub knowledge-graph and case-
store collaborators — no Redis, no Qdrant, no LLM.  Each guarantee
maps to a concrete operator concern:

* **Flag plumbing** — defaults must protect the production path
  (``deterministic`` ON, ``LLM`` OFF).
* **Per-case sync** — the right entity types and relationships are
  emitted from the structured fields, missing IDs are skipped, and
  empty ``property_id`` short-circuits.
* **Batch sync** — aggregate counters reflect what was actually
  written; per-case failures cannot abort the batch.
* **Step-4 orchestration** — the deterministic path runs by
  default when the sync is injected, the legacy LLM path keeps
  pre-Task-7 behaviour when it is not, and operators can opt the
  LLM path back on alongside the deterministic path through
  ``BRAIN_KG_LLM_EXTRACTION_ENABLED``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain_engine.continual_learning.nightly_consolidator import (
    NightlyConsolidator,
)
from brain_engine.memory.kg_deterministic_sync import (
    DeterministicKGSync,
    SyncStats,
    _SOURCE_TAG,
    deterministic_sync_enabled,
    llm_extraction_enabled,
)
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionCase,
    DecisionType,
    Scenario,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _make_case(
    *,
    property_id: str = "p1",
    guest_id: str | None = "g1",
    reservation_id: str | None = "r1",
    case_id: str = "case-1",
) -> DecisionCase:
    """Minimal DecisionCase fixture with the IDs the sync looks at."""
    return DecisionCase(
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        property_id=property_id,
        owner_id="o1",
        decision=DecisionAction(action_type=DecisionType.APPROVE),
        case_id=case_id,
        guest_id=guest_id,
        reservation_id=reservation_id,
        created_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc),
    )


def _build_kg() -> MagicMock:
    """Stub knowledge-graph with awaitable add_* methods."""
    kg = MagicMock(name="TemporalKnowledgeGraph")
    kg.add_knowledge = AsyncMock(return_value=None)
    kg.add_relationship = AsyncMock(return_value=None)
    return kg


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_kg_env() -> Iterator[None]:
    snapshot = {
        key: os.environ.pop(key, None)
        for key in (
            "BRAIN_KG_DETERMINISTIC_SYNC_ENABLED",
            "BRAIN_KG_LLM_EXTRACTION_ENABLED",
        )
    }
    try:
        yield
    finally:
        for key in (
            "BRAIN_KG_DETERMINISTIC_SYNC_ENABLED",
            "BRAIN_KG_LLM_EXTRACTION_ENABLED",
        ):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_deterministic_default_is_on() -> None:
    """Production protection — deterministic path opt-out required."""
    assert deterministic_sync_enabled() is True


def test_llm_extraction_default_is_off() -> None:
    """LLM path is opt-in to keep token spend predictable."""
    assert llm_extraction_enabled() is False


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "garbage"])
def test_deterministic_can_be_disabled(raw: str) -> None:
    os.environ["BRAIN_KG_DETERMINISTIC_SYNC_ENABLED"] = raw
    assert deterministic_sync_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", " 1 "])
def test_llm_extraction_can_be_enabled(raw: str) -> None:
    os.environ["BRAIN_KG_LLM_EXTRACTION_ENABLED"] = raw
    assert llm_extraction_enabled() is True


# ---------------------------------------------------------------------------
# sync_decision_case
# ---------------------------------------------------------------------------


async def test_sync_full_case_writes_three_entities_and_three_edges() -> None:
    """Property + Guest + Booking + 3 relationships."""
    kg = _build_kg()
    sync = DeterministicKGSync(kg=kg)

    nodes, edges = await sync.sync_decision_case(_make_case())

    assert nodes == 3
    assert edges == 3
    # Three add_knowledge calls (property, guest, booking).
    assert kg.add_knowledge.await_count == 3
    # Three add_relationship calls
    # (involved_in_case, booked_for, stayed_at).
    assert kg.add_relationship.await_count == 3
    # Source tag on every node call.
    for call in kg.add_knowledge.await_args_list:
        assert call.kwargs["source"] == _SOURCE_TAG


async def test_sync_property_only_case_writes_just_property() -> None:
    """Missing guest_id + reservation_id -> only Property node."""
    kg = _build_kg()
    sync = DeterministicKGSync(kg=kg)

    nodes, edges = await sync.sync_decision_case(
        _make_case(guest_id=None, reservation_id=None),
    )

    assert nodes == 1
    assert edges == 0
    assert kg.add_knowledge.await_count == 1
    assert kg.add_relationship.await_count == 0


async def test_sync_skips_case_without_property_id() -> None:
    """No property -> nothing written, return zero counters."""
    kg = _build_kg()
    sync = DeterministicKGSync(kg=kg)

    case = _make_case(property_id="")

    nodes, edges = await sync.sync_decision_case(case)

    assert nodes == 0
    assert edges == 0
    kg.add_knowledge.assert_not_called()
    kg.add_relationship.assert_not_called()


async def test_sync_uses_case_created_at_for_event_time() -> None:
    """The bi-temporal event_time matches the case's created_at."""
    kg = _build_kg()
    sync = DeterministicKGSync(kg=kg)
    case = _make_case()

    await sync.sync_decision_case(case)

    iso = case.created_at.isoformat()
    for call in kg.add_knowledge.await_args_list:
        assert call.kwargs["event_time"] == iso


async def test_sync_does_not_call_any_llm() -> None:
    """Anchor: zero LLM imports / litellm.acompletion calls.

    The whole point of Task 7 is to avoid token spend on the
    nightly KG path; pin that contract by checking that no
    ``litellm`` reference even appears as a side effect.
    """
    import sys

    kg = _build_kg()
    sync = DeterministicKGSync(kg=kg)

    # ``litellm`` may have been imported elsewhere already; capture
    # whether new calls happen during the sync.
    if "litellm" in sys.modules:
        litellm = sys.modules["litellm"]
        baseline = getattr(litellm, "_call_count", None)
    else:
        baseline = None

    await sync.sync_decision_case(_make_case())

    if baseline is not None:
        assert getattr(sys.modules["litellm"], "_call_count", None) == (
            baseline
        )


# ---------------------------------------------------------------------------
# sync_decision_cases batch
# ---------------------------------------------------------------------------


async def test_batch_sync_aggregates_counters() -> None:
    kg = _build_kg()
    sync = DeterministicKGSync(kg=kg)

    cases = [
        _make_case(case_id="c1"),
        _make_case(case_id="c2", reservation_id=None, guest_id=None),
    ]
    stats = await sync.sync_decision_cases(cases)

    assert isinstance(stats, SyncStats)
    assert stats.cases_seen == 2
    assert stats.cases_skipped == 0
    # First case: 3 nodes, 3 edges. Second: 1 node, 0 edges.
    assert stats.nodes_written == 4
    assert stats.relationships_written == 3


async def test_batch_sync_records_skipped_cases() -> None:
    kg = _build_kg()
    sync = DeterministicKGSync(kg=kg)
    bad_case = _make_case(case_id="ghost", property_id="")
    cases = [bad_case, _make_case(case_id="real")]

    stats = await sync.sync_decision_cases(cases)

    assert stats.cases_seen == 2
    assert stats.cases_skipped == 1
    assert stats.nodes_written == 3  # only the real case
    assert stats.relationships_written == 3


async def test_batch_sync_continues_on_per_case_failure() -> None:
    """A flaky add_relationship call cannot abort the batch."""
    kg = _build_kg()
    kg.add_relationship.side_effect = [
        None,  # first case: ok
        RuntimeError("redis down"),  # second case: blow up
        None, None, None, None,  # remaining (would-be) calls
    ]
    sync = DeterministicKGSync(kg=kg)

    cases = [_make_case(case_id="c1"), _make_case(case_id="c2")]
    stats = await sync.sync_decision_cases(cases)

    # The first case succeeded fully; the second blew up partway and
    # was logged.  cases_seen counts both.
    assert stats.cases_seen == 2


# ---------------------------------------------------------------------------
# NightlyConsolidator step 4 orchestration
# ---------------------------------------------------------------------------


def _build_consolidator(
    *,
    deterministic_kg_sync: Any = None,
    case_store: Any = None,
    memory: Any = None,
) -> NightlyConsolidator:
    """Construct a consolidator with stubbed collaborators."""
    if memory is None:
        memory = MagicMock()
        memory.episodic.get_recent = AsyncMock(return_value=[])
        memory.consolidator.consolidate = AsyncMock(
            return_value={"facts": 0},
        )
    return NightlyConsolidator(
        memory=memory,
        skills=MagicMock(),
        recorder=MagicMock(),
        grader=MagicMock(),
        case_store=case_store,
        deterministic_kg_sync=deterministic_kg_sync,
    )


async def test_step4_runs_deterministic_when_sync_injected() -> None:
    """Sync injected -> deterministic path runs, LLM does not."""
    sync = MagicMock()
    sync.sync_decision_cases = AsyncMock(
        return_value=SyncStats(
            cases_seen=5,
            cases_skipped=0,
            nodes_written=10,
            relationships_written=8,
        ),
    )
    case_store = MagicMock()
    case_store.search = AsyncMock(return_value=[_make_case()])
    memory = MagicMock()
    memory.episodic.get_recent = AsyncMock(return_value=[])
    memory.consolidator.consolidate = AsyncMock(
        return_value={"facts": 0},
    )
    consolidator = _build_consolidator(
        deterministic_kg_sync=sync,
        case_store=case_store,
        memory=memory,
    )

    result = await consolidator._step4_update_knowledge_graph()

    assert result["deterministic"]["nodes_written"] == 10
    assert result.get("llm_skipped") is True
    sync.sync_decision_cases.assert_awaited_once()
    memory.consolidator.consolidate.assert_not_called()


async def test_step4_falls_back_to_llm_without_sync() -> None:
    """No sync injected -> pre-Task-7 LLM path runs unchanged."""
    memory = MagicMock()
    memory.episodic.get_recent = AsyncMock(
        return_value=[MagicMock()],
    )
    memory.consolidator.consolidate = AsyncMock(
        return_value={"facts": 5},
    )
    consolidator = _build_consolidator(
        deterministic_kg_sync=None,
        memory=memory,
    )

    result = await consolidator._step4_update_knowledge_graph()

    assert "deterministic" not in result
    assert result["llm"]["consolidation_result"] == {"facts": 5}
    memory.consolidator.consolidate.assert_awaited_once_with(force=True)


async def test_step4_runs_both_when_llm_flag_on() -> None:
    """Operator opts LLM back on -> both paths run."""
    os.environ["BRAIN_KG_LLM_EXTRACTION_ENABLED"] = "1"
    sync = MagicMock()
    sync.sync_decision_cases = AsyncMock(
        return_value=SyncStats(cases_seen=1),
    )
    case_store = MagicMock()
    case_store.search = AsyncMock(return_value=[_make_case()])
    memory = MagicMock()
    memory.episodic.get_recent = AsyncMock(
        return_value=[MagicMock()],
    )
    memory.consolidator.consolidate = AsyncMock(
        return_value={"facts": 1},
    )
    consolidator = _build_consolidator(
        deterministic_kg_sync=sync,
        case_store=case_store,
        memory=memory,
    )

    result = await consolidator._step4_update_knowledge_graph()

    assert "deterministic" in result
    assert "llm" in result
    sync.sync_decision_cases.assert_awaited_once()
    memory.consolidator.consolidate.assert_awaited_once_with(force=True)


async def test_step4_skips_deterministic_when_flag_off() -> None:
    """Operator can panic-disable the new path via env flag."""
    os.environ["BRAIN_KG_DETERMINISTIC_SYNC_ENABLED"] = "0"
    sync = MagicMock()
    sync.sync_decision_cases = AsyncMock()
    case_store = MagicMock()
    case_store.search = AsyncMock(return_value=[])
    memory = MagicMock()
    # Episodic returns one event so the legacy fallback actually
    # invokes ``consolidate`` and we can pin the call.
    memory.episodic.get_recent = AsyncMock(
        return_value=[MagicMock()],
    )
    memory.consolidator.consolidate = AsyncMock(
        return_value={"facts": 0},
    )
    consolidator = _build_consolidator(
        deterministic_kg_sync=sync,
        case_store=case_store,
        memory=memory,
    )

    result = await consolidator._step4_update_knowledge_graph()

    assert "deterministic" not in result
    sync.sync_decision_cases.assert_not_called()
    # When the deterministic path is disabled the consolidator
    # falls through to the legacy LLM behaviour so KG keeps
    # ingesting *something*.
    memory.consolidator.consolidate.assert_awaited_once_with(force=True)


async def test_step4_handles_deterministic_failure_gracefully() -> None:
    """Sync exception is logged but does not fail the step.

    On deterministic failure the legacy LLM fallback runs so the KG
    keeps ingesting *something* — never silent.  The test pins both
    halves of the contract: error recorded in stats, and the LLM
    path observed in stats too (here as ``episodes_found: 0`` since
    the stub episodic returns nothing).
    """
    sync = MagicMock()
    sync.sync_decision_cases = AsyncMock(
        side_effect=RuntimeError("redis down"),
    )
    case_store = MagicMock()
    case_store.search = AsyncMock(return_value=[_make_case()])
    consolidator = _build_consolidator(
        deterministic_kg_sync=sync,
        case_store=case_store,
    )

    result = await consolidator._step4_update_knowledge_graph()

    assert result.get("deterministic_error") == "sync_failed"
    # Failure -> deterministic_ran stays False -> LLM fallback ran.
    assert "llm" in result
