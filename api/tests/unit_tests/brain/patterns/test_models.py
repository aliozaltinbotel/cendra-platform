"""Value-object behaviour of the patterns models.

Written at port time: the reference exercises these paths only through
miner/extractor integration tests; this suite pins the pure logic —
condition evaluation, deterministic ids, promotion criteria, outcome
signals, and the provenance trail — plus the genericised str scenario /
stage contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.brain.patterns.models import (
    CONFIDENCE_AUTO_THRESHOLD,
    MIN_SUPPORT_AUTO,
    SCENARIO_GENERAL,
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    PatternRule,
    PatternScope,
    ResolutionType,
    RiskLevel,
)


def _action(kind: DecisionType = DecisionType.APPROVE) -> DecisionAction:
    return DecisionAction(action_type=kind)


def _rule(**overrides) -> PatternRule:
    base = {
        "scenario": "discount_request",
        "scope": PatternScope.PROPERTY,
        "scope_id": "prop-1",
        "conditions": {"nights": {"operator": "gte", "value": 5}},
        "action": _action(),
    }
    base.update(overrides)
    return PatternRule(**base)


class TestConditionEvaluation:
    def test_all_operators(self):
        rule = _rule(
            conditions={
                "nights": {"operator": "gte", "value": 5},
                "guests": {"operator": "lt", "value": 4},
                "channel": {"operator": "in", "value": ["airbnb", "direct"]},
                "status": {"operator": "neq", "value": "cancelled"},
                "note": {"operator": "contains", "value": "vip"},
            }
        )
        assert rule.matches_conditions(
            {
                "nights": 6,
                "guests": 2,
                "channel": "direct",
                "status": "confirmed",
                "note": "a vip guest",
            }
        )

    def test_missing_feature_fails(self):
        rule = _rule()
        assert not rule.matches_conditions({})

    def test_default_operator_is_eq(self):
        rule = _rule(conditions={"x": {"value": 3}})
        assert rule.matches_conditions({"x": 3})
        assert not rule.matches_conditions({"x": 4})

    def test_unknown_operator_fails_closed(self):
        rule = _rule(conditions={"x": {"operator": "regex", "value": ".*"}})
        assert not rule.matches_conditions({"x": "anything"})


class TestDeterministicId:
    def test_stable_across_observed_fields(self):
        kwargs = {
            "scenario": "discount_request",
            "scope": PatternScope.PROPERTY,
            "scope_id": "prop-1",
            "action_type": DecisionType.APPROVE,
            "conditions": {"nights": {"operator": "gte", "value": 5}},
        }
        a = PatternRule.deterministic_id(**kwargs)
        b = PatternRule.deterministic_id(**kwargs)
        assert a == b
        assert len(a) == 32

    def test_identity_fields_change_id(self):
        base = {
            "scenario": "discount_request",
            "scope": PatternScope.PROPERTY,
            "scope_id": "prop-1",
            "action_type": DecisionType.APPROVE,
            "conditions": {},
        }
        a = PatternRule.deterministic_id(**base)
        b = PatternRule.deterministic_id(**{**base, "scenario": "late_checkout"})
        c = PatternRule.deterministic_id(**{**base, "action_type": DecisionType.DENY})
        assert len({a, b, c}) == 3


class TestPromotion:
    def test_promotable_when_all_criteria_met(self):
        rule = _rule(
            support_count=MIN_SUPPORT_AUTO,
            confidence=CONFIDENCE_AUTO_THRESHOLD,
            counterexample_count=0,
            risk_level=RiskLevel.LOW,
        )
        assert rule.is_promotable

    def test_critical_risk_never_promotable(self):
        rule = _rule(
            support_count=100,
            confidence=0.99,
            risk_level=RiskLevel.CRITICAL,
        )
        assert not rule.is_promotable

    def test_counterexample_ratio(self):
        rule = _rule(support_count=8, counterexample_count=2)
        assert rule.total_cases == 10
        assert rule.counterexample_ratio == 0.2
        assert _rule().counterexample_ratio == 0.0

    def test_expiry(self):
        assert not _rule(valid_to=None).is_expired
        past = datetime.now(UTC) - timedelta(days=1)
        assert _rule(valid_to=past).is_expired


class TestCaseOutcome:
    def test_deliberate_decisions_collapse_to_pm_approved(self):
        for kind in (
            DecisionType.APPROVE,
            DecisionType.CHARGE,
            DecisionType.OFFER,
            DecisionType.RELEASE,
            DecisionType.DENY,
            DecisionType.BLOCK,
        ):
            outcome = CaseOutcome.from_decision_type(kind)
            assert outcome.resolution_type is ResolutionType.PM_APPROVED
            assert outcome.successful is True
            assert outcome.is_positive_signal

    def test_escalate_is_negative(self):
        outcome = CaseOutcome.from_decision_type(DecisionType.ESCALATE)
        assert outcome.resolution_type is ResolutionType.ESCALATED
        assert outcome.successful is False
        assert outcome.is_negative_signal

    def test_conversational_decisions_auto_resolve(self):
        outcome = CaseOutcome.from_decision_type(DecisionType.INFORM)
        assert outcome.resolution_type is ResolutionType.AUTO_RESOLVED
        assert outcome.is_positive_signal

    def test_override_dominates_signals(self):
        outcome = CaseOutcome(human_overrode=True, successful=True)
        assert not outcome.is_positive_signal
        assert outcome.is_negative_signal


class TestPatternOrigin:
    def test_round_trip(self):
        origin = PatternOrigin(
            foundation_scenario_ids=("fs-1", "fs-2"),
            source_event_ids=("ev-1",),
        )
        rebuilt = PatternOrigin.from_jsonable(origin.to_jsonable())
        assert rebuilt == origin

    def test_empty_origin_serialises_to_empty_dict(self):
        origin = PatternOrigin()
        assert origin.is_empty()
        assert origin.to_jsonable() == {}
        assert PatternOrigin.from_jsonable({}) == origin

    def test_malformed_payload_degrades_to_empty(self):
        assert PatternOrigin.from_jsonable(None) == PatternOrigin()
        assert PatternOrigin.from_jsonable({"source_event_ids": 42}) == PatternOrigin()


class TestDecisionCase:
    def _case(self, **overrides) -> DecisionCase:
        base = {
            "stage": "in_stay",
            "scenario": "noise_complaint",
            "property_id": "prop-1",
            "owner_id": "own-1",
            "decision": _action(),
        }
        base.update(overrides)
        return DecisionCase(**base)

    def test_scenario_and_stage_are_opaque_strings(self):
        case = self._case(stage="any_vertical_stage", scenario="any_vertical_scenario")
        assert case.stage == "any_vertical_stage"
        assert case.scenario == "any_vertical_scenario"

    def test_learnable_requires_outcome_and_classification(self):
        assert not self._case().is_learnable  # no outcome
        with_outcome = self._case(outcome=CaseOutcome.from_decision_type(DecisionType.APPROVE))
        assert with_outcome.is_learnable
        general = self._case(
            scenario=SCENARIO_GENERAL,
            outcome=CaseOutcome.from_decision_type(DecisionType.APPROVE),
        )
        assert not general.is_learnable
