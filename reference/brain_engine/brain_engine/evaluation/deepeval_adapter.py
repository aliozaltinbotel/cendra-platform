"""Адаптеры DeepEval — обёртки метрик DeepEval для Evaluator Protocol.

Предоставляет четыре адаптера, каждый из которых оборачивает одну метрику
DeepEval (GEval, Faithfulness, Hallucination, AnswerRelevancy) в единый
интерфейс ``Evaluator``.  Импорт deepeval выполняется лениво внутри
методов ``evaluate``, чтобы модуль оставался лёгким при отсутствии
зависимости.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.evaluation.protocol import EvalResult

logger = logging.getLogger(__name__)


# ── GEval ────────────────────────────────────────────────────────── #


class DeepEvalGEvalAdapter:
    """Адаптер GEval — оценка по пользовательским критериям.

    Оборачивает ``deepeval.metrics.GEval`` для оценки ответа модели
    по произвольным критериям (например, для гостиничной индустрии).

    Args:
        criteria: Текст пользовательского критерия оценки.
        llm_model: Идентификатор LLM-модели для GEval.
    """

    name: str = "deepeval_geval"

    def __init__(
        self,
        criteria: str,
        llm_model: str = "gpt-4o",
    ) -> None:
        self._criteria = criteria
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Оценить ответ с помощью GEval.

        Args:
            input_text: Входной вопрос/запрос.
            output_text: Ответ модели.
            reference: Эталонный ответ (если есть).
            **kwargs: Дополнительный контекст.

        Returns:
            EvalResult с числовым баллом и обоснованием.
        """
        try:
            from deepeval.metrics import GEval
            from deepeval.test_case import LLMTestCase
        except ImportError:
            logger.error("deepeval is not installed")
            return EvalResult(
                score=0.0,
                reasoning="deepeval is not installed",
                criteria=self.name,
            )

        try:
            test_case = LLMTestCase(
                input=input_text,
                actual_output=output_text,
                expected_output=reference or None,
            )
            metric = GEval(
                name=self.name,
                criteria=self._criteria,
                model=self._llm_model,
            )
            metric.measure(test_case)

            score = float(metric.score or 0.0)
            return EvalResult(
                score=min(1.0, max(0.0, score)),
                value="Y" if score >= 0.5 else "N",
                reasoning=str(metric.reason or ""),
                criteria=self.name,
                metadata={"deepeval_metric": "GEval"},
            )
        except Exception:
            logger.error("GEval evaluation failed", exc_info=True)
            return EvalResult(
                score=0.0,
                reasoning="GEval evaluation failed",
                criteria=self.name,
            )


# ── Faithfulness ─────────────────────────────────────────────────── #


