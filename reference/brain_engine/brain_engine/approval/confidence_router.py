"""Confidence-Based Approval Routing — трёхуровневая маршрутизация решений.

Заменяет бинарные списки AUTO_APPROVE / ALWAYS_REQUIRE на три уровня
уверенности, каждый из которых определяет своё поведение:

  HIGH   (>= 0.85) → автоматическое одобрение
  MEDIUM (0.50-0.85) → уведомление PM с пакетом доказательств (EvidencePack)
  LOW    (< 0.50) → эскалация с повышением urgency на 1

Пакет доказательств (EvidencePack) содержит: обоснование, confidence score,
релевантные записи из KB, и прошлые решения по аналогичным вопросам.

Использование:
    router = ConfidenceRouter()
    decision = router.route(
        confidence=0.72,
        action_type=ActionType.LATE_CHECKOUT,
        reasoning="Guest is VIP with 5 past stays",
        kb_entries=["Late checkout policy: up to 2 PM free for VIPs"],
    )
    # decision.tier == ConfidenceTier.MEDIUM
    # decision.evidence_pack содержит обоснование
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from brain_engine.approval.models import ActionType

logger = logging.getLogger(__name__)


# ── Конфигурация порогов ─────────────────────────────────────── #

_HIGH_THRESHOLD: Final[float] = 0.85
_MEDIUM_THRESHOLD: Final[float] = 0.50


class ConfidenceTier(StrEnum):
    """Уровень уверенности в решении.

    Определяет маршрут одобрения:
      HIGH   → auto-approve (без участия человека)
      MEDIUM → notify PM с EvidencePack
      LOW    → escalate с повышением urgency
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Пакет доказательств для PM при MEDIUM/LOW уверенности.

    Иммутабельный value object. Содержит всю информацию для PM,
    чтобы принять обоснованное решение.

    Attributes:
        reasoning: Объяснение AI, почему выбрано это решение.
        confidence: Числовая уверенность (0.0 — 1.0).
        tier: Вычисленный уровень уверенности.
        kb_entries: Релевантные записи из базы знаний.
        past_decisions: Прошлые решения по аналогичным вопросам.
        action_type: Тип предлагаемого действия.
        metadata: Дополнительные данные (ID гостя, сумма и т.д.).
    """

    reasoning: str
    confidence: float
    tier: ConfidenceTier
    kb_entries: tuple[str, ...] = ()
    past_decisions: tuple[str, ...] = ()
    action_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """Краткая сводка для отображения PM в уведомлении."""
        tier_labels: dict[ConfidenceTier, str] = {
            ConfidenceTier.HIGH: "AUTO-APPROVED",
            ConfidenceTier.MEDIUM: "NEEDS REVIEW",
            ConfidenceTier.LOW: "ESCALATED",
        }
        label = tier_labels.get(self.tier, "UNKNOWN")
        kb_count = len(self.kb_entries)
        past_count = len(self.past_decisions)
        return (
            f"[{label}] confidence={self.confidence:.0%} | "
            f"KB entries: {kb_count} | past decisions: {past_count}\n"
            f"Reasoning: {self.reasoning}"
        )


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Результат маршрутизации через ConfidenceRouter.

    Attributes:
        tier: Вычисленный уровень уверенности.
        auto_approve: Одобрить автоматически (True для HIGH).
        escalate: Эскалировать с повышением urgency (True для LOW).
        urgency_boost: На сколько повысить urgency при эскалации.
        evidence_pack: Пакет доказательств (для MEDIUM и LOW).
    """

    tier: ConfidenceTier
    auto_approve: bool
    escalate: bool
    urgency_boost: int = 0
    evidence_pack: EvidencePack | None = None


