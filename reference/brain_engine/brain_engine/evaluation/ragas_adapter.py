"""Адаптеры метрик RAGAS для протокола Evaluator.

Оборачивают метрики RAGAS (Faithfulness, ContextPrecision, ContextRecall)
в единый интерфейс Evaluator, возвращая стандартный EvalResult.
Импорт ragas — ленивый: если библиотека не установлена, адаптеры
возвращают EvalResult с score=0.0 и описанием ошибки.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from brain_engine.evaluation.protocol import EvalResult

logger = logging.getLogger(__name__)


# ── Faithfulness ─────────────────────────────────────────────────── #


class RAGASFaithfulnessAdapter:
    """Адаптер метрики RAGAS Faithfulness.

    Оценивает, насколько ответ обоснован полученным контекстом
    (retrieved documents). Требует ``contexts`` в kwargs.

    Args:
        llm_model: Идентификатор LLM для внутреннего использования RAGAS.
    """

    name: str = "ragas_faithfulness"

    def __init__(self, llm_model: str | None = None) -> None:
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Запуск метрики Faithfulness на одном примере.

        Args:
            input_text: Вопрос пользователя.
            output_text: Ответ модели.
            reference: Эталонный ответ (не используется).
            **kwargs: Должен содержать ``contexts`` — list[str].

        Returns:
            EvalResult со скором верности контексту.
        """
        contexts: list[str] | None = kwargs.get("contexts")
        if not contexts:
            return EvalResult(
                score=0.0,
                reasoning=(
                    "Missing 'contexts' in kwargs — "
                    "faithfulness requires retrieved documents"
                ),
                criteria=self.name,
            )

        return await _run_ragas_metric(
            metric_name="faithfulness",
            user_input=input_text,
            response=output_text,
            retrieved_contexts=contexts,
            reference=None,
            criteria=self.name,
            llm_model=self._llm_model,
        )


# ── Context Precision ────────────────────────────────────────────── #


class RAGASContextPrecisionAdapter:
    """Адаптер метрики RAGAS ContextPrecision.

    Оценивает, содержат ли извлечённые фрагменты релевантную
    информацию для ответа на вопрос (с учётом эталона).

    Args:
        llm_model: Идентификатор LLM для внутреннего использования RAGAS.
    """

    name: str = "ragas_context_precision"

    def __init__(self, llm_model: str | None = None) -> None:
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Запуск метрики ContextPrecision на одном примере.

        Args:
            input_text: Вопрос пользователя.
            output_text: Ответ модели.
            reference: Эталонный ответ (обязателен).
            **kwargs: Должен содержать ``contexts`` — list[str].

        Returns:
            EvalResult со скором точности контекста.
        """
        contexts: list[str] | None = kwargs.get("contexts")
        if not contexts:
            return EvalResult(
                score=0.0,
                reasoning=(
                    "Missing 'contexts' in kwargs — "
                    "context_precision requires retrieved documents"
                ),
                criteria=self.name,
            )
        if not reference:
            return EvalResult(
                score=0.0,
                reasoning=(
                    "Missing 'reference' — "
                    "context_precision requires ground truth"
                ),
                criteria=self.name,
            )

        return await _run_ragas_metric(
            metric_name="context_precision",
            user_input=input_text,
            response=output_text,
            retrieved_contexts=contexts,
            reference=reference,
            criteria=self.name,
            llm_model=self._llm_model,
        )


# ── Context Recall ───────────────────────────────────────────────── #


class RAGASContextRecallAdapter:
    """Адаптер метрики RAGAS ContextRecall.

    Оценивает, покрывают ли извлечённые документы все
    утверждения из эталонного ответа.

    Args:
        llm_model: Идентификатор LLM для внутреннего использования RAGAS.
    """

    name: str = "ragas_context_recall"

    def __init__(self, llm_model: str | None = None) -> None:
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Запуск метрики ContextRecall на одном примере.

        Args:
            input_text: Вопрос пользователя.
            output_text: Ответ модели.
            reference: Эталонный ответ (обязателен).
            **kwargs: Должен содержать ``contexts`` — list[str].

        Returns:
            EvalResult со скором полноты контекста.
        """
        contexts: list[str] | None = kwargs.get("contexts")
        if not contexts:
            return EvalResult(
                score=0.0,
                reasoning=(
                    "Missing 'contexts' in kwargs — "
                    "context_recall requires retrieved documents"
                ),
                criteria=self.name,
            )
        if not reference:
            return EvalResult(
                score=0.0,
                reasoning=(
                    "Missing 'reference' — "
                    "context_recall requires ground truth"
                ),
                criteria=self.name,
            )

        return await _run_ragas_metric(
            metric_name="context_recall",
            user_input=input_text,
            response=output_text,
            retrieved_contexts=contexts,
            reference=reference,
            criteria=self.name,
            llm_model=self._llm_model,
        )