class DeepEvalFaithfulnessAdapter:
    """Адаптер Faithfulness — оценка верности ответа источникам.

    Оборачивает ``deepeval.metrics.FaithfulnessMetric`` для проверки,
    что ответ модели подкреплён контекстом из извлечённых документов.

    Args:
        llm_model: Идентификатор LLM-модели для метрики.
    """

    name: str = "deepeval_faithfulness"

    def __init__(self, llm_model: str = "gpt-4o") -> None:
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Оценить верность ответа контексту.

        Args:
            input_text: Входной вопрос/запрос.
            output_text: Ответ модели.
            reference: Эталонный ответ (не используется).
            **kwargs: Должен содержать ``retrieval_context`` — список
                строк извлечённых документов.

        Returns:
            EvalResult с баллом верности.
        """
        retrieval_context: list[str] = kwargs.get(
            "retrieval_context", [],
        )
        if not retrieval_context:
            return EvalResult(
                score=0.0,
                reasoning="retrieval_context is required but empty",
                criteria=self.name,
            )

        try:
            from deepeval.metrics import FaithfulnessMetric
            from deepeval.test_case import LLMTestCase
        except ImportError:
            logger.error("deepeval is not installed")
            return EvalResult(
                score=0.0,
                reasoning="deepeval is not installed",
                criteria=self.name,
            )

        try:
            test_case = LLMTestCase(
                input=input_text,
                actual_output=output_text,
                retrieval_context=retrieval_context,
            )
            metric = FaithfulnessMetric(model=self._llm_model)
            metric.measure(test_case)

            score = float(metric.score or 0.0)
            return EvalResult(
                score=min(1.0, max(0.0, score)),
                value="Y" if score >= 0.5 else "N",
                reasoning=str(metric.reason or ""),
                criteria=self.name,
                metadata={
                    "deepeval_metric": "FaithfulnessMetric",
                },
            )
        except Exception:
            logger.error(
                "Faithfulness evaluation failed", exc_info=True,
            )
            return EvalResult(
                score=0.0,
                reasoning="Faithfulness evaluation failed",
                criteria=self.name,
            )


# ── Hallucination ────────────────────────────────────────────────── #


class DeepEvalHallucinationAdapter:
    """Адаптер Hallucination — обнаружение галлюцинаций модели.

    Оборачивает ``deepeval.metrics.HallucinationMetric`` для проверки,
    что ответ модели не содержит вымышленных фактов относительно
    предоставленного контекста.

    Args:
        llm_model: Идентификатор LLM-модели для метрики.
    """

    name: str = "deepeval_hallucination"

    def __init__(self, llm_model: str = "gpt-4o") -> None:
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Оценить наличие галлюцинаций в ответе.

        Args:
            input_text: Входной вопрос/запрос.
            output_text: Ответ модели.
            reference: Эталонный ответ (не используется).
            **kwargs: Должен содержать ``context`` — список
                фактических утверждений для проверки.

        Returns:
            EvalResult с баллом по галлюцинациям.
        """
        context: list[str] = kwargs.get("context", [])
        if not context:
            return EvalResult(
                score=0.0,
                reasoning="context is required but empty",
                criteria=self.name,
            )

        try:
            from deepeval.metrics import HallucinationMetric
            from deepeval.test_case import LLMTestCase
        except ImportError:
            logger.error("deepeval is not installed")
            return EvalResult(
                score=0.0,
                reasoning="deepeval is not installed",
                criteria=self.name,
            )

        try:
            test_case = LLMTestCase(
                input=input_text,
                actual_output=output_text,
                context=context,
            )
            metric = HallucinationMetric(model=self._llm_model)
            metric.measure(test_case)

            score = float(metric.score or 0.0)
            return EvalResult(
                score=min(1.0, max(0.0, score)),
                value="Y" if score >= 0.5 else "N",
                reasoning=str(metric.reason or ""),
                criteria=self.name,
                metadata={
                    "deepeval_metric": "HallucinationMetric",
                },
            )
        except Exception:
            logger.error(
                "Hallucination evaluation failed", exc_info=True,
            )
            return EvalResult(
                score=0.0,
                reasoning="Hallucination evaluation failed",
                criteria=self.name,
            )


# ── Answer Relevancy ─────────────────────────────────────────────── #


class DeepEvalAnswerRelevancyAdapter:
    """Адаптер AnswerRelevancy — оценка релевантности ответа.

    Оборачивает ``deepeval.metrics.AnswerRelevancyMetric`` для проверки,
    что ответ модели соответствует заданному вопросу.

    Args:
        llm_model: Идентификатор LLM-модели для метрики.
    """

    name: str = "deepeval_answer_relevancy"

    def __init__(self, llm_model: str = "gpt-4o") -> None:
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Оценить релевантность ответа вопросу.

        Args:
            input_text: Входной вопрос/запрос.
            output_text: Ответ модели.
            reference: Эталонный ответ (не используется).
            **kwargs: Дополнительный контекст.

        Returns:
            EvalResult с баллом релевантности.
        """
        try:
            from deepeval.metrics import AnswerRelevancyMetric
            from deepeval.test_case import LLMTestCase
        except ImportError:
            logger.error("deepeval is not installed")
            return EvalResult(
                score=0.0,
                reasoning="deepeval is not installed",
                criteria=self.name,
            )

        try:
            test_case = LLMTestCase(
                input=input_text,
                actual_output=output_text,
            )
            metric = AnswerRelevancyMetric(model=self._llm_model)
            metric.measure(test_case)

            score = float(metric.score or 0.0)
            return EvalResult(
                score=min(1.0, max(0.0, score)),
                value="Y" if score >= 0.5 else "N",
                reasoning=str(metric.reason or ""),
                criteria=self.name,
                metadata={
                    "deepeval_metric": "AnswerRelevancyMetric",
                },
            )
        except Exception:
            logger.error(
                "AnswerRelevancy evaluation failed", exc_info=True,
            )
            return EvalResult(
                score=0.0,
                reasoning="AnswerRelevancy evaluation failed",
                criteria=self.name,
            )
