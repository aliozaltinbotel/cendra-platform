"""Tests for the bi-temporal contradiction resolver (Sprint 1).

Direct port of Graphiti's ``resolve_edge_contradictions``
(``graphiti_core/utils/maintenance/edge_operations.py:537-572``,
arXiv 2501.13956 §3.2) adapted to PatternRule's structured
identity tuple — **no LLM is involved**.

These tests pin the contracts the resolver offers callers:

1. **Empty input** — empty candidate list returns empty result.
2. **Same identity, different action** — older candidate is
   invalidated with ``invalid_at`` = new ``valid_from`` and
   ``deactivated_at`` = "now".
3. **Same action_type** — never a contradiction; updates flow
   through the existing UPSERT path instead.
4. **Disjoint condition slices** — temporal split (Mümin's
   "5 days vs 2 days" example, ali.md §3) is NOT a contradiction
   because the condition intervals don't overlap.
5. **Already deactivated** — idempotent: re-running on a closed
   rule emits nothing.
6. **Cross-scope / cross-scenario** — never invalidated even if
   conditions look similar (defensive against API misuse).
7. **Frozen dataclass** — original input is never mutated;
   resolver returns ``dataclasses.replace`` copies.
8. **Newer candidate** — when candidate's ``valid_from`` is
   *later* than the new rule, the new rule is "older from the
   candidate's POV" so we don't invalidate (Graphiti algorithm
   only invalidates strictly older candidates).

Plus router point-in-time filter contracts:

9. **Default behaviour preserved** — ``as_of=None`` keeps
   pre-Sprint-1 logic (only ``deactivated_at IS NULL`` filter).
10. **Point-in-time** — when ``as_of`` is supplied, rules whose
    ``invalid_at <= as_of`` or ``deactivated_at <= as_of`` are
    filtered out so historical reservations see the rule that
    was active at that moment.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.brain.patterns.models import (
    DecisionAction,
    DecisionType,
    PatternRule,
    PatternScope,
)
from core.brain.patterns.pattern_miner import (
    _conditions_overlap,
    _resolve_pattern_rule_contradictions,
)
from core.brain.patterns.router import PatternRuleRouter, _is_valid_at

_NOW = datetime(2026, 5, 5, tzinfo=UTC)


def _rule(
    *,
    pattern_id: str,
    action: DecisionType,
    valid_from: datetime,
    conditions: dict[str, Any] | None = None,
    scope: PatternScope = PatternScope.PROPERTY,
    scope_id: str = "p1",
    scenario: Scenario = "access_code_release",
    invalid_at: datetime | None = None,
    deactivated_at: datetime | None = None,
    active: bool = True,
) -> PatternRule:
    """Build a PatternRule fixture with sensible defaults."""
    return PatternRule(
        pattern_id=pattern_id,
        scenario=scenario,
        scope=scope,
        scope_id=scope_id,
        conditions=conditions or {},
        action=DecisionAction(action_type=action, params={}),
        valid_from=valid_from,
        invalid_at=invalid_at,
        deactivated_at=deactivated_at,
        active=active,
    )


# ---------------------------------------------------------------------------
# resolve_pattern_rule_contradictions
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_empty() -> None:
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    assert _resolve_pattern_rule_contradictions(new, []) == []


def test_older_contradicting_candidate_is_invalidated() -> None:
    old = _rule(
        pattern_id="OLD",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    result = _resolve_pattern_rule_contradictions(new, [old])
    assert len(result) == 1
    closed = result[0]
    assert closed.pattern_id == "OLD"
    assert closed.invalid_at == _NOW
    assert closed.deactivated_at is not None
    assert closed.active is False


def test_same_action_type_is_not_contradiction() -> None:
    twin = _rule(
        pattern_id="TWIN",
        action=DecisionType.DENY,
        valid_from=_NOW - timedelta(days=30),
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    assert _resolve_pattern_rule_contradictions(new, [twin]) == []


def test_temporal_split_is_not_contradiction() -> None:
    # ali.md §3 / Mümin's question: "5 days defer, 2 days inform"
    # → two rules with disjoint lead_time_hours intervals must
    # co-exist.
    defer_rule = _rule(
        pattern_id="DEFER",
        action=DecisionType.DEFER,
        valid_from=_NOW - timedelta(days=10),
        conditions={
            "lead_time_hours": {"value": 120, "operator": "gte"},
        },
    )
    inform_rule = _rule(
        pattern_id="INFORM",
        action=DecisionType.INFORM,
        valid_from=_NOW,
        conditions={
            "lead_time_hours": {"value": 48, "operator": "lte"},
        },
    )
    assert (
        _resolve_pattern_rule_contradictions(
            inform_rule,
            [defer_rule],
        )
        == []
    )


def test_already_deactivated_candidate_is_skipped() -> None:
    closed = _rule(
        pattern_id="CLOSED",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
        invalid_at=_NOW - timedelta(days=5),
        deactivated_at=_NOW - timedelta(days=5),
        active=False,
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    assert _resolve_pattern_rule_contradictions(new, [closed]) == []


def test_cross_scope_never_contradicts() -> None:
    other_property = _rule(
        pattern_id="OTHER",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
        scope_id="p2",
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
        scope_id="p1",
    )
    assert (
        _resolve_pattern_rule_contradictions(
            new,
            [other_property],
        )
        == []
    )


def test_cross_scenario_never_contradicts() -> None:
    other_scenario = _rule(
        pattern_id="OTHER",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
        scenario="late_checkout",
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
        scenario="access_code_release",
    )
    assert (
        _resolve_pattern_rule_contradictions(
            new,
            [other_scenario],
        )
        == []
    )


def test_frozen_input_is_never_mutated() -> None:
    old = _rule(
        pattern_id="OLD",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    _resolve_pattern_rule_contradictions(new, [old])
    # Original frozen dataclass remains untouched — only the
    # returned ``dataclasses.replace`` copy carries the
    # invalid_at / deactivated_at.
    assert old.active is True
    assert old.invalid_at is None
    assert old.deactivated_at is None


def test_newer_candidate_is_not_invalidated() -> None:
    # If the candidate started LATER than the new rule, Graphiti's
    # algorithm leaves it alone — only strictly-older candidates
    # are closed.  This protects out-of-order ingestion: a newly
    # mined "old" rule must not invalidate a previously mined
    # "new" rule that already represents a later world.
    later_candidate = _rule(
        pattern_id="LATER",
        action=DecisionType.APPROVE,
        valid_from=_NOW + timedelta(days=10),
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    assert (
        _resolve_pattern_rule_contradictions(
            new,
            [later_candidate],
        )
        == []
    )


def test_self_reference_is_skipped() -> None:
    same_id = _rule(
        pattern_id="SAME",
        action=DecisionType.DENY,
        valid_from=_NOW - timedelta(days=30),
    )
    new = _rule(
        pattern_id="SAME",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    assert _resolve_pattern_rule_contradictions(new, [same_id]) == []


def test_idempotent_re_invocation() -> None:
    old = _rule(
        pattern_id="OLD",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    first = _resolve_pattern_rule_contradictions(new, [old])
    # Re-running with the just-invalidated rule emits nothing —
    # already deactivated, skipped per
    # ``test_already_deactivated_candidate_is_skipped``.
    second = _resolve_pattern_rule_contradictions(new, first)
    assert second == []


# ---------------------------------------------------------------------------
# _conditions_overlap helper — the deterministic substitute for
# Graphiti's LLM contradicted_facts list.
# ---------------------------------------------------------------------------


def test_empty_conditions_overlap_anything() -> None:
    assert _conditions_overlap({}, {}) is True
    assert _conditions_overlap({}, {"x": 1}) is True
    assert _conditions_overlap({"x": 1}, {}) is True


def test_disjoint_eq_does_not_overlap() -> None:
    a = {"channel": {"operator": "eq", "value": "bookingcom"}}
    b = {"channel": {"operator": "eq", "value": "airbnb"}}
    assert _conditions_overlap(a, b) is False


def test_disjoint_gte_lte_does_not_overlap() -> None:
    a = {"total_price": {"operator": "gte", "value": 100}}
    b = {"total_price": {"operator": "lte", "value": 50}}
    assert _conditions_overlap(a, b) is False
    assert _conditions_overlap(b, a) is False


def test_subsuming_conditions_overlap() -> None:
    broader = {"total_price": {"operator": "gte", "value": 26}}
    narrower = {"total_price": {"operator": "gte", "value": 100}}
    assert _conditions_overlap(broader, narrower) is True


# ---------------------------------------------------------------------------
# Router point-in-time filter
# ---------------------------------------------------------------------------


class _StubStore:
    """Minimal ``PatternRuleStore`` stand-in for router tests."""

    def __init__(self, rules: list[PatternRule]) -> None:
        self._rules = list(rules)

    def store(self, rule: PatternRule) -> str:
        self._rules.append(rule)
        return rule.pattern_id

    def get_active_rules(
        self,
        *,
        scenario: Scenario | None = None,
        scope: PatternScope | None = None,
        scope_id: str | None = None,
    ) -> list[PatternRule]:
        return [
            r
            for r in self._rules
            if (scenario is None or r.scenario is scenario)
            and (scope is None or r.scope is scope)
            and (scope_id is None or r.scope_id == scope_id)
        ]


def test_is_valid_at_no_anchor_uses_legacy_check() -> None:
    active = _rule(
        pattern_id="A",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
    )
    closed = _rule(
        pattern_id="C",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
        deactivated_at=_NOW - timedelta(days=1),
        active=False,
    )
    assert _is_valid_at(active, None) is True
    assert _is_valid_at(closed, None) is False


def test_is_valid_at_anchor_filters_invalidated_after_window() -> None:
    closed_after = _rule(
        pattern_id="C",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
        invalid_at=_NOW,
        deactivated_at=_NOW,
        active=False,
    )
    earlier = _NOW - timedelta(days=10)
    # Anchor BEFORE the rule was invalidated → still valid.
    assert _is_valid_at(closed_after, earlier) is True
    # Anchor AFTER invalidation → filtered out.
    assert _is_valid_at(closed_after, _NOW) is False


def test_is_valid_at_anchor_filters_pre_valid_from() -> None:
    rule = _rule(
        pattern_id="R",
        action=DecisionType.APPROVE,
        valid_from=_NOW,
    )
    too_early = _NOW - timedelta(days=5)
    assert _is_valid_at(rule, too_early) is False


def test_router_match_default_anchor() -> None:
    rule = _rule(
        pattern_id="R",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
    )
    router = PatternRuleRouter(_StubStore([rule]))
    match = router.match(
        scenario="access_code_release",
        property_id="p1",
        features={},
    )
    assert match is not None
    assert match.rule.pattern_id == "R"


def test_router_match_point_in_time_filter() -> None:
    closed = _rule(
        pattern_id="OLD",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
        invalid_at=_NOW - timedelta(days=5),
        deactivated_at=_NOW - timedelta(days=5),
        active=False,
    )
    new_rule = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW - timedelta(days=5),
    )
    router = PatternRuleRouter(_StubStore([closed, new_rule]))
    # Reservation date BEFORE the regime change → old rule applies.
    earlier = _NOW - timedelta(days=15)
    match = router.match(
        scenario="access_code_release",
        property_id="p1",
        features={},
        as_of=earlier,
    )
    assert match is not None
    assert match.rule.pattern_id == "OLD"
    # Reservation date AFTER regime change → new rule applies.
    match2 = router.match(
        scenario="access_code_release",
        property_id="p1",
        features={},
        as_of=_NOW,
    )
    assert match2 is not None
    assert match2.rule.pattern_id == "NEW"
