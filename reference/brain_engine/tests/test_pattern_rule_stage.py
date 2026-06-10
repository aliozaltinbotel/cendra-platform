"""Sprint 6 — top-level ``stage`` field on :class:`PatternRule`.

The miner derives ``stage`` from the supporting cases as the
strict-majority booking stage so explainability surfaces ("this rule
fires only at PRE_ARRIVAL") do not have to recover the answer by
scanning rule conditions.  ``stage`` is observed metadata, not a
rule identity field — it must stay out of
:meth:`PatternRule.deterministic_id` so that re-mining cannot orphan
rows when the dominant stage shifts (e.g. seasonal traffic moves
support from PRE_ARRIVAL into IN_STAY).

These tests pin:

1. ``_dominant_stage`` strict-majority semantics (top, tie, empty,
   single-case) — shared between extractor + miner via the helper
   in :mod:`brain_engine.patterns.extractor`.
2. ``PatternRule.stage`` defaults to ``None`` so existing
   constructors (8 sites at write time) keep working untouched.
3. The miner's ``_build_rule`` and ``_build_conditional_rule``
   wire the helper through into both unconditional and conditional
   rules.
4. ``deterministic_id`` is invariant to stage so re-mining over the
   same evidence produces the same ``pattern_id`` regardless of
   which stage the cases predominantly land in.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain_engine.patterns.extractor import _dominant_stage
from brain_engine.patterns.models import (
    BookingStage,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.pattern_miner import PatternMiner


def _make_case(
    *,
    stage: BookingStage = BookingStage.IN_STAY,
    scenario: Scenario = Scenario.ACCESS_CODE_RELEASE,
    action: DecisionType = DecisionType.INFORM,
    property_id: str = "prop-stage",
) -> DecisionCase:
    return DecisionCase(
        stage=stage,
        scenario=scenario,
        property_id=property_id,
        owner_id="owner-stage",
        decision=DecisionAction(action_type=action, params={}),
        source=CaseSource.LIVE,
        extracted_entities={},
        created_at=datetime(2026, 5, 4, tzinfo=UTC),
    )


@pytest.fixture
def miner() -> PatternMiner:
    return PatternMiner(
        min_support=3,
        min_confidence=0.4,
        require_outcome=False,
    )


# ---------------------------------------------------------------------------
# _dominant_stage — strict-majority semantics
# ---------------------------------------------------------------------------


def test_dominant_stage_returns_strict_majority() -> None:
    cases = [
        _make_case(stage=BookingStage.PRE_ARRIVAL),
        _make_case(stage=BookingStage.PRE_ARRIVAL),
        _make_case(stage=BookingStage.PRE_ARRIVAL),
        _make_case(stage=BookingStage.IN_STAY),
        _make_case(stage=BookingStage.IN_STAY),
    ]
    assert _dominant_stage(cases) is BookingStage.PRE_ARRIVAL


def test_dominant_stage_returns_none_on_tie() -> None:
    cases = [
        _make_case(stage=BookingStage.PRE_ARRIVAL),
        _make_case(stage=BookingStage.PRE_ARRIVAL),
        _make_case(stage=BookingStage.IN_STAY),
        _make_case(stage=BookingStage.IN_STAY),
    ]
    assert _dominant_stage(cases) is None


def test_dominant_stage_returns_none_on_empty() -> None:
    assert _dominant_stage(()) is None


def test_dominant_stage_returns_only_stage_when_single() -> None:
    assert _dominant_stage(
        [_make_case(stage=BookingStage.CHECKOUT)],
    ) is BookingStage.CHECKOUT


# ---------------------------------------------------------------------------
# Dataclass default + identity invariant
# ---------------------------------------------------------------------------


def test_pattern_rule_stage_defaults_to_none() -> None:
    rule = PatternRule(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        scope=PatternScope.PROPERTY,
        scope_id="prop-stage",
        conditions={},
        action=DecisionAction(
            action_type=DecisionType.INFORM, params={},
        ),
    )
    assert rule.stage is None


def test_deterministic_id_is_invariant_to_stage() -> None:
    """Stage must not enter the identity hash — see Sprint 6 design note.

    If stage participated in :meth:`deterministic_id`, every
    re-bootstrap whose evidence shifted the dominant stage from
    PRE_ARRIVAL to IN_STAY would produce a fresh ``pattern_id``,
    leaving the original row as an orphan exactly like the bug
    Mümin found in 3c2b943.
    """
    pid_a = PatternRule.deterministic_id(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        scope=PatternScope.PROPERTY,
        scope_id="prop-stage",
        action_type=DecisionType.INFORM,
        conditions={},
    )
    pid_b = PatternRule.deterministic_id(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        scope=PatternScope.PROPERTY,
        scope_id="prop-stage",
        action_type=DecisionType.INFORM,
        conditions={},
    )
    assert pid_a == pid_b


# ---------------------------------------------------------------------------
# Miner wiring — _build_rule + _build_conditional_rule populate stage
# ---------------------------------------------------------------------------


def test_miner_builds_rule_with_dominant_stage(
    miner: PatternMiner,
) -> None:
    cases = [
        _make_case(stage=BookingStage.PRE_ARRIVAL) for _ in range(4)
    ] + [_make_case(stage=BookingStage.IN_STAY)]

    rules, _ = miner.mine(cases)

    assert len(rules) == 1
    assert rules[0].stage is BookingStage.PRE_ARRIVAL


def test_miner_emits_none_stage_when_evidence_splits(
    miner: PatternMiner,
) -> None:
    cases = [
        _make_case(stage=BookingStage.PRE_ARRIVAL),
        _make_case(stage=BookingStage.PRE_ARRIVAL),
        _make_case(stage=BookingStage.IN_STAY),
        _make_case(stage=BookingStage.IN_STAY),
    ]

    rules, _ = miner.mine(cases)

    assert len(rules) == 1
    assert rules[0].stage is None
