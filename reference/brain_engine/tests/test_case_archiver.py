"""Tests for the DecisionCase soft-archive (Sprint 4).

Covers both halves of the contract:

1. **Store-level** — ``InMemoryDecisionCaseStore`` (used by every
   in-process test in this repo) honours the same archive +
   ``include_archived`` semantics the Postgres store implements.
   This guarantees parity so tests written against the in-memory
   stub keep producing the same results when wired against the
   real Postgres store.
2. **Archiver-level** — :class:`CaseArchiver` selects only cases
   older than ``now() - retention_days``, archives them
   idempotently, surfaces a structured report, and never
   touches rows inside the retention window.

The "not referenced by any active rule" filter the Postgres
store applies is documented in the in-memory stub's docstring as
out of scope (the in-memory store does not see PatternRules), so
those tests are pinned at the integration / Postgres layer
separately.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from brain_engine.patterns.case_archiver import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_RETENTION_DAYS,
    CaseArchiver,
)
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionCase,
    DecisionType,
    Scenario,
)
from brain_engine.patterns.store import InMemoryDecisionCaseStore

_NOW = datetime(2026, 5, 5, tzinfo=UTC)


def _case(
    *,
    case_id: str,
    created_at: datetime,
    archived_at: datetime | None = None,
) -> DecisionCase:
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.IN_STAY,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        property_id="p1",
        owner_id="o1",
        decision=DecisionAction(
            action_type=DecisionType.INFORM, params={},
        ),
        created_at=created_at,
        archived_at=archived_at,
    )


# ---------------------------------------------------------------------------
# Store-level archive + filter parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_flips_archived_at_once() -> None:
    store = InMemoryDecisionCaseStore()
    case = _case(case_id="C1", created_at=_NOW - timedelta(days=120))
    await store.store(case)
    assert await store.archive("C1") is True
    # Idempotent: re-archiving an already-archived row is a no-op.
    assert await store.archive("C1") is False


@pytest.mark.asyncio
async def test_archive_unknown_case_returns_false() -> None:
    store = InMemoryDecisionCaseStore()
    assert await store.archive("does-not-exist") is False


@pytest.mark.asyncio
async def test_search_excludes_archived_by_default() -> None:
    store = InMemoryDecisionCaseStore()
    fresh = _case(case_id="FRESH", created_at=_NOW - timedelta(days=1))
    stale = _case(case_id="STALE", created_at=_NOW - timedelta(days=120))
    await store.store(fresh)
    await store.store(stale)
    await store.archive("STALE")
    visible = await store.search()
    assert {c.case_id for c in visible} == {"FRESH"}


@pytest.mark.asyncio
async def test_search_include_archived_returns_full_table() -> None:
    store = InMemoryDecisionCaseStore()
    fresh = _case(case_id="FRESH", created_at=_NOW - timedelta(days=1))
    stale = _case(case_id="STALE", created_at=_NOW - timedelta(days=120))
    await store.store(fresh)
    await store.store(stale)
    await store.archive("STALE")
    visible = await store.search(include_archived=True)
    assert {c.case_id for c in visible} == {"FRESH", "STALE"}


@pytest.mark.asyncio
async def test_select_archive_candidates_returns_oldest_first() -> None:
    store = InMemoryDecisionCaseStore()
    # Three stale cases at decreasing ages, plus one fresh case
    # that must NOT be selected.
    await store.store(
        _case(case_id="A", created_at=_NOW - timedelta(days=200)),
    )
    await store.store(
        _case(case_id="B", created_at=_NOW - timedelta(days=150)),
    )
    await store.store(
        _case(case_id="C", created_at=_NOW - timedelta(days=100)),
    )
    await store.store(
        _case(case_id="FRESH", created_at=_NOW - timedelta(days=10)),
    )
    cutoff = _NOW - timedelta(days=90)
    candidates = await store.select_archive_candidates(cutoff=cutoff)
    assert candidates == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# CaseArchiver behaviour
# ---------------------------------------------------------------------------


def test_constructor_rejects_invalid_retention() -> None:
    store = InMemoryDecisionCaseStore()
    with pytest.raises(ValueError):
        CaseArchiver(store, retention_days=0)


def test_constructor_rejects_invalid_batch_limit() -> None:
    store = InMemoryDecisionCaseStore()
    with pytest.raises(ValueError):
        CaseArchiver(store, batch_limit=0)


def test_constructor_defaults() -> None:
    store = InMemoryDecisionCaseStore()
    archiver = CaseArchiver(store)
    assert archiver._retention_days == DEFAULT_RETENTION_DAYS
    assert archiver._batch_limit == DEFAULT_BATCH_LIMIT


@pytest.mark.asyncio
async def test_archive_stale_cases_skips_within_retention() -> None:
    store = InMemoryDecisionCaseStore()
    fresh = _case(case_id="F", created_at=_NOW - timedelta(days=10))
    await store.store(fresh)
    archiver = CaseArchiver(store, retention_days=90)
    report = await archiver.archive_stale_cases(now=_NOW)
    assert report.candidates == 0
    assert report.archived == 0


@pytest.mark.asyncio
async def test_archive_stale_cases_archives_only_stale() -> None:
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(case_id="STALE", created_at=_NOW - timedelta(days=120)),
    )
    await store.store(
        _case(case_id="FRESH", created_at=_NOW - timedelta(days=10)),
    )
    archiver = CaseArchiver(store, retention_days=90)
    report = await archiver.archive_stale_cases(now=_NOW)
    assert report.archived == 1
    visible = await store.search()
    assert {c.case_id for c in visible} == {"FRESH"}


@pytest.mark.asyncio
async def test_archive_stale_cases_idempotent() -> None:
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(case_id="STALE", created_at=_NOW - timedelta(days=120)),
    )
    archiver = CaseArchiver(store, retention_days=90)
    first = await archiver.archive_stale_cases(now=_NOW)
    second = await archiver.archive_stale_cases(now=_NOW)
    assert first.archived == 1
    # Re-running picks zero candidates because the stale row is
    # now archived → excluded from select_archive_candidates.
    assert second.candidates == 0
    assert second.archived == 0


@pytest.mark.asyncio
async def test_run_nightly_returns_json_dict() -> None:
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(case_id="STALE", created_at=_NOW - timedelta(days=120)),
    )
    archiver = CaseArchiver(store, retention_days=90)
    payload = await archiver.run_nightly()
    assert payload["candidates"] >= 1
    assert payload["archived"] >= 0
    assert "cutoff" in payload
    assert payload["retention_days"] == 90


@pytest.mark.asyncio
async def test_archive_stale_cases_respects_batch_limit() -> None:
    store = InMemoryDecisionCaseStore()
    for i in range(5):
        await store.store(
            _case(
                case_id=f"S{i}",
                created_at=_NOW - timedelta(days=120 + i),
            ),
        )
    archiver = CaseArchiver(store, retention_days=90, batch_limit=2)
    report = await archiver.archive_stale_cases(now=_NOW)
    assert report.candidates == 2
    assert report.archived == 2


@pytest.mark.asyncio
async def test_archive_preserves_immutability_of_original() -> None:
    store = InMemoryDecisionCaseStore()
    case = _case(case_id="C1", created_at=_NOW - timedelta(days=120))
    await store.store(case)
    await store.archive("C1")
    # The original frozen dataclass instance the caller still
    # holds must remain untouched (master_guide_2026: value
    # objects never mutate).  The store's copy is the one with
    # archived_at set.
    assert case.archived_at is None
    fetched = await store.get("C1")
    assert fetched is not None
    assert fetched.archived_at is not None


# ---------------------------------------------------------------------------
# Pre-archived candidates (defensive — should never re-archive)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_candidates_skips_already_archived() -> None:
    store = InMemoryDecisionCaseStore()
    pre_archived = replace(
        _case(case_id="OLD", created_at=_NOW - timedelta(days=200)),
        archived_at=_NOW - timedelta(days=10),
    )
    await store.store(pre_archived)
    candidates = await store.select_archive_candidates(
        cutoff=_NOW - timedelta(days=90),
    )
    assert candidates == []
