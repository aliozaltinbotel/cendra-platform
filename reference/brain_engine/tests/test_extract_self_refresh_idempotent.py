"""Regression tests — re-running pattern extraction must not deactivate
self-refreshed rules.

Mümin's 2026-05-08 round-4 feedback (#1) reported that
``GET /patterns/rules`` returned an empty list right after
``POST /patterns/extract`` succeeded with ``rules_extracted=2``, then
the rules eventually appeared "after about half an hour".  Live DB
inspection on the smoke property ``mumi-smoke-deny-001`` showed both
rules were stored at extract time but flipped to ``active=False`` ~3
minutes later — coinciding with a follow-up ``/patterns/extract`` call
on the same property.

Root cause: the contradiction resolver (`_resolve_pattern_rule_contradictions`)
treats any older rule with a different ``action_type`` and overlapping
condition slice as a world-shift candidate to deactivate.  When extract
emits *both* an approve and a deny rule over the same property scope,
the second extract run sees the prior active copies in
``existing_rules``, the resolver returns invalidated copies of the
sibling rules (same ``pattern_id`` as the freshly stored ones), and the
trailing UPSERT loop clobbers the row with ``active=False``.

The fix excludes any ``pattern_id`` that the *current* run is
re-emitting from the contradiction candidate pool: refreshes flow
through the existing UPSERT path instead of the invalidation path.

These tests pin the no-self-deactivation contract for both call sites
of the resolver:

1. ``OnboardingBootstrapPipeline._collect_invalidations`` — bootstrap
   path that the nightly miner uses.
2. The endpoint helper (replicated inline here) that
   ``/patterns/extract`` runs per request.
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
from brain_engine.patterns.pattern_miner import (
    _resolve_pattern_rule_contradictions,
)
from brain_engine.patterns.store import InMemoryPatternRuleStore

_PROPERTY = "test-property-self-refresh"
_T1 = datetime(2026, 5, 8, 13, 49, 27, tzinfo=UTC)
_T2 = _T1 + timedelta(minutes=4)


def _approve_rule(*, valid_from: datetime) -> PatternRule:
    return PatternRule(
        pattern_id="approve-fixed-pid",
        scenario=Scenario.EARLY_CHECKIN,
        scope=PatternScope.PROPERTY,
        scope_id=_PROPERTY,
        conditions={
            "stage": {"operator": "eq", "value": "pre_arrival"},
            "adults": {"operator": "gte", "value": 4.0},
        },
        action=DecisionAction(
            action_type=DecisionType.APPROVE,
            params={},
        ),
        support_count=6,
        counterexample_count=0,
        confidence=0.61,
        valid_from=valid_from,
    )


def _deny_rule(*, valid_from: datetime) -> PatternRule:
    return PatternRule(
        pattern_id="deny-fixed-pid",
        scenario=Scenario.EARLY_CHECKIN,
        scope=PatternScope.PROPERTY,
        scope_id=_PROPERTY,
        conditions={
            "stage": {"operator": "eq", "value": "pre_arrival"},
            "adults": {"operator": "gte", "value": 2.0},
        },
        action=DecisionAction(
            action_type=DecisionType.DENY,
            params={},
        ),
        support_count=8,
        counterexample_count=0,
        confidence=0.68,
        valid_from=valid_from,
    )


@pytest.mark.asyncio
async def test_bootstrap_collect_invalidations_skips_self_refresh() -> None:
    """Re-emitting the same ``pattern_id`` must not appear as a contradiction.

    Bootstrap stores the prior approve + deny rules at ``T1``; the
    second run produces the same rules with fresh ``valid_from=T2``
    timestamps.  Without the self-refresh filter the resolver would
    return invalidated copies of the *sibling* rule (approve→deactivate
    deny, deny→deactivate approve), and the caller's UPSERT loop would
    overwrite the freshly-stored rows with ``active=False``.
    """
    store = InMemoryPatternRuleStore()
    await store.store(_approve_rule(valid_from=_T1))
    await store.store(_deny_rule(valid_from=_T1))

    sentinel = cast(Any, object())
    pipeline = OnboardingBootstrapPipeline(
        archive_loader=sentinel,
        episode_builder=sentinel,
        case_extractor=sentinel,
        case_store=sentinel,
        pattern_miner=sentinel,
        rule_store=cast(Any, store),
    )

    refreshed = [
        _approve_rule(valid_from=_T2),
        _deny_rule(valid_from=_T2),
    ]
    invalidated = await pipeline._collect_invalidations(
        property_id=_PROPERTY,
        new_rules=refreshed,
    )

    assert invalidated == [], (
        "Self-refresh rules must not be returned as contradictions; "
        f"got {[r.pattern_id for r in invalidated]}"
    )


@pytest.mark.asyncio
async def test_extract_endpoint_filter_skips_self_refresh() -> None:
    """The endpoint-side filter must mirror the bootstrap behaviour.

    Replicates the inlined filter from
    :func:`brain_engine.api.pattern_endpoints.extract_patterns`: build
    ``contradiction_candidates`` from ``existing_rules`` minus any
    ``pattern_id`` that the current run is re-emitting, then call the
    resolver per new rule.  The expected outcome is *no* invalidations
    when the run is a pure refresh.
    """
    existing_rules = [
        _approve_rule(valid_from=_T1),
        _deny_rule(valid_from=_T1),
    ]
    refreshed = [
        _approve_rule(valid_from=_T2),
        _deny_rule(valid_from=_T2),
    ]
    new_pattern_ids = {rule.pattern_id for rule in refreshed}
    contradiction_candidates = [
        existing for existing in existing_rules
        if existing.pattern_id not in new_pattern_ids
    ]

    invalidated: list[PatternRule] = []
    for rule in refreshed:
        invalidated.extend(
            _resolve_pattern_rule_contradictions(
                rule, contradiction_candidates,
            ),
        )

    assert invalidated == [], (
        "Endpoint self-refresh filter must keep the contradiction "
        "candidate pool empty when every new rule replaces its prior "
        "copy."
    )


@pytest.mark.asyncio
async def test_genuine_cross_action_contradiction_still_invalidates() -> None:
    """Self-refresh filter must not mask legitimate world-shift cases.

    A truly older rule that is *not* being re-emitted in the current
    run keeps its place in the contradiction candidate pool and gets
    invalidated.  This guards against the filter over-reaching.
    """
    legacy_inform = PatternRule(
        pattern_id="legacy-inform-pid",
        scenario=Scenario.EARLY_CHECKIN,
        scope=PatternScope.PROPERTY,
        scope_id=_PROPERTY,
        conditions={
            "stage": {"operator": "eq", "value": "pre_arrival"},
            "adults": {"operator": "gte", "value": 2.0},
        },
        action=DecisionAction(
            action_type=DecisionType.INFORM,
            params={},
        ),
        support_count=10,
        confidence=0.7,
        valid_from=_T1 - timedelta(days=30),
    )
    refreshed = [_deny_rule(valid_from=_T2)]
    new_pattern_ids = {rule.pattern_id for rule in refreshed}
    contradiction_candidates = [
        existing for existing in [legacy_inform]
        if existing.pattern_id not in new_pattern_ids
    ]

    invalidated: list[PatternRule] = []
    for rule in refreshed:
        invalidated.extend(
            _resolve_pattern_rule_contradictions(
                rule, contradiction_candidates,
            ),
        )

    assert len(invalidated) == 1
    closed = invalidated[0]
    assert closed.pattern_id == "legacy-inform-pid"
    assert closed.active is False
    assert closed.invalid_at == _T2
