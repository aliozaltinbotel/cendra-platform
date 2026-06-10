"""Decision card builder.

The builder gathers evidence from every available source (learned
rules, historical cases, active blockers, owner preferences) and
produces a :class:`DecisionCard` ready for rendering.

It does **not** execute the recommended action — it only prepares
it.  Execution goes through the action pipeline where the
reversibility tier gates the Undo behavior.

When a :class:`brain_engine.planner.PlannerDecision` is supplied,
the Planner constraint envelope (denylist, autonomy ceiling,
reversibility ceiling) is applied to the built card via
:func:`brain_engine.planner.apply_style` before it is returned —
this is the GR00T P1 integration seam (Moat #4).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from brain_engine.autonomy.models import AutonomyState
from brain_engine.cards.models import (
    DecisionCard,
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
    ReversibilityTier,
)

if TYPE_CHECKING:
    from brain_engine.planner.decision import PlannerDecision


_BLOCKED_TITLE_PREFIX = "Hold: "
_READY_TITLE_PREFIX = "Ready: "


class DecisionCardBuilder:
    """Compose a :class:`DecisionCard` from evidence rows and an action.

    The builder is stateless.  Callers pass the evidence already
    collected upstream (rules, cases, blockers, preferences) — the
    builder only arranges, deduplicates, and writes the trust footer.
    """

    def build(
        self,
        *,
        property_id: str,
        workflow: str,
        context_tag: str,
        reasoning: Sequence[ReasoningRow],
        action: PreparedAction,
        autonomy_state: AutonomyState,
        hold_seconds: int = 60,
        style_decision: PlannerDecision | None = None,
    ) -> DecisionCard:
        """Return a new :class:`DecisionCard`.

        Args:
            property_id: Property the card describes.
            workflow: Workflow name (e.g. ``"send_access_code"``).
            context_tag: One-line situational label.
            reasoning: Ordered evidence rows.
            action: The prepared action to recommend.
            autonomy_state: Current autonomy state for the workflow.
            hold_seconds: Countdown shown on SEMI_AUTO cards.
            style_decision: Optional Planner decision; when supplied,
                its constraint envelope (denylist, autonomy ceiling,
                reversibility ceiling) is applied *before* the card
                is composed so the trust footer reflects the final
                capped values.  Existing callers that pass no
                decision get the unchanged behaviour.
        """
        action, reasoning_rows, autonomy_state = self._apply_style(
            action=action,
            reasoning=reasoning,
            autonomy_state=autonomy_state,
            decision=style_decision,
        )
        rows = self._dedupe(reasoning_rows)
        title = self._title(workflow, rows)
        footer = self._trust_footer(
            state=autonomy_state,
            hold_seconds=hold_seconds,
            reversibility=action.reversibility,
        )
        return DecisionCard(
            property_id=property_id,
            workflow=workflow,
            context_tag=context_tag,
            title=title,
            reasoning=tuple(rows),
            action=action,
            trust_footer=footer,
            autonomy_state=autonomy_state,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_style(
        *,
        action: PreparedAction,
        reasoning: Sequence[ReasoningRow],
        autonomy_state: AutonomyState,
        decision: PlannerDecision | None,
    ) -> tuple[PreparedAction, list[ReasoningRow], AutonomyState]:
        """Apply the Planner constraint envelope to builder inputs.

        Returns the (possibly modified) action, the reasoning rows
        (with a blocker row appended when the action was forbidden),
        and the capped autonomy state.  When ``decision`` is
        ``None`` the inputs pass through unchanged.
        """
        if decision is None:
            return action, list(reasoning), autonomy_state
        # Local import keeps the cards module free of an eager
        # planner dependency at module-load time and avoids any
        # circular wiring with the planner package.
        from brain_engine.planner.integration import apply_style as _ap

        seed_card = DecisionCard(
            property_id="_seed",
            workflow="_seed",
            context_tag="_seed",
            title="_seed",
            reasoning=tuple(reasoning),
            action=action,
            trust_footer="_seed",
            autonomy_state=autonomy_state,
        )
        applied = _ap(card=seed_card, decision=decision)
        return (
            applied.card.action,
            list(applied.card.reasoning),
            applied.card.autonomy_state,
        )

    @staticmethod
    def _dedupe(rows: Sequence[ReasoningRow]) -> list[ReasoningRow]:
        """Drop duplicate rows keyed by (kind, label)."""
        seen: set[tuple[EvidenceKind, str]] = set()
        out: list[ReasoningRow] = []
        for row in rows:
            key = (row.kind, row.label)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    @staticmethod
    def _title(
        workflow: str,
        rows: Sequence[ReasoningRow],
    ) -> str:
        """Compose a human headline from workflow + blocker presence."""
        label = workflow.replace("_", " ").strip()
        if not label:
            label = "action"
        if any(r.kind is EvidenceKind.BLOCKER for r in rows):
            return f"{_BLOCKED_TITLE_PREFIX}{label}"
        return f"{_READY_TITLE_PREFIX}{label}"

    @staticmethod
    def _trust_footer(
        *,
        state: AutonomyState,
        hold_seconds: int,
        reversibility: ReversibilityTier,
    ) -> str:
        """Write the one-sentence trust footer.

        The footer surfaces both the autonomy promise and the undo
        affordance so the PM always knows how to reverse.
        """
        undo = _UNDO_COPY[reversibility]
        if state is AutonomyState.AUTOPILOT:
            return f"Autopilot. {undo}"
        if state is AutonomyState.SEMI_AUTO:
            return (
                f"Semi-auto, runs in {max(0, hold_seconds)} s unless "
                f"cancelled. {undo}"
            )
        return f"Observe mode — PM confirmation required. {undo}"


_UNDO_COPY: dict[ReversibilityTier, str] = {
    ReversibilityTier.GREEN: "Fully reversible within 60 s.",
    ReversibilityTier.AMBER: "Reversible via compensating action.",
    ReversibilityTier.RED: "Irreversible — audit log only.",
}
