"""Tests for Mümin round-4 #2 (``/patterns/scenarios``) and #3 (pagination).

Mümin's 2026-05-08 round-4 feedback flagged two list-endpoint gaps:

* **#3** — ``/patterns/rules`` and ``/patterns/cases`` accepted ``limit``
  but never reported the unfiltered total ("ne kadar veri olduğunu
  anlayalım"), so a UI couldn't tell whether more pages existed.
  The fix adds ``offset`` to both requests, replaces the
  ``len(returned_page)`` total with the store-level unfiltered count,
  and surfaces ``has_more``.
* **#2** — no endpoint listed *which* scenarios a property has rules
  for.  ``GET /patterns/scenarios`` now groups
  ``rule_store.get_active_rules`` output by scenario and returns a
  per-scenario count plus the freshest ``last_seen_at`` anchor.

These tests pin both contracts via FastAPI ``TestClient`` against a
minimal app whose ``rule_store`` / ``case_store`` are
:class:`InMemoryPatternRuleStore` / :class:`InMemoryDecisionCaseStore`
seeded by the test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain_engine.api.pattern_endpoints import router
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternRule,
    PatternScope,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.store import (
    InMemoryDecisionCaseStore,
    InMemoryPatternRuleStore,
)

_BASE_TIME = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)


def _make_app(
    *,
    case_store: InMemoryDecisionCaseStore | None = None,
    rule_store: InMemoryPatternRuleStore | None = None,
) -> FastAPI:
    """Wire a minimal app with the patterns router + the supplied stores."""
    app = FastAPI()
    app.include_router(router)
    app.state.case_store = case_store or InMemoryDecisionCaseStore()
    app.state.rule_store = rule_store or InMemoryPatternRuleStore()
    return app


def _make_rule(
    *,
    pattern_id: str,
    scenario: Scenario,
    scope_id: str = "p1",
    action: DecisionType = DecisionType.INFORM,
    last_seen_at: datetime | None = None,
    conditions: dict[str, Any] | None = None,
) -> PatternRule:
    return PatternRule(
        pattern_id=pattern_id,
        scenario=scenario,
        scope=PatternScope.PROPERTY,
        scope_id=scope_id,
        conditions=conditions or {"adults": {"operator": "gte", "value": 2}},
        action=DecisionAction(action_type=action, params={}),
        support_count=10,
        confidence=0.7,
        last_seen_at=last_seen_at or _BASE_TIME,
    )


def _make_case(
    *,
    case_id: str,
    scenario: Scenario = Scenario.EARLY_CHECKIN,
    property_id: str = "p1",
    created_at: datetime | None = None,
) -> DecisionCase:
    return DecisionCase(
        case_id=case_id,
        scenario=scenario,
        stage=BookingStage.PRE_ARRIVAL,
        property_id=property_id,
        owner_id="",
        message_text="msg",
        decision=DecisionAction(action_type=DecisionType.APPROVE, params={}),
        outcome=CaseOutcome(
            resolution_type=ResolutionType.PM_APPROVED,
            successful=True,
            approved=True,
        ),
        source=CaseSource.LIVE,
        created_at=created_at or _BASE_TIME,
    )


# ---------------------------------------------------------------------------
# /patterns/rules — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_pagination_first_page_has_more_set() -> None:
    """5 rules, limit=2 offset=0 returns first 2 + has_more=True."""
    rule_store = InMemoryPatternRuleStore()
    for i in range(5):
        await rule_store.store(
            _make_rule(
                pattern_id=f"rule-{i}",
                scenario=Scenario.EARLY_CHECKIN,
            ),
        )
    app = _make_app(rule_store=rule_store)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1", "limit": 2, "offset": 0},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 5
    assert body["offset"] == 0
    assert body["limit"] == 2
    assert body["has_more"] is True
    assert len(body["rules"]) == 2


@pytest.mark.asyncio
async def test_rules_pagination_last_page_has_more_false() -> None:
    """Last page returns the tail and has_more=False."""
    rule_store = InMemoryPatternRuleStore()
    for i in range(5):
        await rule_store.store(
            _make_rule(
                pattern_id=f"rule-{i}",
                scenario=Scenario.EARLY_CHECKIN,
            ),
        )
    app = _make_app(rule_store=rule_store)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1", "limit": 2, "offset": 4},
    )

    body = response.json()
    assert body["total"] == 5
    assert body["offset"] == 4
    assert body["has_more"] is False
    assert len(body["rules"]) == 1


@pytest.mark.asyncio
async def test_rules_pagination_default_offset_is_zero() -> None:
    """Backward-compatible: callers that omit offset get the first page."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="rule-0",
            scenario=Scenario.EARLY_CHECKIN,
        ),
    )
    app = _make_app(rule_store=rule_store)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    body = response.json()
    assert body["total"] == 1
    assert body["offset"] == 0
    assert body["has_more"] is False


