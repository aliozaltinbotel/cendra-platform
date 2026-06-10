"""Apply a Planner decision to a built :class:`DecisionCard`.

The Planner's constraint envelope (denylist, autonomy ceiling,
reversibility ceiling) lives on :class:`PlannerStyleSpec`.  This
module is the single place those caps are applied to a card so the
behaviour stays auditable from one entry point.

Brain Engine's :class:`brain_engine.cards.builder.DecisionCardBuilder`
is intentionally unmodified — Moat #4 leaves the builder pure and
adds the planner as a wrapping pass.  Future moats (Sprint 5
critic, Moat #2 DSL) plug into the same wrapping pass.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.cards.models import (
    DecisionCard,
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
)
from brain_engine.planner.decision import PlannerDecision


__all__ = ["StyleAppliedCard", "apply_style"]


@dataclass(frozen=True, slots=True)
class StyleAppliedCard:
    """A card after the Planner constraint envelope was applied.

    Attributes:
        card: The (possibly modified) :class:`DecisionCard`.
        decision: The :class:`PlannerDecision` that produced the
            envelope.  Always present so the audit log can record
            *which style* shaped the card.
        forbidden: ``True`` when the style's denylist forbade the
            card's prepared action; in that case ``card.action``
            carries a fallback :attr:`CardActionKind.LOG_DECISION`
            audit-only action and the original kind is preserved
            in the payload under ``forbidden_kind``.
    """

    card: DecisionCard
    decision: PlannerDecision
    forbidden: bool


def apply_style(
    *,
    card: DecisionCard,
    decision: PlannerDecision,
) -> StyleAppliedCard:
    """Return ``card`` adjusted by the Planner constraint envelope.

    Order of application:

    1. Denylist — if the action kind is forbidden, the action is
       replaced by a :attr:`CardActionKind.LOG_DECISION` audit-only
       action and a blocker reasoning row is appended.
    2. Reversibility ceiling — the action's reversibility tier is
       capped to the more cautious of (engine_tier, ceiling).
    3. Autonomy ceiling — the card's autonomy state is capped.

    Cards whose :attr:`PreparedAction.action_type` does not match a
    canonical :class:`CardActionKind` pass through untouched by the
    denylist step (only the ceilings apply).
    """
    spec = decision.spec
    forbidden = False
    action = card.action
    reasoning = list(card.reasoning)
    kind = _canonical_kind(action.action_type)
    if kind is not None and spec.forbids(kind):
        forbidden = True
        action = PreparedAction(
            action_type=CardActionKind.LOG_DECISION.value,
            payload={
                "forbidden_kind": kind.value,
                **action.payload,
            },
            reversibility=action.reversibility,
            undo_window_seconds=action.undo_window_seconds,
        )
        reasoning.append(
            ReasoningRow(
                kind=EvidenceKind.BLOCKER,
                label=(
                    f"style {decision.style_id.value} forbids "
                    f"{kind.value}"
                ),
                weight=1.0,
                reference_id=decision.style_id.value,
            )
        )
    capped_action = replace(
        action,
        reversibility=spec.cap_reversibility(action.reversibility),
    )
    capped_state = spec.cap_autonomy(card.autonomy_state)
    new_card = replace(
        card,
        action=capped_action,
        autonomy_state=capped_state,
        reasoning=tuple(reasoning),
    )
    return StyleAppliedCard(
        card=new_card,
        decision=decision,
        forbidden=forbidden,
    )


def _canonical_kind(action_type: str) -> CardActionKind | None:
    """Return the matching :class:`CardActionKind` or ``None``."""
    try:
        return CardActionKind(action_type)
    except ValueError:
        return None
