"""MIRA Auto-Approver — AI decision layer для knowledge candidates.

MIRA (Machine Intelligence Review & Approval) оценивает knowledge
candidates, пришедшие из Cendra, и принимает решение: auto-approve
или отложить для ручного review PM-ом.

Логика ``evaluate()`` — **чистая функция** (без side-effects):

1. ``source == "PM_Correction"`` → **всегда approve** (confidence override).
2. Есть хотя бы один заблокированный red_flag → **skip** (требует PM).
3. ``confidence >= CONFIDENCE_THRESHOLD`` (0.75) → **approve**.
4. Иначе → **skip**.

Blocked flags (чувствительные темы, которые AI не должен одобрять):
- ``access_code`` — дверные коды, замки, пароли доступа.
- ``price_or_fee`` — цены, комиссии, возвраты.
- ``vague_or_unc`` — нечёткий или неуверенный ответ.
- ``might_be_que`` — возможно вопрос, а не утверждение.

Пример использования::

    approver = MIRAAutoApprover()
    result = await approver.process_candidates(candidates_dicts)
    # result.mcp_actions → [MCPAction(approveKnowledgeCandidate, ...)]
    # result.approved     → [candidate_dict, ...]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger(__name__)

# ── Константы ─────────────────────────────────────────────────────── #

CONFIDENCE_THRESHOLD: Final[float] = 0.75

# Флаги, при наличии которых MIRA НЕ одобряет кандидата автоматически.
BLOCKED_FLAGS: Final[frozenset[str]] = frozenset({
    "access_code",
    "price_or_fee",
    "vague_or_unc",
    "might_be_que",
})

# Source, при котором кандидат одобряется безусловно.
SOURCE_PM_CORRECTION: Final[str] = "PM_Correction"


# ── Value Objects ─────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class MIRADecision:
    """Решение MIRA по одному кандидату.

    Attributes:
        should_approve: ``True`` если кандидат одобрен.
        reason: Причина одобрения (для логирования).
        skip_reason: Причина отклонения / отложения (если не одобрен).
    """

    should_approve: bool
    reason: str = ""
    skip_reason: str = ""


@dataclass(slots=True)
class MIRAResult:
    """Агрегированный результат прогона MIRA по пачке кандидатов.

    Attributes:
        approved: Одобренные кандидаты (dict-проекции).
        skipped: Отложенные для PM review кандидаты.
        errors: Ошибки обработки отдельных кандидатов.
        mcp_actions: Список MCP-действий ``approveKnowledgeCandidate``
            для одобренных кандидатов.
    """

    approved: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mcp_actions: list[Any] = field(default_factory=list)

    @property
    def approved_count(self) -> int:
        return len(self.approved)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    def to_dict(self) -> dict[str, Any]:
        """Сериализация для JSON-ответа и логов."""
        return {
            "approved_count": self.approved_count,
            "skipped_count": self.skipped_count,
            "error_count": len(self.errors),
            "approved": self.approved,
            "skipped": self.skipped,
            "errors": self.errors,
        }


# ── Основной класс ───────────────────────────────────────────────── #


class MIRAAutoApprover:
    """AI decision layer над knowledge candidates.

    Все публичные методы stateless и thread-safe.
    """

    # ── Чистая оценка (без side-effects) ────────────────────────── #

    def evaluate(self, candidate: dict[str, Any]) -> MIRADecision:
        """Оценить одного кандидата — чистая функция.

        Args:
            candidate: dict с ключами ``source``, ``confidence``,
                ``red_flags`` (str), ``answer``, ``candidate_id``.

        Returns:
            :class:`MIRADecision` с результатом оценки.
        """
        source = candidate.get("source", "")
        confidence = float(candidate.get("confidence", 0.0))
        flags = parse_red_flags(candidate.get("red_flags", ""))
        answer = candidate.get("answer", "")

        # Правило 1: PM_Correction → безусловный approve.
        if source == SOURCE_PM_CORRECTION:
            return MIRADecision(
                should_approve=True,
                reason="PM_Correction: auto-approved (confidence override)",
            )

        # Правило 2: заблокированный red_flag → skip.
        blocked = flags & BLOCKED_FLAGS
        if blocked:
            return MIRADecision(
                should_approve=False,
                skip_reason=f"blocked red_flags: {sorted(blocked)}",
            )

        # Правило 3: пустой ответ → нечего одобрять.
        if not answer or not answer.strip():
            return MIRADecision(
                should_approve=False,
                skip_reason="empty answer",
            )

        # Правило 4: confidence >= порог → approve.
        if confidence >= CONFIDENCE_THRESHOLD:
            return MIRADecision(
                should_approve=True,
                reason=f"confidence {confidence:.2f} >= {CONFIDENCE_THRESHOLD}",
            )

        # Иначе → skip, ждём PM review.
        return MIRADecision(
            should_approve=False,
            skip_reason=f"low confidence {confidence:.2f} < {CONFIDENCE_THRESHOLD}",
        )

    # ── Batch-обработка ─────────────────────────────────────────── #

    async def process_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        semantic_memory: Any | None = None,
        dry_run: bool = False,
    ) -> MIRAResult:
        """Обработать пачку кандидатов и сгенерировать MCP-действия.

        Args:
            candidates: Список dict-проекций кандидатов.
            semantic_memory: Опциональный :class:`SemanticMemory` — если
                передан и ``dry_run=False``, одобренные кандидаты будут
                дополнительно записаны в Qdrant.
            dry_run: Если ``True``, MCP-действия генерируются, но в
                SemanticMemory ничего не пишется.

        Returns:
            :class:`MIRAResult` с одобренными, отложенными, ошибками и
            MCP-действиями.
        """
        result = MIRAResult()

        for candidate in candidates:
            cid = candidate.get("candidate_id", "?")
            try:
                decision = self.evaluate(candidate)
            except Exception as exc:  # noqa: BLE001 — один кандидат не роняет batch
                result.errors.append(f"{cid}: {exc}")
                continue

            if not decision.should_approve:
                result.skipped.append(candidate)
                continue

            result.approved.append(candidate)
            mcp = self._build_approve_action(candidate)
            result.mcp_actions.append(mcp)

            # Опциональная запись в SemanticMemory.
            if semantic_memory and not dry_run:
                await self._store_to_semantic(candidate, semantic_memory)

        logger.info(
            "MIRA: approved=%d, skipped=%d, errors=%d, dry_run=%s",
            result.approved_count, result.skipped_count,
            len(result.errors), dry_run,
        )
        return result

    # ── Preview (sync) ──────────────────────────────────────────── #

    def preview_stats(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Синхронный preview: сколько кандидатов будет одобрено/отложено.

        Args:
            candidates: Список dict-проекций кандидатов.

        Returns:
            dict с ключами ``total``, ``would_approve``, ``would_skip``,
            ``by_skip_reason``.
        """
        would_approve = 0
        would_skip = 0
        by_reason: dict[str, int] = {}

        for candidate in candidates:
            decision = self.evaluate(candidate)
            if decision.should_approve:
                would_approve += 1
            else:
                would_skip += 1
                reason = decision.skip_reason or "unknown"
                by_reason[reason] = by_reason.get(reason, 0) + 1

        return {
            "total": len(candidates),
            "would_approve": would_approve,
            "would_skip": would_skip,
            "by_skip_reason": by_reason,
        }

    # ── Внутренние helpers ──────────────────────────────────────── #

    @staticmethod
    def _build_approve_action(candidate: dict[str, Any]) -> Any:
        """Создать MCP-действие ``approveKnowledgeCandidate``.

        Args:
            candidate: dict-проекция кандидата.

        Returns:
            :class:`~brain_engine.api.mcp_tools.MCPToolCall`.
        """
        from brain_engine.api.mcp_tools import MCPToolFormatter

        return MCPToolFormatter.approve_knowledge_candidate(
            candidate_id=candidate.get("candidate_id", ""),
            approved=True,
            edited_content=candidate.get("answer", ""),
        )

    @staticmethod
    async def _store_to_semantic(
        candidate: dict[str, Any],
        semantic: Any,
    ) -> None:
        """Записать одобренного кандидата в SemanticMemory (Qdrant).

        Args:
            candidate: dict-проекция кандидата.
            semantic: :class:`~brain_engine.memory.semantic_memory.SemanticMemory`.
        """
        import uuid as _uuid

        text = f"Q: {candidate.get('question', '')}\nA: {candidate.get('answer', '')}"
        metadata = {
            "source": "mira_approved",
            "candidate_id": candidate.get("candidate_id", ""),
            "property_id": candidate.get("property_id", ""),
            "confidence": candidate.get("confidence", 0.0),
        }
        record_id = str(_uuid.uuid5(
            _uuid.NAMESPACE_URL,
            f"mira_{candidate.get('candidate_id', '')}",
        ))

        try:
            await semantic.store(text=text, metadata=metadata, record_id=record_id)
        except Exception as exc:  # noqa: BLE001 — запись в Qdrant не роняет pipeline
            logger.warning(
                "MIRA: failed to store candidate %s to semantic: %s",
                candidate.get("candidate_id"), exc,
            )


