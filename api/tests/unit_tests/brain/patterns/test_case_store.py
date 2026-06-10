"""SQLAlchemy DecisionCase store behaviour against in-memory SQLite.

Mirrors the in-memory store contract from test_store.py for the
persistent implementation, plus tenant isolation, idempotent append,
and the active-rule reference guard on archive candidates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.patterns.case_store import SQLAlchemyDecisionCaseStore
from core.brain.patterns.models import (
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    PatternRule,
    PatternScope,
    ResolutionType,
)
from core.brain.patterns.rule_store import SQLAlchemyPatternRuleStore
from models.brain_decision import BrainDecisionCase
from models.brain_rules import BrainPatternRule

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainDecisionCase.__table__.create(engine)
    BrainPatternRule.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def store(session_maker) -> SQLAlchemyDecisionCaseStore:
    return SQLAlchemyDecisionCaseStore(session_maker=session_maker, tenant_id=TENANT)


def _case(**overrides) -> DecisionCase:
    base = {
        "stage": "in_stay",
        "scenario": "noise_complaint",
        "property_id": "prop-1",
        "owner_id": "own-1",
        "decision": DecisionAction(action_type=DecisionType.INFORM, params={"note": "x"}),
    }
    base.update(overrides)
    return DecisionCase(**base)


def test_round_trip_preserves_fields(store):
    case = _case(
        reservation_id="res-9",
        guest_id="g-3",
        message_text="can we check in early?",
        message_language="de",
        extracted_entities={"time": "10:00"},
        pms_snapshot={"status": "confirmed"},
        outcome=CaseOutcome(
            approved=True,
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
            revenue_impact=42.5,
        ),
        evidence_source_ids=("m1",),
        source=CaseSource.HISTORICAL,
        orchestrator_verdict={"tier": "T2", "action": "approve"},
        foundation_scenario_id="fs-1",
        origin=PatternOrigin(source_event_ids=("ev-1",)),
    )
    store.store(case)
    loaded = store.get(case.case_id)
    assert loaded is not None
    assert loaded.stage == "in_stay"
    assert loaded.scenario == "noise_complaint"
    assert loaded.reservation_id == "res-9"
    assert loaded.message_language == "de"
    assert loaded.extracted_entities == {"time": "10:00"}
    assert loaded.decision.action_type is DecisionType.INFORM
    assert loaded.outcome.resolution_type is ResolutionType.PM_APPROVED
    assert loaded.outcome.revenue_impact == 42.5
    assert loaded.source is CaseSource.HISTORICAL
    assert loaded.orchestrator_verdict == {"tier": "T2", "action": "approve"}
    assert loaded.origin == case.origin
    assert loaded.created_at.tzinfo is not None
    assert loaded.archived_at is None


def test_store_is_idempotent_append(store, session_maker):
    case = _case()
    store.store(case)
    # same case_id with different payload is a no-op, not an update
    from dataclasses import replace

    store.store(replace(case, message_text="changed"))
    with session_maker() as session:
        assert session.query(BrainDecisionCase).count() == 1
    assert store.get(case.case_id).message_text == ""


def test_tenant_isolation(session_maker):
    a = SQLAlchemyDecisionCaseStore(session_maker=session_maker, tenant_id=TENANT)
    b = SQLAlchemyDecisionCaseStore(session_maker=session_maker, tenant_id=OTHER_TENANT)
    case = _case()
    a.store(case)
    assert b.get(case.case_id) is None
    assert b.search() == []
    assert b.count() == 0
    assert b.archive(case.case_id) is False


def test_search_filters_and_pagination(store):
    t0 = datetime.now(UTC)
    cases = [_case(created_at=t0 + timedelta(seconds=i)) for i in range(5)]
    for case in cases:
        store.store(case)
    store.store(_case(scenario="late_checkout", created_at=t0 + timedelta(seconds=9)))
    page1 = store.search(scenario="noise_complaint", limit=2)
    page2 = store.search(scenario="noise_complaint", limit=2, offset=2)
    assert [c.case_id for c in page1] == [cases[4].case_id, cases[3].case_id]
    assert [c.case_id for c in page2] == [cases[2].case_id, cases[1].case_id]
    assert store.count(scenario="noise_complaint") == 5
    assert store.count() == 6


def test_search_by_source_event_id_fallback(store):
    tagged = _case(origin=PatternOrigin(source_event_ids=("ev-7", "ev-8")))
    store.store(tagged)
    store.store(_case())
    assert [c.case_id for c in store.search(source_event_id="ev-7")] == [tagged.case_id]
    assert store.search(source_event_id="ev-x") == []
    assert store.count(source_event_id="ev-7") == 1


def test_archive_idempotent_and_excluded_by_default(store):
    case = _case()
    store.store(case)
    assert store.archive(case.case_id) is True
    assert store.archive(case.case_id) is False
    assert store.archive("missing") is False
    assert store.search() == []
    assert len(store.search(include_archived=True)) == 1
    assert store.count() == 0
    loaded = store.get(case.case_id)
    assert loaded.archived_at is not None


def test_get_by_reservation_oldest_first(store):
    t0 = datetime.now(UTC)
    newer = _case(reservation_id="res-1", created_at=t0 + timedelta(seconds=2))
    older = _case(reservation_id="res-1", created_at=t0)
    store.store(newer)
    store.store(older)
    store.store(_case(reservation_id="res-2"))
    results = store.get_by_reservation("res-1")
    assert [c.case_id for c in results] == [older.case_id, newer.case_id]


def test_archive_candidates_respect_active_rule_references(store, session_maker):
    t0 = datetime.now(UTC)
    referenced = _case(created_at=t0 - timedelta(days=120))
    unreferenced = _case(created_at=t0 - timedelta(days=120))
    fresh = _case(created_at=t0)
    for case in (referenced, unreferenced, fresh):
        store.store(case)

    rule_store = SQLAlchemyPatternRuleStore(session_maker=session_maker, tenant_id=TENANT)
    rule = PatternRule(
        scenario="noise_complaint",
        scope=PatternScope.PROPERTY,
        scope_id="prop-1",
        conditions={},
        action=DecisionAction(action_type=DecisionType.INFORM),
        source_case_ids=(referenced.case_id,),
    )
    rule_store.store(rule)

    cutoff = t0 - timedelta(days=90)
    assert store.select_archive_candidates(cutoff=cutoff) == [unreferenced.case_id]

    # deactivating the rule releases the referenced case
    rule_store.deactivate(rule.pattern_id)
    assert set(store.select_archive_candidates(cutoff=cutoff)) == {
        referenced.case_id,
        unreferenced.case_id,
    }


def test_empty_tenant_rejected(session_maker):
    with pytest.raises(ValueError, match="tenant_id"):
        SQLAlchemyDecisionCaseStore(session_maker=session_maker, tenant_id="")
