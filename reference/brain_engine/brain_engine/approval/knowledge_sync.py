"""Сервис синхронизации знаний при PM-коррекции.

Когда PM редактирует ответ AI (``decision_type == "modified"``),
``KnowledgeSyncService`` превращает это исправление в
:class:`~brain_engine.api.mcp_tools.MCPToolCall` с tool
``addKnowledgeEntry`` — Cendra затем исполнит этот action и создаст
``KnowledgeCandidateModel`` с ``Source = "PM_Correction"`` и
``ConfidenceScore = 0.95``.

PM-коррекция — это высокоуровневый сигнал качества: если PM
правит ответ, значит AI ответил неправильно. Поэтому:
- confidence фиксирован на 0.95 (выше стандартного порога MIRA 0.75);
- source ``PM_Correction`` даёт MIRA право на auto-approve без
  дополнительных проверок (см. Фаза 3).
"""

from __future__ import annotations

import logging
from typing import Final

from brain_engine.api.mcp_tools import MCPToolCall, MCPToolFormatter

logger = logging.getLogger(__name__)

# Константы: уровень уверенности и тэг источника для PM-коррекций.
CONFIDENCE_PM_CORRECTION: Final[float] = 0.95
SOURCE_PM_CORRECTION: Final[str] = "PM_Correction"


class KnowledgeSyncService:
    """Сервис создания MCP-действий из PM-коррекций.

    Stateless — не хранит собственного состояния, вся персистенция
    делегируется Cendra через возвращённые :class:`MCPToolCall`.
    """

    def create_mcp_action_for_correction(
        self,
        *,
        guest_message: str,
        pm_answer: str,
        property_id: str = "",
        category: str = "PM_Correction",
    ) -> MCPToolCall | None:
        """Создать MCP-действие ``addKnowledgeEntry`` из PM-коррекции.

        Args:
            guest_message: Исходное сообщение гостя (вопрос).
            pm_answer: Ответ PM, заменивший ответ AI.
            property_id: ID property (Workspace GUID), scope для KB-записи.
            category: Категория записи в KB. По умолчанию
                ``"PM_Correction"``.

        Returns:
            :class:`MCPToolCall` с tool ``addKnowledgeEntry`` или ``None``,
            если входные данные пустые и action создавать нечего.
        """
        if not guest_message or not guest_message.strip():
            logger.debug("create_mcp_action_for_correction: пустой guest_message, пропускаем.")
            return None
        if not pm_answer or not pm_answer.strip():
            logger.debug("create_mcp_action_for_correction: пустой pm_answer, пропускаем.")
            return None

        # Формируем title и content для KB: вопрос → ответ.
        title = guest_message.strip()
        content = (
            f"Q: {guest_message.strip()}\n"
            f"A: {pm_answer.strip()}\n"
            f"Source: {SOURCE_PM_CORRECTION}\n"
            f"Confidence: {CONFIDENCE_PM_CORRECTION}"
        )

        action = MCPToolFormatter.add_knowledge_entry(
            title=title,
            content=content,
            property_id=property_id,
            category=category,
        )

        logger.info(
            "PM correction → addKnowledgeEntry (property=%s, title_len=%d).",
            property_id, len(title),
        )
        return action

    @staticmethod
    def log_correction(
        *,
        request_id: str,
        owner_id: str,
        property_id: str,
        guest_message: str,
        original_ai_response: str,
        pm_correction: str,
    ) -> None:
        """Записать аудит-лог PM-коррекции.

        Не бросает исключений — чисто fire-and-forget запись в лог,
        чтобы потом можно было grep-нуть ``pm_correction_audit`` при
        расследовании инцидентов.

        Args:
            request_id: ID approval-запроса.
            owner_id: Кто правил (PM / Owner).
            property_id: Property scope.
            guest_message: Что написал гость.
            original_ai_response: Что ответил AI.
            pm_correction: Чем PM заменил ответ.
        """
        logger.info(
            "pm_correction_audit request_id=%s owner=%s property=%s "
            "guest_len=%d ai_len=%d pm_len=%d",
            request_id, owner_id, property_id,
            len(guest_message), len(original_ai_response), len(pm_correction),
        )
