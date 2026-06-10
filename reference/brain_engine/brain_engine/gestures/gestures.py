"""Pattern-rule → one-tap gesture translation.

The builder converts matched :class:`PatternRule` rows into
:class:`PatternGesture` objects that the mobile client can render as
one-tap suggestions.  The mapping is deterministic:

- :class:`ExecutionMode` decides the :class:`GestureMode` affordance.
- :class:`RiskLevel` decides the :class:`ReversibilityTier` and the
  default Undo window the card builder later consumes.
- :class:`DecisionType` decides the human-readable button label.

Rules that are inactive, expired, or whose conditions do not satisfy
the provided features are filtered out.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog

from brain_engine.cards.models import ReversibilityTier
from brain_engine.gestures.models import (
    GestureContext,
    GestureMode,
    PatternGesture,
)
from brain_engine.patterns.models import (
    DecisionType,
    ExecutionMode,
    PatternRule,
    RiskLevel,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Static mapping tables
# ---------------------------------------------------------------------------


_MODE_BY_EXECUTION: dict[ExecutionMode, GestureMode] = {
    ExecutionMode.AUTO: GestureMode.ONE_TAP,
    ExecutionMode.ASK: GestureMode.CONFIRM,
    ExecutionMode.APPROVAL: GestureMode.APPROVAL_REQUIRED,
    ExecutionMode.BLOCK: GestureMode.BLOCKED,
}


_TIER_BY_RISK: dict[RiskLevel, ReversibilityTier] = {
    RiskLevel.LOW: ReversibilityTier.GREEN,
    RiskLevel.MEDIUM: ReversibilityTier.AMBER,
    RiskLevel.HIGH: ReversibilityTier.RED,
    RiskLevel.CRITICAL: ReversibilityTier.RED,
}


_LABEL_BY_DECISION: dict[DecisionType, str] = {
    DecisionType.ASK: "Ask guest",
    DecisionType.APPROVE: "Approve as usual",
    DecisionType.DENY: "Decline politely",
    DecisionType.CHARGE: "Charge as usual",
    DecisionType.QUOTE: "Send quote",
    DecisionType.BLOCK: "Block and escalate",
    DecisionType.ESCALATE: "Escalate to PM",
    DecisionType.DISPATCH: "Dispatch as usual",
    DecisionType.FETCH_LIVE_DATA: "Fetch live data",
    DecisionType.OFFER: "Offer as usual",
    DecisionType.INFORM: "Send update",
    DecisionType.RELEASE: "Release info",
    DecisionType.DEFER: "Wait and revisit",
    DecisionType.MODIFY_BOOKING: "Modify booking",
    DecisionType.REFUND: "Refund as usual",
    DecisionType.CLAIM: "Submit claim",
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class PatternGestureBuilder:
    """Convert matched pattern rules into UI gestures."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.0,
        limit: int = 5,
        evaluate_conditions: bool = True,
    ) -> None:
        self._min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self._limit = max(1, limit)
        self._evaluate_conditions = evaluate_conditions
        self._log = logger.bind(component="pattern_gestures")

    def build(
        self,
        rules: Iterable[PatternRule],
        *,
        context: GestureContext,
    ) -> tuple[PatternGesture, ...]:
        """Return a ranked tuple of gestures for ``rules``."""
        features = context.features
        eligible: list[PatternGesture] = []
        for rule in rules:
            if not self._is_eligible(rule, features=features):
                continue
            eligible.append(self._to_gesture(rule))
        ranked = sorted(
            eligible,
            key=lambda g: (-g.confidence, g.label),
        )
        return tuple(ranked[: self._limit])

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_eligible(
        self,
        rule: PatternRule,
        *,
        features: dict[str, Any],
    ) -> bool:
        if not rule.active:
            return False
        if rule.is_expired:
            return False
        if rule.confidence < self._min_confidence:
            return False
        if self._evaluate_conditions and rule.conditions:
            if not rule.matches_conditions(features):
                return False
        return True

    def _to_gesture(self, rule: PatternRule) -> PatternGesture:
        """Translate a single rule into its gesture representation."""
        mode = _MODE_BY_EXECUTION.get(
            rule.execution_mode,
            GestureMode.CONFIRM,
        )
        tier = _TIER_BY_RISK.get(
            rule.risk_level,
            ReversibilityTier.AMBER,
        )
        label = _LABEL_BY_DECISION.get(
            rule.action.action_type,
            rule.action.action_type.value.replace("_", " ").capitalize(),
        )
        return PatternGesture(
            label=label,
            pattern_id=rule.pattern_id,
            scenario=rule.scenario,
            action=rule.action,
            mode=mode,
            confidence=rule.confidence,
            risk_level=rule.risk_level,
            reversibility=tier,
            metadata={
                "support_count": rule.support_count,
                "counterexample_ratio": rule.counterexample_ratio,
                "scope": rule.scope.value,
                "scope_id": rule.scope_id,
            },
        )
