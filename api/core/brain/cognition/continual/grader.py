"""APM Grader — Reinforcement signal for Brain Engine skill evolution.

Based on: MetaClaw Process Reward Model + RFT (OpenAI).
Scores each interaction outcome to determine skill quality.

This is NOT a training signal — it drives skill evolution
(ProceduralMemory updates) with frozen LLM weights.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class InteractionOutcome:
    """Observed outcome of a Brain Engine interaction.

    Attributes:
        resolved_without_escalation: Issue solved without human help.
        owner_did_not_intervene: Owner did not override the decision.
        guest_satisfaction: 'positive', 'neutral', or 'negative'.
        response_time_minutes: Time from event to resolution.
        cascade_level: Which cascade level resolved it (1 = first try).
        owner_rejected: Owner explicitly rejected the action.
        hallucination_detected: Guardrails caught a hallucination.
        wrong_contact_called: Wrong person was contacted.
    """

    resolved_without_escalation: bool = False
    owner_did_not_intervene: bool = True
    guest_satisfaction: str = "neutral"
    response_time_minutes: float = 10.0
    cascade_level: int = 1
    owner_rejected: bool = False
    hallucination_detected: bool = False
    wrong_contact_called: bool = False


# Scoring weights (from Blueprint v5)
_POSITIVE_RESOLVED = 0.25
_POSITIVE_NO_INTERVENTION = 0.15
_POSITIVE_GUEST_HAPPY = 0.20
_POSITIVE_FAST_RESPONSE = 0.10
_POSITIVE_FIRST_TRY = 0.15

_NEGATIVE_OWNER_REJECTED = 0.35
_NEGATIVE_GUEST_UNHAPPY = 0.25
_NEGATIVE_SLOW_RESPONSE = 0.15
_NEGATIVE_HALLUCINATION = 0.40
_NEGATIVE_WRONG_CONTACT = 0.30

_BASELINE = 0.5
_FAST_RESPONSE_THRESHOLD = 5.0  # minutes
_SLOW_RESPONSE_THRESHOLD = 60.0  # minutes


class APMGrader:
    """Grades Brain Engine interaction outcomes.

    Produces a 0.0-1.0 quality score used by SkillEvolutionEngine
    to decide whether to reinforce or evolve skills.
    """

    def grade(self, outcome: InteractionOutcome) -> float:
        """Score an interaction outcome.

        Args:
            outcome: The observed outcome of an interaction.

        Returns:
            Quality score between 0.0 and 1.0.
        """
        score = _BASELINE
        score += self._positive_signals(outcome)
        score -= self._negative_signals(outcome)
        score = max(0.0, min(1.0, score))

        logger.debug("APMGrader score: %.2f", score)
        return score

    def grade_batch(
        self,
        outcomes: list[InteractionOutcome],
    ) -> list[float]:
        """Score a batch of outcomes.

        Args:
            outcomes: List of interaction outcomes.

        Returns:
            List of quality scores.
        """
        return [self.grade(o) for o in outcomes]

    def _positive_signals(self, outcome: InteractionOutcome) -> float:
        """Sum positive scoring signals.

        Args:
            outcome: The interaction outcome.

        Returns:
            Total positive score adjustment.
        """
        score = 0.0

        if outcome.resolved_without_escalation:
            score += _POSITIVE_RESOLVED

        if outcome.owner_did_not_intervene:
            score += _POSITIVE_NO_INTERVENTION

        if outcome.guest_satisfaction == "positive":
            score += _POSITIVE_GUEST_HAPPY

        if outcome.response_time_minutes < _FAST_RESPONSE_THRESHOLD:
            score += _POSITIVE_FAST_RESPONSE

        if outcome.cascade_level == 1:
            score += _POSITIVE_FIRST_TRY

        return score

    def _negative_signals(self, outcome: InteractionOutcome) -> float:
        """Sum negative scoring signals.

        Args:
            outcome: The interaction outcome.

        Returns:
            Total negative score adjustment.
        """
        score = 0.0

        if outcome.owner_rejected:
            score += _NEGATIVE_OWNER_REJECTED

        if outcome.guest_satisfaction == "negative":
            score += _NEGATIVE_GUEST_UNHAPPY

        if outcome.response_time_minutes > _SLOW_RESPONSE_THRESHOLD:
            score += _NEGATIVE_SLOW_RESPONSE

        if outcome.hallucination_detected:
            score += _NEGATIVE_HALLUCINATION

        if outcome.wrong_contact_called:
            score += _NEGATIVE_WRONG_CONTACT

        return score
