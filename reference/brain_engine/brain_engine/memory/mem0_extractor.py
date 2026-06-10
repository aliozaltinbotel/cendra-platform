"""Сервис извлечения фактов из диалогов на основе mem0ai.

Оборачивает библиотеку ``mem0ai`` для автоматического извлечения фактов
из разговоров и хранения их в векторном хранилище Qdrant.

Все вызовы mem0 обёрнуты защитным try/except: если зависимость не
установлена, хранилище недоступно или произошла ошибка, методы возвращают
пустые структуры без выбрасывания исключений наружу.

Пример использования::

    service = Mem0ExtractorService(
        qdrant_url="http://localhost:6333",
        qdrant_collection="mem0_facts",
        llm_model="gpt-4o-mini",
    )
    facts = await service.extract_facts(
        conversation=[{"role": "user", "content": "Я предпочитаю тихий номер"}],
        user_id="guest_42",
    )
    result = await service.update_memory(user_id="guest_42", facts=facts)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

logger = logging.getLogger(__name__)

# Типы фактов, которые mem0 может извлечь из диалога.
FACT_TYPES: Final[frozenset[str]] = frozenset(
    {"preference", "rule", "info", "incident"},
)
# Тип по умолчанию, если mem0 не указал категорию.
DEFAULT_FACT_TYPE: Final[str] = "info"


# ── Объекты-значения ─────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    """Один извлечённый факт из разговора.

    Attributes:
        fact_id: Уникальный идентификатор факта (UUID).
        content: Текстовое содержимое факта.
        fact_type: Категория факта — ``preference``, ``rule``,
            ``info`` или ``incident``.
        entity_id: Идентификатор сущности, к которой относится факт
            (гость, объект, бронирование).
        confidence: Уровень уверенности в диапазоне ``[0.0, 1.0]``.
        source: Идентификатор источника (например, episode_id).
        extracted_at: ISO-timestamp момента извлечения.
        keywords: Набор ключевых слов, ассоциированных с фактом.
    """

    fact_id: str
    content: str
    fact_type: str
    entity_id: str = ""
    confidence: float = 1.0
    source: str = ""
    extracted_at: str = ""
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryUpdateResult:
    """Результат обновления памяти через mem0.

    Attributes:
        added: Количество добавленных фактов.
        updated: Количество обновлённых фактов.
        deleted: Количество удалённых фактов.
        unchanged: Количество неизменённых фактов.
        facts: Кортеж фактов, затронутых обновлением.
    """

    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    facts: tuple[ExtractedFact, ...] = ()


# ── Основной сервис ──────────────────────────────────────────────────── #


class Mem0ExtractorService:
    """Защитная обёртка над ``mem0ai`` для извлечения фактов из диалогов.

    Все внешние вызовы mem0 завёрнуты так, что недоступная зависимость
    или сломанное соединение с Qdrant не выбрасываются наружу. Методы
    чтения при недоступности возвращают пустые списки, методы записи —
    нулевой ``MemoryUpdateResult``.

    Args:
        qdrant_url: URL-адрес сервера Qdrant.
        qdrant_collection: Имя коллекции в Qdrant для хранения фактов.
        redis_url: URL-адрес Redis (используется mem0 для
            вспомогательного кэширования).
        llm_model: Модель LLM для извлечения фактов.
    """

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        qdrant_collection: str = "mem0_facts",
        redis_url: str = "redis://localhost:6379",
        llm_model: str = "gpt-4o-mini",
    ) -> None:
        self._qdrant_url = qdrant_url
        self._qdrant_collection = qdrant_collection
        self._redis_url = redis_url
        self._llm_model = llm_model
        self._memory: Any | None = None
        self._available: bool = False

        self._init_memory()

    # ── Инициализация ────────────────────────────────────────────────── #

    def _init_memory(self) -> None:
        """Ленивая инициализация клиента mem0.

        При отсутствии ``mem0ai`` или ошибке подключения к Qdrant
        сервис переходит в режим постоянного no-op.
        """
        try:
            from mem0 import Memory  # type: ignore[import-not-found]

            config: dict[str, Any] = {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "url": self._qdrant_url,
                        "collection_name": self._qdrant_collection,
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": self._llm_model,
                    },
                },
            }
            self._memory = Memory.from_config(config)
            self._available = True
            logger.info(
                "Mem0ExtractorService ready (qdrant=%s, collection=%s).",
                self._qdrant_url,
                self._qdrant_collection,
            )
        except ImportError:
            logger.warning(
                "mem0ai is not installed — "
                "Mem0ExtractorService will return empty results.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Mem0ExtractorService initialization failed (%s): %s",
                type(exc).__name__,
                exc,
            )

    def is_available(self) -> bool:
        """Возвращает ``True``, если mem0 инициализирован и готов."""
        return self._available and self._memory is not None

    # ── Извлечение фактов ────────────────────────────────────────────── #

    async def extract_facts(
        self,
        conversation: list[dict[str, str]],
        user_id: str = "",
    ) -> list[ExtractedFact]:
        """Извлечь факты из истории разговора через mem0.

        Принимает список сообщений формата
        ``[{"role": "user", "content": "..."}]`` и через LLM-пайплайн
        mem0 превращает их в набор фактов.

        Args:
            conversation: История диалога — список словарей с ключами
                ``role`` и ``content``.
            user_id: Идентификатор пользователя для привязки фактов.

        Returns:
            Список извлечённых фактов. При ошибке — пустой список.
        """
        if not self.is_available() or not conversation:
            return []

        try:
            # mem0 Memory.add — синхронный вызов; выносим в executor,
            # чтобы не блокировать event-loop.
            loop = asyncio.get_running_loop()
            raw_result: Any = await loop.run_in_executor(
                None,
                self._add_to_mem0,
                conversation,
                user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Mem0ExtractorService.extract_facts failed (%s): %s",
                type(exc).__name__,
                exc,
            )
            return []

        return self._parse_mem0_results(raw_result, source="extract")

    def _add_to_mem0(
        self,
        messages: list[dict[str, str]],
        user_id: str,
    ) -> Any:
        """Синхронная обёртка над ``Memory.add`` для вызова в executor.

        Args:
            messages: Список сообщений разговора.
            user_id: Идентификатор пользователя.

        Returns:
            Сырой результат mem0.
        """
        kwargs: dict[str, Any] = {}
        if user_id:
            kwargs["user_id"] = user_id
        return self._memory.add(messages, **kwargs)  # type: ignore[union-attr]

    # ── Обновление памяти ────────────────────────────────────────────── #

    async def update_memory(
        self,
        user_id: str,
        facts: list[ExtractedFact],
    ) -> MemoryUpdateResult:
        """Сохранить факты в памяти mem0 для указанного пользователя.

        Каждый факт передаётся как отдельное «сообщение» в ``Memory.add``,
        чтобы mem0 мог дедуплицировать и обновить существующие записи.

        Args:
            user_id: Идентификатор пользователя.
            facts: Список фактов для сохранения.

        Returns:
            Результат обновления с подсчётом добавленных/обновлённых
            записей. При ошибке — нулевой ``MemoryUpdateResult``.
        """
        if not self.is_available() or not facts or not user_id:
            return MemoryUpdateResult()

        added = 0
        updated = 0
        stored_facts: list[ExtractedFact] = []

        for fact in facts:
            messages = [{"role": "user", "content": fact.content}]
            try:
                loop = asyncio.get_running_loop()
                raw: Any = await loop.run_in_executor(
                    None,
                    self._add_to_mem0,
                    messages,
                    user_id,
                )
                # mem0 возвращает dict с ключами "results" / "relations".
                # Каждый элемент results содержит "event" — "ADD" или "UPDATE".
                results = (
                    raw.get("results", []) if isinstance(raw, dict) else []
                )
                for entry in results:
                    event = entry.get("event", "").upper()
                    if event == "ADD":
                        added += 1
                    elif event == "UPDATE":
                        updated += 1
                stored_facts.append(fact)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Mem0ExtractorService.update_memory failed "
                    "for fact %s (%s): %s",
                    fact.fact_id,
                    type(exc).__name__,
                    exc,
                )

        unchanged = len(facts) - added - updated
        result = MemoryUpdateResult(
            added=added,
            updated=updated,
            deleted=0,
            unchanged=max(unchanged, 0),
            facts=tuple(stored_facts),
        )

        logger.debug(
            "Mem0 memory updated for user=%s "
            "(added=%d, updated=%d, unchanged=%d).",
            user_id,
            result.added,
            result.updated,
            result.unchanged,
        )
        return result

    # ── Поиск фактов ────────────────────────────────────────────────── #

    async def search_facts(
        self,
        query: str,
        user_id: str = "",
        top_k: int = 5,
        *,
        decay_halflife_days: float = 0.0,
    ) -> list[ExtractedFact]:
        """Найти релевантные факты в памяти mem0.

        Args:
            query: Текстовый поисковый запрос.
            user_id: Идентификатор пользователя для фильтрации.
            top_k: Максимальное количество возвращаемых результатов.
            decay_halflife_days: Sprint-5 recency decay multiplier.
                When > 0 each result's ``confidence`` is multiplied
                by ``exp(-ln(2) * age_days / halflife_days)`` and
                the list is re-sorted by post-decay confidence so
                older facts rank lower than fresh ones with the
                same semantic similarity.  Default ``0.0`` keeps
                the pre-Sprint-5 raw-score behaviour — opt-in
                only.  Recommended starting value for PM
                preference data is 30 (months-scale freshness).

        Returns:
            Список найденных фактов, отсортированных по релевантности
            (с учётом decay при ``decay_halflife_days > 0``).  При
            ошибке — пустой список.
        """
        if not self.is_available() or not query.strip():
            return []

        try:
            loop = asyncio.get_running_loop()
            raw: Any = await loop.run_in_executor(
                None,
                self._search_mem0,
                query,
                user_id,
                top_k,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Mem0ExtractorService.search_facts failed (%s): %s",
                type(exc).__name__,
                exc,
            )
            return []

        facts = self._parse_mem0_results(raw, source="search")
        if decay_halflife_days > 0 and facts:
            from brain_engine.memory.recency_decay import (
                apply_recency_decay,
            )

            facts = apply_recency_decay(
                facts, halflife_days=decay_halflife_days,
            )
        return facts

    def _search_mem0(
        self,
        query: str,
        user_id: str,
        top_k: int,
    ) -> Any:
        """Синхронная обёртка над ``Memory.search`` для executor-а.

        Args:
            query: Поисковый запрос.
            user_id: Идентификатор пользователя.
            top_k: Лимит результатов.

        Returns:
            Сырой результат mem0.
        """
        kwargs: dict[str, Any] = {"limit": top_k}
        if user_id:
            kwargs["user_id"] = user_id
        return self._memory.search(query, **kwargs)  # type: ignore[union-attr]

    # ── Внутренние утилиты ───────────────────────────────────────────── #

    @staticmethod
    def _parse_mem0_results(
        raw: Any,
        *,
        source: str = "",
    ) -> list[ExtractedFact]:
        """Преобразовать сырой ответ mem0 в список ``ExtractedFact``.

        mem0 может возвращать данные в нескольких форматах:
        - dict с ключом ``"results"`` (после ``add``)
        - list записей (после ``search`` / ``get_all``)

        Метод унифицирует оба формата.

        Args:
            raw: Сырой ответ от mem0.
            source: Метка источника для поля ``source`` факта.

        Returns:
            Список ``ExtractedFact``.
        """
        if raw is None:
            return []

        now_iso = datetime.now(timezone.utc).isoformat()
        entries: list[dict[str, Any]] = []

        if isinstance(raw, dict):
            # Формат ответа Memory.add: {"results": [...], ...}
            entries = raw.get("results", [])
        elif isinstance(raw, list):
            # Формат ответа Memory.search / Memory.get_all
            entries = raw
        else:
            logger.debug(
                "Unexpected mem0 response type: %s",
                type(raw).__name__,
            )
            return []

        facts: list[ExtractedFact] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            # mem0 хранит текст в поле "memory" или "text".
            content = (
                entry.get("memory", "")
                or entry.get("text", "")
                or entry.get("content", "")
            )
            if not content:
                continue

            # Определение типа факта из метаданных mem0.
            raw_type = str(
                entry.get("category", "")
                or entry.get("type", "")
                or DEFAULT_FACT_TYPE,
            ).lower()
            fact_type = (
                raw_type if raw_type in FACT_TYPES else DEFAULT_FACT_TYPE
            )

            # Уверенность из скора поиска или метаданных.
            score = entry.get("score", entry.get("confidence", 1.0))
            try:
                confidence = float(score)
            except (TypeError, ValueError):
                confidence = 1.0

            fact = ExtractedFact(
                fact_id=str(entry.get("id", uuid.uuid4())),
                content=content,
                fact_type=fact_type,
                entity_id=str(entry.get("user_id", "")),
                confidence=confidence,
                source=source,
                extracted_at=now_iso,
                keywords=tuple(entry.get("keywords", ())),
            )
            facts.append(fact)

        return facts
