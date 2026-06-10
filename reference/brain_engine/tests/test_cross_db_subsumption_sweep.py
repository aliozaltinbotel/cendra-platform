"""Regression tests — cross-DB subsumption sweep deactivates stale rules.

Mümin's 2026-05-08 round-4 #5a complaint: two ``early_checkin/inform``
rules survived in ``GET /patterns/rules`` on property ``218126`` even
though one strictly dominated the other (``adults gte 1.5`` covers
``adults gte 2.0``; ``hours_before_checkin gte -1185.65`` covers
``-381.66``).  Per-batch ``_merge_subsumed_rules`` only sees rules
emitted by a *single* extract or bootstrap run, so a narrower rule
left over from an earlier run kept its active row even when a later
run produced a strictly broader sibling.

The fix sweeps active rules across each touched
``(scope, scope_id, scenario)`` bucket after the persistence loop has
completed — ``_merge_subsumed_rules`` decides which rules survive,
``PatternRuleStore.deactivate`` closes the rest.

These tests pin the contract for the bootstrap path (the endpoint
path mirrors the same helper inline; covered by the post-deploy live
smoke check):

1. **Stale narrower sibling** — broader rule from this run subsumes a
   pre-existing narrower rule with the same action; sweep deactivates
   the narrower row and leaves the broader one active.
2. **Disjoint siblings** — same scenario / action / scope but
   conditions don't overlap (``adults gte 4`` vs ``adults lte 2``).
   Sweep keeps both active because neither covers the other.
3. **Cross-action sibling** — same scenario / scope / conditions but
   different ``action_type``. Sweep treats them as independent because
   ``_merge_subsumed_rules`` only collapses within an action group.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
)
from brain_engine.patterns.models import (
    DecisionAction,
    DecisionType,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.store import InMemoryPatternRuleStore

_PROPERTY = "test-prop-cross-db-sub"
_T1 = datetime(2026, 5, 8, 13, 0, tzinfo=UTC)
_T2 = _T1 + timedelta(hours=1)


def _rule(
    *,
    pattern_id: str,
    action: DecisionType,
    conditions: dict[str, Any],
    valid_from: datetime,
    support: int = 10,
    scenario: Scenario = Scenario.EARLY_CHECKIN,
    scope: PatternScope = PatternScope.PROPERTY,
    scope_id: str = _PROPERTY,
) -> PatternRule:
    return PatternRule(
        pattern_id=pattern_id,
        scenario=scenario,
        scope=scope,
        scope_id=scope_id,
        conditions=conditions,
        action=DecisionAction(action_type=action, params={}),
        support_count=support,
        confidence=0.7,
        valid_from=valid_from,
    )


def _pipeline(rule_store: InMemoryPatternRuleStore) -> OnboardingBootstrapPipeline:
    sentinel = cast(Any, object())
    return OnboardingBootstrapPipeline(
        archive_loader=sentinel,
        episode_builder=sentinel,
        case_extractor=sentinel,
        case_store=sentinel,
        pattern_miner=sentinel,
        rule_store=cast(Any, rule_store),
    )


@pytest.mark.asyncio
async def test_sweep_deactivates_stale_narrower_sibling() -> None:
    """Broader sibling from current run dominates an older narrower row."""
    store = InMemoryPatternRuleStore()
    narrow = _rule(
        pattern_id="narrow-prior",
        action=DecisionType.INFORM,
        conditions={
            "adults": {"operator": "gte", "value": 2.0},
            "hours_before_checkin": {"operator": "gte", "value": -381.66},
        },
        valid_from=_T1,
        support=20,
    )
    broader = _rule(
        pattern_id="broader-current",
        action=DecisionType.INFORM,
        conditions={
            "adults": {"operator": "gte", "value": 1.5},
            "hours_before_checkin": {"operator": "gte", "value": -1185.65},
        },
        valid_from=_T2,
        support=21,
    )
    await store.store(narrow)
    await store.store(broader)

    pipeline = _pipeline(store)
    await pipeline._sweep_subsumed_actives(
        property_id=_PROPERTY,
        buckets={(PatternScope.PROPERTY, _PROPERTY, Scenario.EARLY_CHECKIN)},
    )

    actives = await store.get_active_rules(
        scenario=Scenario.EARLY_CHECKIN,
        scope=PatternScope.PROPERTY,
        scope_id=_PROPERTY,
    )
    active_ids = {r.pattern_id for r in actives}
    assert active_ids == {"broader-current"}, (
        f"sweep should deactivate narrow-prior; remaining actives = {active_ids}"
    )


@pytest.mark.asyncio
async def test_sweep_keeps_disjoint_siblings() -> None:
    """Disjoint condition slices are not in a subsumption relationship."""
    store = InMemoryPatternRuleStore()
    big_party = _rule(
        pattern_id="big-party",
        action=DecisionType.INFORM,
        conditions={"adults": {"operator": "gte", "value": 4.0}},
        valid_from=_T1,
    )
    small_party = _rule(
        pattern_id="small-party",
        action=DecisionType.INFORM,
        conditions={"adults": {"operator": "lte", "value": 2.0}},
        valid_from=_T2,
    )
    await store.store(big_party)
    await store.store(small_party)

    pipeline = _pipeline(store)
    await pipeline._sweep_subsumed_actives(
        property_id=_PROPERTY,
        buckets={(PatternScope.PROPERTY, _PROPERTY, Scenario.EARLY_CHECKIN)},
    )

    actives = await store.get_active_rules(
        scenario=Scenario.EARLY_CHECKIN,
        scope=PatternScope.PROPERTY,
        scope_id=_PROPERTY,
    )
    assert {r.pattern_id for r in actives} == {"big-party", "small-party"}


@pytest.mark.asyncio
async def test_sweep_does_not_collapse_across_action_types() -> None:
    """Different action types are not siblings; both survive the sweep."""
    store = InMemoryPatternRuleStore()
    approve_rule = _rule(
        pattern_id="approve-rule",
        action=DecisionType.APPROVE,
        conditions={"adults": {"operator": "gte", "value": 4.0}},
        valid_from=_T1,
    )
    deny_rule = _rule(
        pattern_id="deny-rule",
        action=DecisionType.DENY,
        conditions={"adults": {"operator": "gte", "value": 2.0}},
        valid_from=_T1,
    )
    await store.store(approve_rule)
    await store.store(deny_rule)

    pipeline = _pipeline(store)
    await pipeline._sweep_subsumed_actives(
        property_id=_PROPERTY,
        buckets={(PatternScope.PROPERTY, _PROPERTY, Scenario.EARLY_CHECKIN)},
    )

    actives = await store.get_active_rules(
        scenario=Scenario.EARLY_CHECKIN,
        scope=PatternScope.PROPERTY,
        scope_id=_PROPERTY,
    )
    assert {r.pattern_id for r in actives} == {"approve-rule", "deny-rule"}


@pytest.mark.asyncio
async def test_sweep_skips_when_store_lacks_capabilities() -> None:
    """Stores without ``deactivate`` / ``get_active_rules`` no-op cleanly."""

    class _StubStore:
        async def store(self, rule: PatternRule) -> str:
            return rule.pattern_id

    store = _StubStore()
    pipeline = _pipeline(cast(Any, store))
    # Should not raise even though the store can't fulfil the sweep.
    await pipeline._sweep_subsumed_actives(
        property_id=_PROPERTY,
        buckets={(PatternScope.PROPERTY, _PROPERTY, Scenario.EARLY_CHECKIN)},
    )
