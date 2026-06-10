"""Eval Runner — batch evaluation execution and reporting.

Runs multiple evaluators across datasets and aggregates results.
Supports parallel execution and structured reporting. Integrates
with the existing APMGrader for outcome scoring.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.evaluation.protocol import EvalResult, Evaluator

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """A single evaluation test case.

    Attributes:
        input_text: The input/question.
        output_text: The agent's response.
        reference: Expected/reference answer.
        metadata: Additional context for evaluators.
    """

    input_text: str
    output_text: str
    reference: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    """Aggregated evaluation report.

    Attributes:
        total_cases: Number of cases evaluated.
        avg_score: Average score across all evaluations.
        pass_rate: Percentage of cases that passed.
        results: Per-case results.
        evaluator_scores: Per-evaluator average scores.
    """

    total_cases: int = 0
    avg_score: float = 0.0
    pass_rate: float = 0.0
    results: list[dict[str, Any]] = field(default_factory=list)
    evaluator_scores: dict[str, float] = field(default_factory=dict)


class EvalRunner:
    """Batch evaluation runner with multi-evaluator support.

    Runs all registered evaluators across all provided cases and
    produces an aggregated report.

    Args:
        evaluators: List of evaluator instances.
        parallel: Whether to run evaluations in parallel.
    """

    def __init__(
        self,
        evaluators: list[Any],
        parallel: bool = True,
    ) -> None:
        self._evaluators = evaluators
        self._parallel = parallel

    @property
    def evaluator_names(self) -> list[str]:
        """Names of registered evaluators."""
        return [e.name for e in self._evaluators]

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        """Run all evaluators across all cases.

        Args:
            cases: List of evaluation cases.

        Returns:
            Aggregated EvalReport.
        """
        all_results: list[dict[str, Any]] = []

        for case in cases:
            case_results = await self._evaluate_case(case)
            all_results.append(case_results)

        return _build_report(all_results, self._evaluators)

    async def run_single(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> list[EvalResult]:
        """Run all evaluators on a single input/output pair.

        Args:
            input_text: Input text.
            output_text: Output text.
            reference: Reference answer.
            **kwargs: Additional evaluator context.

        Returns:
            List of EvalResults (one per evaluator).
        """
        if self._parallel:
            return await self._run_evaluators_parallel(
                input_text, output_text, reference, kwargs,
            )
        return await self._run_evaluators_sequential(
            input_text, output_text, reference, kwargs,
        )

    # ── Internal ──────────────────────────────────────────────────────

    async def _evaluate_case(
        self, case: EvalCase,
    ) -> dict[str, Any]:
        """Evaluate a single case across all evaluators.

        Args:
            case: The evaluation case.

        Returns:
            Dict with case data and evaluator results.
        """
        results = await self.run_single(
            case.input_text, case.output_text, case.reference,
            **case.metadata,
        )
        return {
            "input": case.input_text[:100],
            "output": case.output_text[:100],
            "results": results,
        }

    async def _run_evaluators_parallel(
        self,
        input_text: str,
        output_text: str,
        reference: str,
        kwargs: dict[str, Any],
    ) -> list[EvalResult]:
        """Run evaluators in parallel via asyncio.gather.

        Args:
            input_text: Input text.
            output_text: Output text.
            reference: Reference answer.
            kwargs: Extra context.

        Returns:
            List of EvalResults.
        """
        tasks = [
            _safe_evaluate(e, input_text, output_text, reference, kwargs)
            for e in self._evaluators
        ]
        return list(await asyncio.gather(*tasks))

    async def _run_evaluators_sequential(
        self,
        input_text: str,
        output_text: str,
        reference: str,
        kwargs: dict[str, Any],
    ) -> list[EvalResult]:
        """Run evaluators sequentially.

        Args:
            input_text: Input text.
            output_text: Output text.
            reference: Reference answer.
            kwargs: Extra context.

        Returns:
            List of EvalResults.
        """
        results: list[EvalResult] = []
        for evaluator in self._evaluators:
            result = await _safe_evaluate(
                evaluator, input_text, output_text, reference, kwargs,
            )
            results.append(result)
        return results


# ── Helpers ───────────────────────────────────────────────────────── #


async def _safe_evaluate(
    evaluator: Any,
    input_text: str,
    output_text: str,
    reference: str,
    kwargs: dict[str, Any],
) -> EvalResult:
    """Safely run an evaluator, catching exceptions.

    Args:
        evaluator: Evaluator instance.
        input_text: Input text.
        output_text: Output text.
        reference: Reference answer.
        kwargs: Extra context.

    Returns:
        EvalResult (error result on failure).
    """
    try:
        return await evaluator.evaluate(
            input_text, output_text, reference, **kwargs,
        )
    except Exception as exc:
        logger.warning("Evaluator %s failed: %s", evaluator.name, exc)
        return EvalResult(
            reasoning=f"Evaluator error: {exc}",
            criteria=evaluator.name,
        )


def _build_report(
    all_results: list[dict[str, Any]],
    evaluators: list[Any],
) -> EvalReport:
    """Build an aggregated report from per-case results.

    Args:
        all_results: List of per-case result dicts.
        evaluators: Evaluator instances for naming.

    Returns:
        Aggregated EvalReport.
    """
    all_scores: list[float] = []
    all_passed: list[bool] = []
    evaluator_totals: dict[str, list[float]] = {
        e.name: [] for e in evaluators
    }

    for case_data in all_results:
        for result in case_data.get("results", []):
            all_scores.append(result.score)
            all_passed.append(result.passed)
            if result.criteria in evaluator_totals:
                evaluator_totals[result.criteria].append(result.score)

    evaluator_avgs = {
        name: _safe_avg(scores)
        for name, scores in evaluator_totals.items()
    }

    return EvalReport(
        total_cases=len(all_results),
        avg_score=_safe_avg(all_scores),
        pass_rate=_safe_avg([1.0 if p else 0.0 for p in all_passed]),
        results=all_results,
        evaluator_scores=evaluator_avgs,
    )


def _safe_avg(values: list[float]) -> float:
    """Calculate average, returning 0.0 for empty lists.

    Args:
        values: Numeric values.

    Returns:
        Average or 0.0.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)
