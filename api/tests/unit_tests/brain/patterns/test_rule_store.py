"""SQLAlchemy PatternRule store behaviour against in-memory SQLite.

Pins the Protocol semantics established by test_store.py for the
persistent implementation, plus the Dify-specific contracts: tenant
isolation, UPSERT-on-pattern_id, deactivated_at COALESCE stamping, and
tz-aware round-trips through naive-UTC columns.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.patterns.models import (
    DecisionAction,
    DecisionType,
    ExecutionMode,
    PatternOrigin,
    PatternRule,
    PatternScope,
    RiskLevel,
)
from core.brain.patterns.rule_store import SQLAlchemyPatternRuleStore
from models.brain_rules import BrainPatternRule

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainPatternRule.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def store(session_maker) -> SQLAlchemyPatternRuleStore:
    return SQLAlchemyPatternRuleStore(session_maker=session_maker, tenant_id=TENANT)


def _rule(**overrides) -> PatternRule:
    base = {
        "scenario": "discount_request",
        "scope": PatternScope.PROPERTY,
        "scope_id": "prop-1",
        "conditions": {"nights": {"operator": "gte", "value": 5}},
        "action": DecisionAction(action_type=DecisionType.APPROVE, params={"pct": 10}),
        "risk_level": RiskLevel.LOW,
        "execution_mode": ExecutionMode.AUTO,
        "origin": PatternOrigin(source_event_ids=("ev-1",)),
    }
    base.update(overrides)
    return PatternRule(**base)


def test_round_trip_preserves_every_field(store):
    rule = _rule(
        blocker_types=("maintenance",),
        support_count=7,
        counterexample_count=1,
        confidence=0.81,
        stage="in_stay",
        valid_to=datetime.now(UTC) + timedelta(days=30),
        source_case_ids=("c1", "c2"),
        foundation_scenario_id="fs-9",
        rationale="PM consistently approves",
    )
    # rationale rides in action.params per the reference convention
    rule = replace(
        rule,
        action=DecisionAction(
            action_type=rule.action.action_type,
            params={**rule.action.params, "_rationale": rule.rationale},
        ),
    )
    store.store(rule)
    loaded = store.get(rule.pattern_id)
    assert loaded is not None
    assert loaded.scenario == rule.scenario
    assert loaded.scope is PatternScope.PROPERTY
    assert loaded.conditions == rule.conditions
    assert loaded.action.action_type is DecisionType.APPROVE
    assert loaded.blocker_types == ("maintenance",)
    assert loaded.support_count == 7
    assert loaded.confidence == pytest.approx(0.81)
    assert loaded.risk_level is RiskLevel.LOW
    assert loaded.execution_mode is ExecutionMode.AUTO
    assert loaded.valid_from.tzinfo is not None  # tz-aware contract restored
    assert loaded.valid_to is not None
    assert loaded.valid_to.tzinfo is not None
    assert loaded.source_case_ids == ("c1", "c2")
    assert loaded.foundation_scenario_id == "fs-9"
    assert loaded.origin == rule.origin
    assert loaded.rationale == "PM consistently approves"
    # stage is observed metadata, not persisted by the reference store either
    assert loaded.active is True


def test_store_is_upsert_on_pattern_id(store, session_maker):
    rule = _rule()
    store.store(rule)
    store.store(replace(rule, confidence=0.95, support_count=12))
    with session_maker() as session:
        assert session.query(BrainPatternRule).count() == 1
    loaded = store.get(rule.pattern_id)
    assert loaded.confidence == pytest.approx(0.95)
    assert loaded.support_count == 12


def test_get_missing_returns_none(store):
    assert store.get("missing") is None


def test_tenant_isolation(session_maker):
    a = SQLAlchemyPatternRuleStore(session_maker=session_maker, tenant_id=TENANT)
    b = SQLAlchemyPatternRuleStore(session_maker=session_maker, tenant_id=OTHER_TENANT)
    rule = _rule()
    a.store(rule)
    assert b.get(rule.pattern_id) is None
    assert b.get_active_rules() == []
    assert b.deactivate(rule.pattern_id) is False
    # same pattern_id may exist independently per tenant
    b.store(replace(rule, confidence=0.1))
    assert a.get(rule.pattern_id).confidence != pytest.approx(0.1)


def test_get_active_rules_filters_and_sorts(store):
    high = _rule(confidence=0.9)
    low = _rule(scope_id="prop-1", confidence=0.5)
    inactive = _rule(active=False, confidence=0.99)
    expired = _rule(valid_to=datetime.now(UTC) - timedelta(days=1), confidence=0.95)
    other_scope = _rule(scope=PatternScope.OWNER, scope_id="own-1")
    for rule in (low, high, inactive, expired, other_scope):
        store.store(rule)
    results = store.get_active_rules(
        scenario="discount_request",
        scope=PatternScope.PROPERTY,
        scope_id="prop-1",
    )
    assert [r.pattern_id for r in results] == [high.pattern_id, low.pattern_id]


def test_deactivate_transition_semantics(store):
    rule = _rule()
    store.store(rule)
    assert store.deactivate(rule.pattern_id) is True
    assert store.deactivate(rule.pattern_id) is False
    assert store.deactivate("missing") is False
    loaded = store.get(rule.pattern_id)
    assert loaded.active is False
    assert loaded.deactivated_at is not None
    first_stamp = loaded.deactivated_at
    # re-storing as inactive must not move the original stamp (COALESCE)
    store.store(replace(loaded, deactivated_at=first_stamp))
    assert store.get(rule.pattern_id).deactivated_at == first_stamp


def test_update_delegates_to_upsert(store):
    rule = _rule()
    store.store(rule)
    store.update(replace(rule, counterexample_count=3))
    assert store.get(rule.pattern_id).counterexample_count == 3


def test_empty_tenant_rejected(session_maker):
    with pytest.raises(ValueError, match="tenant_id"):
        SQLAlchemyPatternRuleStore(session_maker=session_maker, tenant_id="")
