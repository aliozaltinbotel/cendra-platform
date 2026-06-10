"""Behaviour of the situational styles added by Moat #11."""

from __future__ import annotations

import pytest

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.cards.models import ReversibilityTier
from brain_engine.planner.styles import (
    BUILTIN_STYLE_SPECS,
    PlannerStyleId,
)


_MOAT_11_STYLES = (
    PlannerStyleId.SEASONAL_HIGH,
    PlannerStyleId.SEASONAL_LOW,
    PlannerStyleId.POST_INCIDENT_RECOVERY,
    PlannerStyleId.REGULATORY_AUDIT_WINDOW,
    PlannerStyleId.FAMILY_FRIENDLY_STRICT,
    PlannerStyleId.PET_FRIENDLY,
)


def test_six_situational_styles_added() -> None:
    """Moat #11 brought the total style count to twelve."""
    assert len(BUILTIN_STYLE_SPECS) == 12
    for style_id in _MOAT_11_STYLES:
        assert style_id in BUILTIN_STYLE_SPECS


def test_seasonal_high_weights_favour_adr() -> None:
    """Peak-season style biases ADR over occupancy + review."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.SEASONAL_HIGH]
    assert spec.preference_weights["adr"] > (
        spec.preference_weights["occupancy"]
    )
    assert spec.preference_weights["adr"] > (
        spec.preference_weights["review_score"]
    )


def test_seasonal_low_weights_favour_occupancy() -> None:
    """Off-season style biases occupancy over ADR."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.SEASONAL_LOW]
    assert spec.preference_weights["occupancy"] > (
        spec.preference_weights["adr"]
    )


def test_post_incident_recovery_caps_and_denials() -> None:
    """Post-incident style caps autonomy + reversibility + denylist."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.POST_INCIDENT_RECOVERY]
    assert spec.autonomy_ceiling is AutonomyState.SEMI_AUTO
    assert spec.reversibility_ceiling is ReversibilityTier.AMBER
    assert spec.forbids(CardActionKind.APPLY_DISCOUNT)
    assert spec.forbids(CardActionKind.RELEASE_CODE)


def test_regulatory_audit_window_pinned_to_observe() -> None:
    """Audit-window style is the most restrictive shipped."""
    spec = BUILTIN_STYLE_SPECS[
        PlannerStyleId.REGULATORY_AUDIT_WINDOW
    ]
    assert spec.autonomy_ceiling is AutonomyState.OBSERVE
    # Denylist covers financial + booking + discretionary moves
    for kind in (
        CardActionKind.CHARGE_FEE,
        CardActionKind.ISSUE_REFUND,
        CardActionKind.RELEASE_CODE,
        CardActionKind.CONFIRM_BOOKING,
        CardActionKind.CANCEL_BOOKING,
        CardActionKind.APPLY_DISCOUNT,
        CardActionKind.COUNTER_OFFER,
        CardActionKind.DISPATCH_VENDOR,
    ):
        assert spec.forbids(kind)


def test_family_friendly_strict_denies_discretionary_pricing() -> None:
    """Family-orientation forbids discount / counter-offer."""
    spec = BUILTIN_STYLE_SPECS[
        PlannerStyleId.FAMILY_FRIENDLY_STRICT
    ]
    assert spec.forbids(CardActionKind.APPLY_DISCOUNT)
    assert spec.forbids(CardActionKind.COUNTER_OFFER)
    assert spec.autonomy_ceiling is AutonomyState.SEMI_AUTO


def test_pet_friendly_has_no_caps() -> None:
    """Pet-friendly style does not impose extra caps."""
    spec = BUILTIN_STYLE_SPECS[PlannerStyleId.PET_FRIENDLY]
    assert spec.denylist == frozenset()
    assert spec.autonomy_ceiling is None
    assert spec.reversibility_ceiling is None


@pytest.mark.parametrize(
    "style_id",
    list(_MOAT_11_STYLES),
    ids=lambda s: s.value,
)
def test_style_description_non_empty(
    style_id: PlannerStyleId,
) -> None:
    """Every situational style ships a regulator-quotable summary."""
    assert BUILTIN_STYLE_SPECS[style_id].description
