"""Behaviour of the in-memory DecisionCase / PatternRule stores.

Written at port time (the reference exercises the stores only through
integration tests).  These pin the Protocol semantics the SQLAlchemy
stores must mirror: AND-combined search filters, newest-first
pagination, soft-archive idempotence, and active-rule filtering.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.brain.patterns.models import (
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    PatternRule,
    PatternScope,
)
from core.brain.patterns.store import (
    InMemoryDecisionCaseStore,
    InMemoryPatternRuleStore,
)


def _case(**overrides) -> DecisionCase:
    base = {
        "stage": "in_stay",
        "scenario": "noise_complaint",
        "property_id": "prop-1",
        "owner_id": "own-1",
        "decision": DecisionAction(action_type=DecisionType.INFORM),
    }
    base.update(overrides)
    return DecisionCase(**base)


def _rule(**overrides) -> PatternRule:
    base = {
        "scenario": "discount_request",
        "scope": PatternScope.PROPERTY,
        "scope_id": "prop-1",
        "conditions": {},
        "action": DecisionAction(action_type=DecisionType.APPROVE),
    }
    base.update(overrides)
    return PatternRule(**base)


class TestCaseStore:
    def test_store_and_get(self):
        store = InMemoryDecisionCaseStore()
        case = _case()
        assert store.store(case) == case.case_id
        assert store.get(case.case_id) == case
        assert store.get("missing") is None

    def test_search_filters_are_and_combined(self):
        store = InMemoryDecisionCaseStore()
        match = _case()
        store.store(match)
        store.store(_case(scenario="late_checkout"))
        store.store(_case(property_id="prop-2"))
        store.store(_case(stage="checkout"))
        results = store.search(
            scenario="noise_complaint",
            property_id="prop-1",
            stage="in_stay",
        )
        assert results == [match]

    def test_search_by_source_event_id(self):
        store = InMemoryDecisionCaseStore()
        tagged = _case(origin=PatternOrigin(source_event_ids=("ev-7",)))
        store.store(tagged)
        store.store(_case())
        assert store.search(source_event_id="ev-7") == [tagged]
        assert store.search(source_event_id="ev-8") == []

    def test_search_paginates_newest_first(self):
        store = InMemoryDecisionCaseStore()
        t0 = datetime.now(UTC)
        cases = [_case(created_at=t0 + timedelta(seconds=i)) for i in range(5)]
        for case in cases:
            store.store(case)
        page1 = store.search(limit=2)
        page2 = store.search(limit=2, offset=2)
        assert [c.case_id for c in page1] == [cases[4].case_id, cases[3].case_id]
        assert [c.case_id for c in page2] == [cases[2].case_id, cases[1].case_id]

    def test_archive_is_idempotent_and_hides_from_search(self):
        store = InMemoryDecisionCaseStore()
        case = _case()
        store.store(case)
        assert store.archive(case.case_id) is True
        assert store.archive(case.case_id) is False
        assert store.archive("missing") is False
        assert store.search() == []
        assert len(store.search(include_archived=True)) == 1
        assert store.count() == 0

    def test_get_by_reservation_sorted_oldest_first(self):
        store = InMemoryDecisionCaseStore()
        t0 = datetime.now(UTC)
        newer = _case(reservation_id="res-1", created_at=t0 + timedelta(seconds=1))
        older = _case(reservation_id="res-1", created_at=t0)
        store.store(newer)
        store.store(older)
        store.store(_case(reservation_id="res-2"))
        results = store.get_by_reservation("res-1")
        assert [c.case_id for c in results] == [older.case_id, newer.case_id]

    def test_select_archive_candidates_oldest_first_with_cutoff(self):
        store = InMemoryDecisionCaseStore()
        t0 = datetime.now(UTC)
        old1 = _case(created_at=t0 - timedelta(days=120))
        old2 = _case(created_at=t0 - timedelta(days=100))
        fresh = _case(created_at=t0)
        for case in (fresh, old2, old1):
            store.store(case)
        cutoff = t0 - timedelta(days=90)
        assert store.select_archive_candidates(cutoff=cutoff) == [
            old1.case_id,
            old2.case_id,
        ]
        assert store.select_archive_candidates(cutoff=cutoff, limit=1) == [old1.case_id]


class TestRuleStore:
    def test_store_get_update(self):
        store = InMemoryPatternRuleStore()
        rule = _rule()
        assert store.store(rule) == rule.pattern_id
        assert store.get(rule.pattern_id) == rule
        from dataclasses import replace

        refreshed = replace(rule, confidence=0.9)
        store.update(refreshed)
        assert store.get(rule.pattern_id).confidence == 0.9

    def test_get_active_rules_filters_and_sorts(self):
        store = InMemoryPatternRuleStore()
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

    def test_deactivate(self):
        store = InMemoryPatternRuleStore()
        rule = _rule()
        store.store(rule)
        assert store.deactivate(rule.pattern_id) is True
        assert store.get(rule.pattern_id).active is False
        assert store.deactivate("missing") is False
        assert store.get_active_rules() == []
