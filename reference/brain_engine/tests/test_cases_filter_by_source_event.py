"""Tests for ``POST /api/v1/patterns/cases?source_event_id=...``.

Mümin 2026-05-15 round-5 #4 — the FL-12 endpoint
``GET /api/v1/patterns/rules/{rule_id}/origin`` returns a
``source_event_ids`` array (PR-B + PR-C deliver the data); Mümin
needs a way to drill from each event id back to the cases that
produced it.  Previously the ``/patterns/cases`` endpoint only
filtered on ``scenario`` / ``property_id`` / ``owner_id`` / ``stage``
— there was no way to look up cases by upstream event id without
shell access to the DB.

PR-D adds:

* ``source_event_id`` query param to :class:`CaseSearchRequest`.
* Matching filter on both :class:`InMemoryDecisionCaseStore` and
  the Postgres-side ``_build_search_query`` / ``_build_count_query``
  query builders.  The Postgres path uses the migration-028 GIN
  index on the ``origin`` JSONB column.
* ``foundation_scenario_id`` + ``source_event_ids`` echoed on each
  case dict in the response so the row is self-explaining.

These tests pin:

1. In-memory store filters correctly (single match, no match,
   multiple matches, combined with other filters).
2. Postgres query builder emits the JSONB containment clause when
   ``source_event_id`` is supplied.
3. The endpoint round-trips through the request payload and
   surfaces the new fields on each case dict.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain_engine.api.pattern_endpoints import router
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.postgres_store import (
    _build_count_query,
    _build_search_query,
)
from brain_engine.patterns.store import InMemoryDecisionCaseStore

_BASE_TIME = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


def _case(
    *,
    case_id: str,
    property_id: str = "prop-1",
    source_event_ids: tuple[str, ...] = (),
    foundation_scenario_id: str | None = None,
) -> DecisionCase:
    """Build a minimal :class:`DecisionCase` with the requested origin."""
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.EARLY_CHECKIN,
        property_id=property_id,
        owner_id="owner-1",
        decision=DecisionAction(
            action_type=DecisionType.APPROVE,
            params={},
        ),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        created_at=_BASE_TIME,
        foundation_scenario_id=foundation_scenario_id,
        origin=PatternOrigin(source_event_ids=source_event_ids),
    )


# ---------------------------------------------------------------------------
# InMemoryDecisionCaseStore — search by source_event_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_search_returns_only_matching_event_id() -> None:
    """Only cases whose origin tuple contains the id are returned."""
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(case_id="c-match", source_event_ids=("evt-target",)),
    )
    await store.store(
        _case(case_id="c-miss", source_event_ids=("evt-other",)),
    )
    await store.store(
        _case(case_id="c-empty", source_event_ids=()),
    )

    results = await store.search(source_event_id="evt-target")
    assert [c.case_id for c in results] == ["c-match"]


@pytest.mark.asyncio
async def test_in_memory_search_event_id_no_match_returns_empty() -> None:
    """An unknown event id yields zero rows, not an exception."""
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(case_id="c1", source_event_ids=("evt-a",)),
    )

    results = await store.search(source_event_id="evt-unknown")
    assert results == []


@pytest.mark.asyncio
async def test_in_memory_search_event_id_with_other_filters() -> None:
    """``source_event_id`` AND-combines with the other filters."""
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(
            case_id="c-prop1",
            property_id="prop-1",
            source_event_ids=("evt-shared",),
        ),
    )
    await store.store(
        _case(
            case_id="c-prop2",
            property_id="prop-2",
            source_event_ids=("evt-shared",),
        ),
    )

    results = await store.search(
        property_id="prop-1",
        source_event_id="evt-shared",
    )
    assert [c.case_id for c in results] == ["c-prop1"]


@pytest.mark.asyncio
async def test_in_memory_count_event_id_matches_search() -> None:
    """``count`` reflects the same filter set used by ``search``."""
    store = InMemoryDecisionCaseStore()
    for index in range(3):
        await store.store(
            _case(
                case_id=f"c-{index}",
                source_event_ids=("evt-x",),
            ),
        )
    await store.store(
        _case(case_id="c-other", source_event_ids=("evt-y",)),
    )

    total = await store.count(source_event_id="evt-x")
    assert total == 3


@pytest.mark.asyncio
async def test_in_memory_search_matches_when_event_id_is_one_of_many() -> None:
    """A case whose origin lists several events still matches each one."""
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(
            case_id="multi",
            source_event_ids=("evt-a", "evt-b", "evt-c"),
        ),
    )

    for needle in ("evt-a", "evt-b", "evt-c"):
        results = await store.search(source_event_id=needle)
        assert [c.case_id for c in results] == ["multi"]


# ---------------------------------------------------------------------------
# Postgres query builder — JSONB containment clause
# ---------------------------------------------------------------------------


def test_build_search_query_omits_origin_clause_when_event_id_absent() -> None:
    """Without ``source_event_id`` the SQL stays untouched."""
    sql, _args = _build_search_query(
        scenario=None,
        property_id=None,
        owner_id=None,
        stage=None,
        source_event_id=None,
        limit=10,
    )
    assert "origin @>" not in sql


def test_build_search_query_adds_containment_clause_for_event_id() -> None:
    """The JSONB containment predicate uses the migration-028 GIN index."""
    sql, args = _build_search_query(
        scenario=None,
        property_id=None,
        owner_id=None,
        stage=None,
        source_event_id="evt-target",
        limit=10,
    )
    assert "origin @>" in sql
    assert "::jsonb" in sql
    # Payload is rendered as JSON so the GIN ``jsonb_path_ops`` index
    # on ``origin`` can be used directly.
    payload = next(
        arg for arg in args if isinstance(arg, str) and arg.startswith("{")
    )
    assert json.loads(payload) == {"source_event_ids": ["evt-target"]}


def test_build_count_query_adds_containment_clause_for_event_id() -> None:
    """The count query mirrors search so totals stay coherent."""
    sql, args = _build_count_query(
        scenario=None,
        property_id=None,
        owner_id=None,
        stage=None,
        source_event_id="evt-target",
    )
    assert "origin @>" in sql
    assert any(
        isinstance(arg, str)
        and json.loads(arg) == {"source_event_ids": ["evt-target"]}
        for arg in args
    )


# ---------------------------------------------------------------------------
# Endpoint round-trip — payload threads through to the store
# ---------------------------------------------------------------------------


def _make_app(case_store: InMemoryDecisionCaseStore) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.case_store = case_store
    return app


@pytest.mark.asyncio
async def test_endpoint_filters_by_source_event_id() -> None:
    """``source_event_id`` in the JSON body restricts the response."""
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(
            case_id="hit",
            source_event_ids=("evt-target",),
            foundation_scenario_id="s2_63_guest_asks_if_early_checkin_is",
        ),
    )
    await store.store(
        _case(
            case_id="miss",
            source_event_ids=("evt-other",),
        ),
    )

    client = TestClient(_make_app(store))
    response = client.post(
        "/api/v1/patterns/cases",
        json={
            "property_id": "prop-1",
            "source_event_id": "evt-target",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    assert len(body["cases"]) == 1
    only = body["cases"][0]
    assert only["case_id"] == "hit"
    assert only["source_event_ids"] == ["evt-target"]
    assert only["foundation_scenario_id"] == (
        "s2_63_guest_asks_if_early_checkin_is"
    )


@pytest.mark.asyncio
async def test_endpoint_returns_empty_page_for_unknown_event_id() -> None:
    """An unknown event id yields an empty cases list and total=0."""
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(case_id="c1", source_event_ids=("evt-real",)),
    )

    client = TestClient(_make_app(store))
    response = client.post(
        "/api/v1/patterns/cases",
        json={
            "property_id": "prop-1",
            "source_event_id": "evt-unknown",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["cases"] == []
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_endpoint_response_surfaces_origin_fields_when_no_filter() -> (
    None
):
    """``foundation_scenario_id`` + ``source_event_ids`` always echoed."""
    store = InMemoryDecisionCaseStore()
    await store.store(
        _case(
            case_id="c1",
            source_event_ids=("evt-1", "evt-2"),
            foundation_scenario_id="s1_16_early_checkin",
        ),
    )

    client = TestClient(_make_app(store))
    response = client.post(
        "/api/v1/patterns/cases",
        json={"property_id": "prop-1"},
    )

    body = response.json()
    only = body["cases"][0]
    assert only["source_event_ids"] == ["evt-1", "evt-2"]
    assert only["foundation_scenario_id"] == "s1_16_early_checkin"
