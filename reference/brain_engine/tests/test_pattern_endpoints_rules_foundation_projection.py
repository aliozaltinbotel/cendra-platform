"""Tests for the foundation projection on ``GET /api/v1/patterns/rules``.

Mümin 2026-05-15 round-5 #1 — the rule listing emitted
``stage: null`` for the Excel ``Stage`` column because the legacy
projection only surfaced the :class:`BookingStage` enum value and
omitted the foundation slug entirely.  PR-A adds three additive
fields derived from the catalog row:

* ``foundation_scenario_id`` — slug stored on the rule.
* ``stage_group``           — Excel long form (``"Stage N — …"``).
* ``stage_excel``           — Excel short form (workbook ``Stage``).

These tests pin the contract via FastAPI ``TestClient`` against a
minimal app whose ``rule_store`` is :class:`InMemoryPatternRuleStore`
and whose ``foundation_catalog_store`` is
:class:`InMemoryFoundationCatalogStore` seeded by the test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain_engine.api.pattern_endpoints import router
from brain_engine.patterns.foundation_catalog_store import (
    InMemoryFoundationCatalogStore,
)
from brain_engine.patterns.foundation_registry import FoundationScenario
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionType,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.store import InMemoryPatternRuleStore

_BASE_TIME = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)

_FOUNDATION_SLUG = "s2_63_guest_asks_if_early_checkin_is"


def _make_app(
    *,
    rule_store: InMemoryPatternRuleStore | None = None,
    catalog_store: InMemoryFoundationCatalogStore | None = None,
) -> FastAPI:
    """Wire a minimal app with the patterns router + the supplied stores."""
    app = FastAPI()
    app.include_router(router)
    app.state.rule_store = rule_store or InMemoryPatternRuleStore()
    if catalog_store is not None:
        app.state.foundation_catalog_store = catalog_store
    return app


def _make_rule(
    *,
    pattern_id: str,
    scenario: Scenario = Scenario.EARLY_CHECKIN,
    foundation_scenario_id: str | None = None,
    stage: BookingStage | None = BookingStage.CHECKIN,
    scope_id: str = "p1",
) -> PatternRule:
    """Build a PatternRule with optional foundation enrichment."""
    return PatternRule(
        pattern_id=pattern_id,
        scenario=scenario,
        scope=PatternScope.PROPERTY,
        scope_id=scope_id,
        stage=stage,
        conditions={"adults": {"operator": "gte", "value": 2}},
        action=DecisionAction(action_type=DecisionType.INFORM, params={}),
        support_count=10,
        confidence=0.7,
        last_seen_at=_BASE_TIME,
        foundation_scenario_id=foundation_scenario_id,
    )


def _seed_catalog(
    slug: str = _FOUNDATION_SLUG,
) -> InMemoryFoundationCatalogStore:
    """Return an in-memory catalog seeded with a single scenario row."""
    store = InMemoryFoundationCatalogStore()
    store._rows[slug] = FoundationScenario(
        scenario_id=slug,
        title="Guest asks if early check-in is available",
        stage_number=2,
        stage_label="Booking confirmation",
        trigger="early check-in",
    )
    store._doc_hash = "test-hash"
    return store


# ---------------------------------------------------------------------------
# Happy path — rule with foundation_scenario_id + catalog match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_with_foundation_slug_emits_excel_stage_fields() -> None:
    """A foundation-tagged rule surfaces the full Excel projection."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="rule-foundation",
            foundation_scenario_id=_FOUNDATION_SLUG,
        ),
    )
    catalog = _seed_catalog()
    app = _make_app(rule_store=rule_store, catalog_store=catalog)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    rule = body["rules"][0]
    assert rule["foundation_scenario_id"] == _FOUNDATION_SLUG
    assert rule["stage_group"] == "Stage 2 — Booking confirmation"
    assert rule["stage_excel"] == "Booking confirmation"
    # Legacy fields stay intact for backward compatibility.
    assert rule["stage"] == BookingStage.CHECKIN.value
    assert rule["scenario"] == Scenario.EARLY_CHECKIN.value