class ConfidenceRouter:
    """Маршрутизатор решений на основе уверенности.

    Определяет для каждого решения: авто-одобрение, ревью PM,
    или эскалация — на основе confidence score.

    Пороги настраиваемы. Для определённых ActionType
    авто-одобрение невозможно (ALWAYS_REQUIRE_APPROVAL).

    Args:
        high_threshold: Порог для HIGH (авто-одобрение). По умолчанию 0.85.
        medium_threshold: Порог для MEDIUM (ревью). По умолчанию 0.50.
        never_auto_approve: Типы действий, для которых авто-одобрение запрещено.
    """

    def __init__(
        self,
        high_threshold: float = _HIGH_THRESHOLD,
        medium_threshold: float = _MEDIUM_THRESHOLD,
        never_auto_approve: frozenset[ActionType] | None = None,
    ) -> None:
        if medium_threshold >= high_threshold:
            msg = (
                f"medium_threshold ({medium_threshold}) must be < "
                f"high_threshold ({high_threshold})"
            )
            raise ValueError(msg)

        self._high: Final[float] = high_threshold
        self._medium: Final[float] = medium_threshold
        self._never_auto: Final[frozenset[ActionType]] = never_auto_approve or frozenset({
            ActionType.SUBMIT_DAMAGE_CLAIM,
            ActionType.CHARGE_GUEST,
        })

    # ── Публичный API ────────────────────────────────────────── #

    def route(
        self,
        confidence: float,
        action_type: ActionType,
        reasoning: str = "",
        kb_entries: Sequence[str] = (),
        past_decisions: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        """Определить маршрут одобрения на основе уверенности.

        Args:
            confidence: Уверенность AI в решении (0.0 — 1.0).
            action_type: Тип предлагаемого действия.
            reasoning: Объяснение AI.
            kb_entries: Релевантные записи из базы знаний.
            past_decisions: Прошлые решения по аналогичным вопросам.
            metadata: Дополнительные данные.

        Returns:
            RoutingDecision с tier, auto_approve, escalate, evidence_pack.
        """
        # Нормализуем confidence в [0.0, 1.0]
        confidence = max(0.0, min(1.0, confidence))

        tier = self._compute_tier(confidence)

        evidence_pack = EvidencePack(
            reasoning=reasoning,
            confidence=confidence,
            tier=tier,
            kb_entries=tuple(kb_entries),
            past_decisions=tuple(past_decisions),
            action_type=action_type.value,
            metadata=metadata or {},
        )

        # HIGH → авто-одобрение (если action_type не запрещает)
        if tier == ConfidenceTier.HIGH:
            if action_type in self._never_auto:
                logger.info(
                    "Confidence HIGH (%.2f) but %s requires manual approval, "
                    "downgrading to MEDIUM",
                    confidence,
                    action_type.value,
                )
                return RoutingDecision(
                    tier=ConfidenceTier.MEDIUM,
                    auto_approve=False,
                    escalate=False,
                    evidence_pack=evidence_pack,
                )

            logger.info(
                "Confidence HIGH (%.2f) → auto-approve %s",
                confidence,
                action_type.value,
            )
            return RoutingDecision(
                tier=ConfidenceTier.HIGH,
                auto_approve=True,
                escalate=False,
                evidence_pack=evidence_pack,
            )

        # MEDIUM → уведомление PM с evidence pack
        if tier == ConfidenceTier.MEDIUM:
            logger.info(
                "Confidence MEDIUM (%.2f) → notify PM for %s",
                confidence,
                action_type.value,
            )
            return RoutingDecision(
                tier=ConfidenceTier.MEDIUM,
                auto_approve=False,
                escalate=False,
                evidence_pack=evidence_pack,
            )

        # LOW → эскалация с повышением urgency
        logger.warning(
            "Confidence LOW (%.2f) → escalate %s with urgency+1",
            confidence,
            action_type.value,
        )
        return RoutingDecision(
            tier=ConfidenceTier.LOW,
            auto_approve=False,
            escalate=True,
            urgency_boost=1,
            evidence_pack=evidence_pack,
        )

    def classify_tier(self, confidence: float) -> ConfidenceTier:
        """Определить уровень уверенности (без создания полного решения).

        Args:
            confidence: Уверенность AI (0.0 — 1.0).

        Returns:
            ConfidenceTier.
        """
        return self._compute_tier(max(0.0, min(1.0, confidence)))

    # ── Приватные методы ─────────────────────────────────────── #

    def _compute_tier(self, confidence: float) -> ConfidenceTier:
        """Вычислить tier на основе порогов."""
        if confidence >= self._high:
            return ConfidenceTier.HIGH
        if confidence >= self._medium:
            return ConfidenceTier.MEDIUM
        return ConfidenceTier.LOW

    def __repr__(self) -> str:
        return (
            f"ConfidenceRouter(high={self._high}, "
            f"medium={self._medium}, "
            f"never_auto={len(self._never_auto)} actions)"
        )
