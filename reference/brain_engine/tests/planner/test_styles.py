"""Built-in :class:`PlannerStyleSpec` invariants."""

from __future__ import annotations

import pytest

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.cards.models import ReversibilityTier
from brain_engine.planner.styles import (
    BUILTIN_STYLE_SPECS,
    PlannerStyleId,
)


def test_every_style_id_has_a_builtin_spec() -> None:
    """Every :class:`PlannerStyleId` value has a built-in spec."""
    assert set(BUILTIN_STYLE_SPECS.keys()) == set(PlannerStyleId)
    assert len(BUILTIN_STYLE_SPECS) == len(PlannerStyleId)


def test_compliance_strict_blocks_financial_actions() -> None:
    """Regulatory style denies financial / code-release moves."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.COMPLIANCE_STRICT]
    assert spec.forbids(CardActionKind.CHARGE_FEE)
    assert spec.forbids(CardActionKind.ISSUE_REFUND)
    assert spec.forbids(CardActionKind.RELEASE_CODE)
    assert spec.forbids(CardActionKind.CONFIRM_BOOKING)


def test_compliance_strict_pins_to_observe() -> None:
    """Compliance-strict pins autonomy ceiling to OBSERVE."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.COMPLIANCE_STRICT]
    assert spec.autonomy_ceiling is AutonomyState.OBSERVE


def test_cooperative_has_no_caps_or_denials() -> None:
    """Default style imposes no constraint envelope."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.COOPERATIVE]
    assert spec.denylist == frozenset()
    assert spec.autonomy_ceiling is None
    assert spec.reversibility_ceiling is None


@pytest.mark.parametrize(
    ("engine_state", "expected"),
    [
        (AutonomyState.AUTOPILOT, AutonomyState.SEMI_AUTO),
        (AutonomyState.SEMI_AUTO, AutonomyState.SEMI_AUTO),
        (AutonomyState.OBSERVE, AutonomyState.OBSERVE),
    ],
    ids=["autopilot_capped", "at_ceiling", "below_ceiling"],
)
def test_cap_autonomy_picks_lower_rank(
    engine_state: AutonomyState,
    expected: AutonomyState,
) -> None:
    """Defensive ceiling caps AUTOPILOT and leaves below alone."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.DEFENSIVE]
    assert spec.cap_autonomy(engine_state) is expected


def test_cap_autonomy_no_ceiling_returns_input() -> None:
    """Cooperative style passes the engine state through."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.COOPERATIVE]
    assert spec.cap_autonomy(AutonomyState.AUTOPILOT) is (
        AutonomyState.AUTOPILOT
    )


@pytest.mark.parametrize(
    ("engine_tier", "expected"),
    [
        (ReversibilityTier.RED, ReversibilityTier.AMBER),
        (ReversibilityTier.AMBER, ReversibilityTier.AMBER),
        (ReversibilityTier.GREEN, ReversibilityTier.GREEN),
    ],
    ids=["red_capped", "at_ceiling", "below_ceiling"],
)
def test_cap_reversibility_picks_more_cautious(
    engine_tier: ReversibilityTier,
    expected: ReversibilityTier,
) -> None:
    """AMBER ceiling vs RED engine returns AMBER (more cautious)."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.DEFENSIVE]
    assert spec.cap_reversibility(engine_tier) is expected


def test_budget_no_compromise_denies_discounts() -> None:
    """Budget discipline forbids discretionary discounts."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.BUDGET_NO_COMPROMISE]
    assert spec.forbids(CardActionKind.APPLY_DISCOUNT)
    assert spec.forbids(CardActionKind.COUNTER_OFFER)
    assert spec.autonomy_ceiling is AutonomyState.SEMI_AUTO


def test_vip_white_glove_has_no_denials() -> None:
    """VIP style imposes no structural denials."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.VIP_WHITE_GLOVE]
    assert spec.denylist == frozenset()
    assert spec.preference_weights["review_score"] > (
        spec.preference_weights["adr"]
    )


def test_aggressive_revenue_weights_favour_adr() -> None:
    """Revenue-first style prefers ADR over review score."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.AGGRESSIVE_REVENUE]
    assert spec.preference_weights["adr"] > (
        spec.preference_weights["review_score"]
    )