# ---------------------------------------------------------------------------
# Legacy rule — no foundation_scenario_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_rule_without_foundation_id_emits_null_extras() -> None:
    """Rules pre-PR #288 (no foundation slug) emit ``null`` for new fields."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="rule-legacy",
            foundation_scenario_id=None,
        ),
    )
    catalog = _seed_catalog()
    app = _make_app(rule_store=rule_store, catalog_store=catalog)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    rule = response.json()["rules"][0]
    assert rule["foundation_scenario_id"] is None
    assert rule["stage_group"] is None
    assert rule["stage_excel"] is None
    # Legacy stage value still emitted from the enum.
    assert rule["stage"] == BookingStage.CHECKIN.value


# ---------------------------------------------------------------------------
# Graceful degradation — catalog store not wired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_returns_null_extras_when_catalog_store_missing() -> (
    None
):
    """Endpoint still serves rules when the catalog store is absent."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="rule-foundation",
            foundation_scenario_id=_FOUNDATION_SLUG,
        ),
    )
    # No catalog_store passed → endpoint must degrade gracefully.
    app = _make_app(rule_store=rule_store, catalog_store=None)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    body = response.json()
    assert response.status_code == 200
    rule = body["rules"][0]
    # foundation_scenario_id still echoed (it lives on the rule, not
    # in the catalog), but the derived Excel fields are null.
    assert rule["foundation_scenario_id"] == _FOUNDATION_SLUG
    assert rule["stage_group"] is None
    assert rule["stage_excel"] is None


# ---------------------------------------------------------------------------
# Foundation slug references a row absent from the catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_foundation_slug_emits_null_excel_fields() -> None:
    """Rule slug not present in the catalog → null Excel fields."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="rule-orphan",
            foundation_scenario_id="s9_999_unknown_slug",
        ),
    )
    catalog = _seed_catalog()  # only seeded with _FOUNDATION_SLUG
    app = _make_app(rule_store=rule_store, catalog_store=catalog)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    rule = response.json()["rules"][0]
    assert rule["foundation_scenario_id"] == "s9_999_unknown_slug"
    assert rule["stage_group"] is None
    assert rule["stage_excel"] is None


# ---------------------------------------------------------------------------
# Catalog lookup is invoked at most once per request
# ---------------------------------------------------------------------------


class _CountingCatalogStore(InMemoryFoundationCatalogStore):
    """Subclass that records how many times ``list_all`` was awaited."""

    list_all_calls: int

    def __init__(self) -> None:
        super().__init__()
        self.list_all_calls = 0

    async def list_all(self) -> tuple[FoundationScenario, ...]:
        self.list_all_calls += 1
        return await super().list_all()


@pytest.mark.asyncio
async def test_multiple_foundation_rules_share_one_catalog_round_trip() -> (
    None
):
    """Listing N foundation-tagged rules issues a single ``list_all``."""
    rule_store = InMemoryPatternRuleStore()
    for index in range(3):
        await rule_store.store(
            _make_rule(
                pattern_id=f"rule-{index}",
                foundation_scenario_id=_FOUNDATION_SLUG,
            ),
        )
    catalog = _CountingCatalogStore()
    catalog._rows[_FOUNDATION_SLUG] = FoundationScenario(
        scenario_id=_FOUNDATION_SLUG,
        title="Guest asks if early check-in is available",
        stage_number=2,
        stage_label="Booking confirmation",
        trigger="early check-in",
    )
    catalog._doc_hash = "test-hash"
    app = _make_app(rule_store=rule_store, catalog_store=catalog)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    assert response.status_code == 200
    assert len(response.json()["rules"]) == 3
    assert catalog.list_all_calls == 1


# ---------------------------------------------------------------------------
# Catalog lookup is skipped entirely when no rule carries a slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_catalog_call_when_no_rule_has_foundation_id() -> None:
    """``list_all`` is never called when the page has no foundation slugs."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="rule-legacy",
            foundation_scenario_id=None,
        ),
    )
    catalog = _CountingCatalogStore()
    app = _make_app(rule_store=rule_store, catalog_store=catalog)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    assert response.status_code == 200
    rule = response.json()["rules"][0]
    assert rule["foundation_scenario_id"] is None
    assert catalog.list_all_calls == 0


# ---------------------------------------------------------------------------
# Catalog ``list_all`` failure → degrade to null Excel fields, not 500
# ---------------------------------------------------------------------------


class _FailingCatalogStore(InMemoryFoundationCatalogStore):
    """Store whose ``list_all`` raises to simulate a flaky backend."""

    async def list_all(self) -> tuple[FoundationScenario, ...]:
        raise RuntimeError("simulated catalog backend failure")


@pytest.mark.asyncio
async def test_catalog_failure_does_not_break_endpoint() -> None:
    """A raised RuntimeError inside ``list_all`` degrades to null fields."""
    rule_store = InMemoryPatternRuleStore()
    await rule_store.store(
        _make_rule(
            pattern_id="rule-foundation",
            foundation_scenario_id=_FOUNDATION_SLUG,
        ),
    )
    catalog: Any = _FailingCatalogStore()
    app = _make_app(rule_store=rule_store, catalog_store=catalog)
    client = TestClient(app)

    response = client.get(
        "/api/v1/patterns/rules",
        params={"scope_id": "p1"},
    )

    body = response.json()
    assert response.status_code == 200
    rule = body["rules"][0]
    assert rule["foundation_scenario_id"] == _FOUNDATION_SLUG
    assert rule["stage_group"] is None
    assert rule["stage_excel"] is None