# ---------------------------------------------------------------------------
# /patterns/cases — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cases_pagination_total_reflects_unfiltered_count() -> None:
    """``total`` is the unfiltered count; ``cases`` is the limited slice."""
    case_store = InMemoryDecisionCaseStore()
    for i in range(7):
        await case_store.store(
            _make_case(
                case_id=f"c-{i}",
                created_at=_BASE_TIME - timedelta(minutes=i),
            ),
        )
    app = _make_app(case_store=case_store)
    client = TestClient(app)

    response = client.post(
        "/api/v1/patterns/cases",
        json={"property_id": "p1", "limit": 3, "offset": 0},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 7
    assert body["limit"] == 3
    assert body["offset"] == 0
    assert body["has_more"] is True
    assert len(body["cases"]) == 3


@pytest.mark.asyncio
async def test_cases_pagination_offset_advances_through_pages() -> None:
    """Sequential offset windows yield disjoint case_ids."""
    case_store = InMemoryDecisionCaseStore()
    for i in range(7):
        await case_store.store(
            _make_case(
                case_id=f"c-{i:02d}",
                created_at=_BASE_TIME - timedelta(minutes=i),
            ),
        )
    app = _make_app(case_store=case_store)
    client = TestClient(app)

    page_1 = client.post(
        "/api/v1/patterns/cases",
        json={"property_id": "p1", "limit": 3, "offset": 0},
    ).json()
    page_2 = client.post(
        "/api/v1/patterns/cases",
        json={"property_id": "p1", "limit": 3, "offset": 3},
    ).json()
    page_3 = client.post(
        "/api/v1/patterns/cases",
        json={"property_id": "p1", "limit": 3, "offset": 6},
    ).json()

    ids_1 = {c["case_id"] for c in page_1["cases"]}
    ids_2 = {c["case_id"] for c in page_2["cases"]}
    ids_3 = {c["case_id"] for c in page_3["cases"]}
    assert ids_1.isdisjoint(ids_2)
    assert ids_1.isdisjoint(ids_3)
    assert ids_2.isdisjoint(ids_3)
    assert page_1["has_more"] is True
    assert page_2["has_more"] is True
    assert page_3["has_more"] is False
    assert len(page_3["cases"]) == 1


# ---------------------------------------------------------------------------
# /patterns/scenarios — Mümin #2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenarios_endpoint_groups_by_scenario_with_counts() -> None:
    """Scenarios with rules surface with rule_count + last_seen_at."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="r1",
            scenario=Scenario.EARLY_CHECKIN,
            last_seen_at=_BASE_TIME,
        ),
    )
    await rule_store.store(
        _make_rule(
            pattern_id="r2",
            scenario=Scenario.EARLY_CHECKIN,
            last_seen_at=_BASE_TIME + timedelta(hours=2),
        ),
    )
    await rule_store.store(
        _make_rule(
            pattern_id="r3",
            scenario=Scenario.LATE_CHECKOUT,
            last_seen_at=_BASE_TIME + timedelta(hours=1),
        ),
    )
    app = _make_app(rule_store=rule_store)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/scenarios",
        params={"scope_id": "p1"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 2
    by_scenario = {s["scenario"]: s for s in body["scenarios"]}
    assert by_scenario["early_checkin"]["rule_count"] == 2
    assert by_scenario["late_checkout"]["rule_count"] == 1
    # last_seen_at should be the freshest across the scenario's rules.
    assert by_scenario["early_checkin"]["last_seen_at"] == (
        (_BASE_TIME + timedelta(hours=2)).isoformat()
    )


@pytest.mark.asyncio
async def test_scenarios_endpoint_excludes_other_scopes() -> None:
    """Only rules with the matching scope_id are reported."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="match",
            scenario=Scenario.EARLY_CHECKIN,
            scope_id="p1",
        ),
    )
    await rule_store.store(
        _make_rule(
            pattern_id="other",
            scenario=Scenario.LATE_CHECKOUT,
            scope_id="other-property",
        ),
    )
    app = _make_app(rule_store=rule_store)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/scenarios",
        params={"scope_id": "p1"},
    )

    body = response.json()
    assert body["total"] == 1
    assert body["scenarios"][0]["scenario"] == "early_checkin"


@pytest.mark.asyncio
async def test_scenarios_endpoint_empty_when_no_rules() -> None:
    """Empty store returns total=0 with an empty list."""
    app = _make_app()
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/scenarios",
        params={"scope_id": "p1"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["scenarios"] == []