# ── Общий хелпер запуска метрики ─────────────────────────────────── #


async def _run_ragas_metric(
    *,
    metric_name: str,
    user_input: str,
    response: str,
    retrieved_contexts: list[str],
    reference: str | None,
    criteria: str,
    llm_model: str | None,
) -> EvalResult:
    """Универсальный запуск одной RAGAS-метрики.

    Ленивый импорт ragas; при отсутствии пакета или ошибке —
    возвращает EvalResult с нулевым скором и описанием проблемы.

    Args:
        metric_name: Имя метрики (faithfulness / context_precision / context_recall).
        user_input: Вопрос пользователя.
        response: Ответ модели.
        retrieved_contexts: Список извлечённых документов.
        reference: Эталонный ответ (может быть None).
        criteria: Имя критерия для EvalResult.
        llm_model: Идентификатор LLM (если нужно переопределить).

    Returns:
        EvalResult со скором и пояснением.
    """
    try:
        # --- ленивый импорт ragas ---
        from ragas import SingleTurnSample
        from ragas.metrics import (
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError:
        logger.warning("ragas package is not installed")
        return EvalResult(
            score=0.0,
            reasoning="ragas package is not installed",
            criteria=criteria,
        )

    # --- карта метрик ---
    metrics_map = {
        "faithfulness": faithfulness,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
    metric = metrics_map.get(metric_name)
    if metric is None:
        return EvalResult(
            score=0.0,
            reasoning=f"Unknown RAGAS metric: {metric_name}",
            criteria=criteria,
        )

    # --- сборка Sample ---
    sample_kwargs: dict[str, Any] = {
        "user_input": user_input,
        "response": response,
        "retrieved_contexts": retrieved_contexts,
    }
    if reference is not None:
        sample_kwargs["reference"] = reference

    sample = SingleTurnSample(**sample_kwargs)

    # --- подмена LLM (если задана) ---
    if llm_model is not None:
        try:
            from ragas.llms import LangchainLLMWrapper

            metric.llm = LangchainLLMWrapper(llm_model)
        except Exception:
            logger.debug(
                "Could not set custom LLM for RAGAS metric %s",
                metric_name,
                exc_info=True,
            )

    # --- запуск; single_turn_ascore может быть sync — оборачиваем ---
    try:
        score: float = await asyncio.to_thread(
            _sync_score, metric, sample,
        )
    except Exception:
        logger.error(
            "RAGAS metric '%s' evaluation failed",
            metric_name,
            exc_info=True,
        )
        return EvalResult(
            score=0.0,
            reasoning=f"RAGAS metric '{metric_name}' raised an exception",
            criteria=criteria,
        )

    clamped = min(1.0, max(0.0, score))
    return EvalResult(
        score=clamped,
        value="Y" if clamped >= 0.5 else "N",
        reasoning=f"RAGAS {metric_name} score: {clamped:.4f}",
        criteria=criteria,
        metadata={"ragas_metric": metric_name, "raw_score": score},
    )


def _sync_score(metric: Any, sample: Any) -> float:
    """Синхронная обёртка над single_turn_ascore.

    RAGAS предоставляет ``single_turn_ascore`` как корутину,
    но иногда её удобнее запускать из синхронного контекста
    через ``asyncio.run``. Если метод уже синхронный — вызываем напрямую.

    Args:
        metric: Экземпляр RAGAS-метрики.
        sample: SingleTurnSample для оценки.

    Returns:
        Числовой скор (float).
    """
    import asyncio as _asyncio
    import inspect

    result = metric.single_turn_ascore(sample)
    if inspect.isawaitable(result):
        return float(_asyncio.run(result))
    return float(result)