# ── Утилиты ──────────────────────────────────────────────────────── #


def parse_red_flags(raw: str | list[str] | None) -> frozenset[str]:
    """Распарсить red_flags из Cendra в frozenset строк.

    Cendra хранит ``RedFlags`` как STRING (не list). Возможные форматы:
    - JSON-массив: ``'["access_code","price_or_fee"]'``
    - CSV: ``'access_code,price_or_fee'``
    - Одиночный флаг: ``'access_code'``
    - Пустая строка или None: без флагов.
    - Уже готовый list[str] (для удобства тестов).

    Args:
        raw: Сырое значение red_flags.

    Returns:
        frozenset нормализованных (lowercase, stripped) флагов.
    """
    if not raw:
        return frozenset()
    if isinstance(raw, list):
        return _normalize_flags(raw)

    raw_str = raw.strip()
    if not raw_str:
        return frozenset()

    tokens = _try_json_array(raw_str) or raw_str.split(",")
    return _normalize_flags(tokens)


def _try_json_array(raw_str: str) -> list[str] | None:
    """Попробовать распарсить строку как JSON-массив строк.

    Returns:
        Список строк или ``None`` если не JSON-массив.
    """
    if not raw_str.startswith("["):
        return None
    try:
        parsed = json.loads(raw_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, list):
        return [f for f in parsed if isinstance(f, str)]
    return None


def _normalize_flags(items: list[str] | list[Any]) -> frozenset[str]:
    """Нормализовать список флагов: lowercase, strip, убрать пустые."""
    return frozenset(
        f.strip().lower()
        for f in items
        if isinstance(f, str) and f.strip()
    )
