"""End-to-end behaviour of :func:`apply_style`."""

from __future__ import annotations

from datetime import datetime, timezone

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.cards.models import (
    DecisionCard,
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
    ReversibilityTier,
)
from brain_engine.planner.decision import PlannerDecision
from brain_engine.planner.integration import apply_style
from brain_engine.planner.styles import (
    BUILTIN_STYLE_SPECS,
    PlannerStyleId,
)


def _card(
    action_type: str,
    *,
    autonomy_state: AutonomyState = AutonomyState.AUTOPILOT,
    reversibility: ReversibilityTier = ReversibilityTier.RED,
) -> DecisionCard:
    """Build a minimal :class:`DecisionCard` for a test."""
    return DecisionCard(
        property_id="prop_x",
        workflow="wf",
        context_tag="tag",
        title="Ready: wf",
        reasoning=(
            ReasoningRow(kind=EvidenceKind.RULE, label="r"),
        ),
        action=PreparedAction(
            action_type=action_type,
            reversibility=reversibility,
        ),
        trust_footer="footer",
        autonomy_state=autonomy_state,
        created_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )


def _decision(style_id: PlannerStyleId) -> PlannerDecision:
    """Build a :class:`PlannerDecision` from a built-in spec."""
    return PlannerDecision(
        style_id=style_id,
        spec=BUILTIN_STYLE_SPECS[style_id],
        rationale="test",
    )


def test_compliance_strict_replaces_forbidden_action() -> None:
    """A forbidden action becomes a LOG_DECISION audit-only entry."""
    card = _card(CardActionKind.CHARGE_FEE.value)
    decision = _decision(PlannerStyleId.COMPLIANCE_STRICT)

    out = apply_style(card=card, decision=decision)

    assert out.forbidden is True
    assert out.card.action.action_type == (
        CardActionKind.LOG_DECISION.value
    )
    assert out.card.action.payload["forbidden_kind"] == (
        CardActionKind.CHARGE_FEE.value
    )
    assert any(
        r.kind is EvidenceKind.BLOCKER and "forbids" in r.label
        for r in out.card.reasoning
    )


def test_compliance_strict_caps_autonomy_to_observe() -> None:
    """Compliance-strict downgrades AUTOPILOT to OBSERVE."""
    card = _card(
        CardActionKind.SEND_MESSAGE.value,
        autonomy_state=AutonomyState.AUTOPILOT,
    )
    decision = _decision(PlannerStyleId.COMPLIANCE_STRICT)

    out = apply_style(card=card, decision=decision)

    assert out.card.autonomy_state is AutonomyState.OBSERVE
    assert out.forbidden is False


def test_defensive_caps_reversibility_red_to_amber() -> None:
    """Defensive ceiling caps RED action to AMBER."""
    card = _card(
        CardActionKind.SEND_MESSAGE.value,
        reversibility=ReversibilityTier.RED,
    )
    decision = _decision(PlannerStyleId.DEFENSIVE)

    out = apply_style(card=card, decision=decision)

    assert out.card.action.reversibility is ReversibilityTier.AMBER


def test_cooperative_leaves_card_intact() -> None:
    """Cooperative style imposes no envelope; card passes through."""
    card = _card(
        CardActionKind.CHARGE_FEE.value,
        autonomy_state=AutonomyState.AUTOPILOT,
    )
    decision = _decision(PlannerStyleId.COOPERATIVE)

    out = apply_style(card=card, decision=decision)

    assert out.forbidden is False
    assert out.card.autonomy_state is AutonomyState.AUTOPILOT
    assert out.card.action.reversibility is ReversibilityTier.RED
    assert out.card.action.action_type == (
        CardActionKind.CHARGE_FEE.value
    )


def test_unknown_action_type_passes_denylist_step() -> None:
    """Non-canonical action types skip the denylist check."""
    card = _card("custom_unknown_action")
    decision = _decision(PlannerStyleId.COMPLIANCE_STRICT)

    out = apply_style(card=card, decision=decision)

    assert out.forbidden is False
    assert out.card.action.action_type == "custom_unknown_action"
    # Ceilings still apply.
    assert out.card.autonomy_state is AutonomyState.OBSERVE


def test_decision_is_carried_on_output() -> None:
    """The :class:`PlannerDecision` is preserved on the result."""
    card = _card(CardActionKind.SEND_MESSAGE.value)
    decision = _decision(PlannerStyleId.VIP_WHITE_GLOVE)

    out = apply_style(card=card, decision=decision)

    assert out.decision is decision


def test_forbidden_action_preserves_original_payload() -> None:
    """Original payload merges into the LOG_DECISION fallback."""
    card = DecisionCard(
        property_id="prop_x",
        workflow="wf",
        context_tag="tag",
        title="Ready: wf",
        reasoning=(),
        action=PreparedAction(
            action_type=CardActionKind.ISSUE_REFUND.value,
            payload={"amount_eur": 200, "reason": "noise"},
            reversibility=ReversibilityTier.RED,
        ),
        trust_footer="footer",
        autonomy_state=AutonomyState.AUTOPILOT,
    )
    decision = _decision(PlannerStyleId.COMPLIANCE_STRICT)

    out = apply_style(card=card, decision=decision)

    assert out.forbidden is True
    assert out.card.action.payload["amount_eur"] == 200
    assert out.card.action.payload["reason"] == "noise"
    assert out.card.action.payload["forbidden_kind"] == (
        CardActionKind.ISSUE_REFUND.value
    )
