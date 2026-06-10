"""Integration of :class:`DecisionCardBuilder` with the Planner.

These tests exercise the optional ``style_decision`` parameter on
:meth:`DecisionCardBuilder.build` — the GR00T P1 seam Moat #4 is
staked on.  When no decision is supplied the builder behaves
exactly as before (the existing call sites stay green).
"""

from __future__ import annotations

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.cards.builder import DecisionCardBuilder
from brain_engine.cards.models import (
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
    ReversibilityTier,
)
from brain_engine.planner.decision import PlannerDecision
from brain_engine.planner.styles import (
    BUILTIN_STYLE_SPECS,
    PlannerStyleId,
)


def _decision(style_id: PlannerStyleId) -> PlannerDecision:
    return PlannerDecision(
        style_id=style_id,
        spec=BUILTIN_STYLE_SPECS[style_id],
        rationale="test",
    )


def test_builder_without_style_keeps_legacy_behaviour() -> None:
    """No ``style_decision`` argument — output unchanged from baseline."""
    builder = DecisionCardBuilder()
    card = builder.build(
        property_id="prop_x",
        workflow="release_code",
        context_tag="Access code hold",
        reasoning=(
            ReasoningRow(kind=EvidenceKind.RULE, label="key rule"),
        ),
        action=PreparedAction(
            action_type=CardActionKind.RELEASE_CODE.value,
            reversibility=ReversibilityTier.RED,
        ),
        autonomy_state=AutonomyState.AUTOPILOT,
    )
    assert card.autonomy_state is AutonomyState.AUTOPILOT
    assert card.action.action_type == (
        CardActionKind.RELEASE_CODE.value
    )
    assert card.action.reversibility is ReversibilityTier.RED


def test_builder_applies_compliance_strict_envelope() -> None:
    """Compliance-strict swaps forbidden action and caps autonomy."""
    builder = DecisionCardBuilder()
    card = builder.build(
        property_id="prop_x",
        workflow="release_code",
        context_tag="Access code hold",
        reasoning=(
            ReasoningRow(kind=EvidenceKind.RULE, label="key rule"),
        ),
        action=PreparedAction(
            action_type=CardActionKind.RELEASE_CODE.value,
            reversibility=ReversibilityTier.RED,
        ),
        autonomy_state=AutonomyState.AUTOPILOT,
        style_decision=_decision(PlannerStyleId.COMPLIANCE_STRICT),
    )
    assert card.action.action_type == (
        CardActionKind.LOG_DECISION.value
    )
    assert card.action.payload["forbidden_kind"] == (
        CardActionKind.RELEASE_CODE.value
    )
    assert card.autonomy_state is AutonomyState.OBSERVE
    assert any(
        r.kind is EvidenceKind.BLOCKER and "forbids" in r.label
        for r in card.reasoning
    )


def test_builder_footer_reflects_capped_state() -> None:
    """The trust footer is computed *after* the autonomy cap."""
    builder = DecisionCardBuilder()
    card = builder.build(
        property_id="prop_x",
        workflow="message",
        context_tag="Late checkout",
        reasoning=(
            ReasoningRow(kind=EvidenceKind.RULE, label="late rule"),
        ),
        action=PreparedAction(
            action_type=CardActionKind.SEND_MESSAGE.value,
            reversibility=ReversibilityTier.AMBER,
        ),
        autonomy_state=AutonomyState.AUTOPILOT,
        style_decision=_decision(PlannerStyleId.DEFENSIVE),
    )
    assert card.autonomy_state is AutonomyState.SEMI_AUTO
    # Footer must mention semi-auto, not autopilot.
    assert "Semi-auto" in card.trust_footer


def test_builder_preserves_action_when_style_permits() -> None:
    """VIP_white_glove imposes no envelope; action passes through."""
    builder = DecisionCardBuilder()
    card = builder.build(
        property_id="prop_x",
        workflow="counter_offer",
        context_tag="Discount inquiry",
        reasoning=(
            ReasoningRow(kind=EvidenceKind.RULE, label="vip rule"),
        ),
        action=PreparedAction(
            action_type=CardActionKind.COUNTER_OFFER.value,
            reversibility=ReversibilityTier.AMBER,
        ),
        autonomy_state=AutonomyState.AUTOPILOT,
        style_decision=_decision(PlannerStyleId.VIP_WHITE_GLOVE),
    )
    assert card.action.action_type == (
        CardActionKind.COUNTER_OFFER.value
    )
    assert card.autonomy_state is AutonomyState.AUTOPILOT
