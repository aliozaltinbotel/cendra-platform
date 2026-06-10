"""Complexity Router — CogRouter-inspired adaptive cognitive depth.

Based on: CogRouter (arXiv:2602.12662, Fudan/Tencent).
Result: 7B model beats GPT-4o at 62% fewer tokens.

Determines cognitive depth for each incoming request:
    L1 (Instinct):  Simple lookup/FAQ -> GPT-4o Mini, <500ms
    L2 (Situation):  Policy check needed -> GPT-4o Mini + PolicyEnforcer
    L3 (Experience): Complex reasoning -> GPT-4o / Claude Sonnet
    L4 (Strategy):   Multi-factor critical -> Claude Opus, full memory
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CognitiveLevel(Enum):
    """Four cognitive depth levels for request routing."""

    L1_INSTINCT = "instinct"      # ~80% of requests
    L2_SITUATION = "situation"    # ~15% of requests
    L3_EXPERIENCE = "experience"  # ~4% of requests
    L4_STRATEGY = "strategy"      # ~1% of requests


COMPLEX_SIGNALS: list[str] = [
    "schedule_conflict",
    "guest_complaint",
    "cleaner_no_reply_2x",
    "owner_rejected",
    "damage_detected",
    "last_minute_booking",
    "vip_guest",
    "new_city_no_data",
    "cost_above_threshold",
    "multi_issue",
    "ambiguous_reply",
    "recurring_issue",
]

SIGNAL_WEIGHTS: dict[str, float] = {
    "schedule_conflict": 0.25,
    "guest_complaint": 0.20,
    "cleaner_no_reply_2x": 0.20,
    "owner_rejected": 0.30,
    "damage_detected": 0.25,
    "last_minute_booking": 0.20,
    "vip_guest": 0.15,
    "new_city_no_data": 0.20,
    "cost_above_threshold": 0.25,
    "multi_issue": 0.30,
    "ambiguous_reply": 0.15,
    "recurring_issue": 0.20,
}

# Thresholds for routing decisions
_L2_THRESHOLD = 0.2
_L3_THRESHOLD = 0.5
_L4_THRESHOLD = 0.8

# Metacognition confidence below which we bump complexity
_CONFIDENCE_LOW = 0.4

# Surprise score that warrants deeper reasoning
_SURPRISE_HIGH = 0.7


@dataclass(frozen=True)
class MemoryState:
    """Snapshot of memory-related signals for routing decisions.

    Attributes:
        surprise_score: Latest surprise score from SurpriseDetector.
        recent_confidence: Recent average confidence from metacognition.
        procedures_found: Number of matching procedural skills.
    """

    surprise_score: float = 0.0
    recent_confidence: float = 0.8
    procedures_found: int = 0


@dataclass(frozen=True)
class ModelConfig:
    """LLM configuration for a given cognitive level.

    Attributes:
        model: LLM model identifier.
        max_tokens: Maximum response tokens.
        temperature: Sampling temperature.
        memory_retrieval: Depth of memory retrieval strategy.
        guardrails: Guardrail pipeline mode.
    """

    model: str
    max_tokens: int
    temperature: float
    memory_retrieval: str
    guardrails: str


_LEVEL_CONFIGS: dict[CognitiveLevel, ModelConfig] = {
    CognitiveLevel.L1_INSTINCT: ModelConfig(
        model="gpt-4o-mini",
        max_tokens=500,
        temperature=0.3,
        memory_retrieval="working_only",
        guardrails="basic",
    ),
    CognitiveLevel.L2_SITUATION: ModelConfig(
        model="gpt-4o-mini",
        max_tokens=1000,
        temperature=0.5,
        memory_retrieval="working+procedural",
        guardrails="standard",
    ),
    CognitiveLevel.L3_EXPERIENCE: ModelConfig(
        model="gpt-4o",
        max_tokens=2000,
        temperature=0.7,
        memory_retrieval="full",
        guardrails="full+neuro_symbolic",
    ),
    CognitiveLevel.L4_STRATEGY: ModelConfig(
        model="gpt-4o",
        max_tokens=4000,
        temperature=0.7,
        memory_retrieval="full+historical",
        guardrails="full+neuro_symbolic+human_review",
    ),
}


class ComplexityRouter:
    """Routes each request to the appropriate cognitive depth level.

    Evaluates complex signals, surprise score, and metacognition
    confidence to determine whether a request needs fast pattern
    matching (L1) or deep strategic reasoning (L4).
    """

    def route(
        self,
        event: str,
        context: dict[str, Any],
        memory_state: MemoryState,
    ) -> CognitiveLevel:
        """Determine cognitive depth needed for this event.

        Args:
            event: Event type string.
            context: Request context with metadata.
            memory_state: Current memory signals snapshot.

        Returns:
            The appropriate CognitiveLevel for this request.
        """
        score = self._compute_complexity_score(event, context, memory_state)
        level = self._score_to_level(score)

        logger.info(
            "ComplexityRouter: event=%s score=%.2f -> %s",
            event, score, level.value,
        )
        return level

    def get_model_config(self, level: CognitiveLevel) -> ModelConfig:
        """Return LLM config for this cognitive level.

        Args:
            level: The cognitive level to get config for.

        Returns:
            ModelConfig with model, tokens, temperature, etc.
        """
        return _LEVEL_CONFIGS[level]

    def _compute_complexity_score(
        self,
        event: str,
        context: dict[str, Any],
        memory_state: MemoryState,
    ) -> float:
        """Compute a 0.0-1.0 complexity score from all signals.

        Args:
            event: Event type string.
            context: Request context dict.
            memory_state: Memory signals snapshot.

        Returns:
            Complexity score between 0.0 and 1.0.
        """
        score = 0.0

        for signal in COMPLEX_SIGNALS:
            if self._detect_signal(signal, event, context):
                score += SIGNAL_WEIGHTS[signal]

        score += self._memory_adjustment(memory_state)

        return min(1.0, score)

    def _memory_adjustment(self, memory_state: MemoryState) -> float:
        """Calculate additional complexity from memory signals.

        Args:
            memory_state: Current memory state snapshot.

        Returns:
            Additional complexity score (0.0-0.5).
        """
        adjustment = 0.0

        if memory_state.surprise_score > _SURPRISE_HIGH:
            adjustment += 0.3

        if memory_state.recent_confidence < _CONFIDENCE_LOW:
            adjustment += 0.2

        return adjustment

    @staticmethod
    def _score_to_level(score: float) -> CognitiveLevel:
        """Map a complexity score to a cognitive level.

        Args:
            score: Complexity score between 0.0 and 1.0.

        Returns:
            The matching CognitiveLevel.
        """
        if score < _L2_THRESHOLD:
            return CognitiveLevel.L1_INSTINCT
        if score < _L3_THRESHOLD:
            return CognitiveLevel.L2_SITUATION
        if score < _L4_THRESHOLD:
            return CognitiveLevel.L3_EXPERIENCE
        return CognitiveLevel.L4_STRATEGY

    @staticmethod
    def _detect_signal(
        signal: str,
        event: str,
        context: dict[str, Any],
    ) -> bool:
        """Check whether a specific complex signal is present.

        Args:
            signal: Signal name from COMPLEX_SIGNALS.
            event: Event type string.
            context: Request context dict.

        Returns:
            True if the signal is detected.
        """
        return _SIGNAL_DETECTORS.get(signal, _default_detector)(event, context)


# ── Signal detector functions ─────────────────────────────────────────── #


def _detect_schedule_conflict(event: str, ctx: dict[str, Any]) -> bool:
    return event == "schedule_conflict" or ctx.get("has_overlap", False)


def _detect_guest_complaint(event: str, ctx: dict[str, Any]) -> bool:
    sentiment = ctx.get("sentiment", "")
    return event == "guest_complaint" or sentiment == "negative"


def _detect_cleaner_no_reply(event: str, ctx: dict[str, Any]) -> bool:
    return ctx.get("cleaner_no_reply_count", 0) >= 2


def _detect_owner_rejected(event: str, ctx: dict[str, Any]) -> bool:
    return event == "owner_rejected" or ctx.get("owner_rejected", False)


def _detect_damage(event: str, ctx: dict[str, Any]) -> bool:
    return event == "damage_detected" or ctx.get("damage_detected", False)


def _detect_last_minute(event: str, ctx: dict[str, Any]) -> bool:
    hours_to_checkin = ctx.get("hours_to_checkin", 999)
    return hours_to_checkin < 4


def _detect_vip_guest(event: str, ctx: dict[str, Any]) -> bool:
    return ctx.get("guest_score", 0) > 80


def _detect_new_city(event: str, ctx: dict[str, Any]) -> bool:
    return ctx.get("city_maturity", "mature") == "new"


def _detect_cost_above_threshold(event: str, ctx: dict[str, Any]) -> bool:
    limit = ctx.get("cost_limit", 500)
    return ctx.get("vendor_quote", 0) > limit


def _detect_multi_issue(event: str, ctx: dict[str, Any]) -> bool:
    return ctx.get("issue_count", 0) >= 2


def _detect_ambiguous_reply(event: str, ctx: dict[str, Any]) -> bool:
    return ctx.get("reply_clarity", "clear") == "ambiguous"


def _detect_recurring_issue(event: str, ctx: dict[str, Any]) -> bool:
    return ctx.get("recurring_issue", False)


def _default_detector(event: str, ctx: dict[str, Any]) -> bool:
    return False


_SIGNAL_DETECTORS: dict[str, Any] = {
    "schedule_conflict": _detect_schedule_conflict,
    "guest_complaint": _detect_guest_complaint,
    "cleaner_no_reply_2x": _detect_cleaner_no_reply,
    "owner_rejected": _detect_owner_rejected,
    "damage_detected": _detect_damage,
    "last_minute_booking": _detect_last_minute,
    "vip_guest": _detect_vip_guest,
    "new_city_no_data": _detect_new_city,
    "cost_above_threshold": _detect_cost_above_threshold,
    "multi_issue": _detect_multi_issue,
    "ambiguous_reply": _detect_ambiguous_reply,
    "recurring_issue": _detect_recurring_issue,
}
