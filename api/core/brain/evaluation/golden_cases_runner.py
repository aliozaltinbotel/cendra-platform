"""Golden Cases — nightly LLM-as-judge evaluation of recent DecisionCases.

Samples DecisionCases from the last 24 hours, runs :class:`LLMJudge`
on each, aggregates pm_match_rate / hallucination_rate / avg_score,
and persists a per-run summary plus per-case verdicts via the
:class:`EvaluationResultStore` Protocol.

Designed to run as step 8 of the nightly consolidation cycle. The
entire path is gated by ``BRAIN_GOLDEN_CASES_ENABLED`` (default off)
so no LLM tokens are spent until a deploy explicitly opts in.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, runtime_checkable

from core.brain.evaluation.llm_judge import LLMJudge
from core.brain.evaluation.protocol import EvalResult
from core.brain.patterns.models import DecisionCase
from core.brain.patterns.store import DecisionCaseStore

logger = logging.getLogger(__name__)


_GOLDEN_CASES_ENV: Final[str] = "BRAIN_GOLDEN_CASES_ENABLED"


def golden_cases_enabled() -> bool:
    """Whether nightly LLM-as-judge evaluation is wired into step 8.

    Read on every nightly run so a deploy can flip the flag without
    restart. Default: off — no LLM tokens spent until explicitly
    enabled.
    """
    raw = os.environ.get(_GOLDEN_CASES_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class GoldenCasesReport:
    """Aggregated stats for one nightly evaluation run."""

    sample_size: int
    pm_match_rate: float
    hallucination_rate: float
    avg_score: float
    failed_cases: int
    duration_seconds: float


@runtime_checkable
class EvaluationResultStore(Protocol):
    """Storage for evaluation runs and per-case verdicts.

    Production impl persists to Postgres (tables ``evaluation_runs`` and
    ``evaluation_verdicts`` from migration ``025_evaluation_results.sql``).
    Tests use :class:`InMemoryEvaluationResultStore`.
    """

    def save_run(self, report: GoldenCasesReport) -> None:
        """Persist a per-run summary row."""
        ...

    def save_verdict(
        self,
        case_id: str,
        result: EvalResult,
        judge_model: str,
    ) -> None:
        """Persist a per-case judge verdict row."""
        ...


class InMemoryEvaluationResultStore:
    """Test-only in-memory implementation of :class:`EvaluationResultStore`."""

    def __init__(self) -> None:
        self.runs: list[GoldenCasesReport] = []
        self.verdicts: list[tuple[str, EvalResult, str]] = []

    def save_run(self, report: GoldenCasesReport) -> None:
        self.runs.append(report)

    def save_verdict(
        self,
        case_id: str,
        result: EvalResult,
        judge_model: str,
    ) -> None:
        self.verdicts.append((case_id, result, judge_model))


class GoldenCasesRunner:
    """Daily evaluation of recent DecisionCases via LLM-as-judge.

    Args:
        case_store: DecisionCaseStore to sample from.
        judge: LLMJudge instance for evaluation.
        result_store: Persistence for runs + verdicts.
        sample_size: Maximum cases to evaluate per run.
        judge_model: Model identifier persisted with each verdict
            (provenance for downstream analysis when the judge model
            is upgraded).
    """

    def __init__(
        self,
        case_store: DecisionCaseStore,
        judge: LLMJudge,
        result_store: EvaluationResultStore,
        sample_size: int = 500,
        judge_model: str = "gpt-4o-mini",
    ) -> None:
        self._case_store = case_store
        self._judge = judge
        self._result_store = result_store
        self._sample_size = sample_size
        self._judge_model = judge_model

    def run_daily(self) -> GoldenCasesReport:
        """Sample, evaluate, aggregate, persist."""
        start = datetime.now(UTC)
        since = start - timedelta(hours=24)

        cases = self._sample_recent(since=since)
        if not cases:
            logger.info("Golden cases: no recent cases to evaluate")
            return GoldenCasesReport(0, 0.0, 0.0, 0.0, 0, 0.0)

        scores: list[float] = []
        pm_matches = 0
        hallucinations = 0
        failed = 0

        for case in cases:
            try:
                result = self._judge_case(case)
                scores.append(result.score)
                if result.metadata.get("would_pm_agree"):
                    pm_matches += 1
                if result.metadata.get("hallucination_detected"):
                    hallucinations += 1
                self._result_store.save_verdict(
                    case.case_id,
                    result,
                    self._judge_model,
                )
            except Exception:
                failed += 1
                logger.exception(
                    "Golden cases: failed to evaluate %s",
                    case.case_id,
                )

        n = len(cases)
        duration = (datetime.now(UTC) - start).total_seconds()
        report = GoldenCasesReport(
            sample_size=n,
            pm_match_rate=pm_matches / n if n else 0.0,
            hallucination_rate=hallucinations / n if n else 0.0,
            avg_score=sum(scores) / len(scores) if scores else 0.0,
            failed_cases=failed,
            duration_seconds=duration,
        )
        self._result_store.save_run(report)
        logger.info("Golden cases run complete: %s", report)
        return report

    def _sample_recent(self, since: datetime) -> list[DecisionCase]:
        """Fetch recent live cases and filter to the last-24h window.

        ``DecisionCaseStore`` exposes ``search()`` (no time-window
        parameter); we over-fetch (``sample_size * 4``) and filter in
        Python so even when the head of the result set is skewed toward
        archived/legacy rows we still get enough fresh cases to
        evaluate. Volumes are bounded (≤ a few thousand cases/day),
        so the cost is negligible.
        """
        candidates = self._case_store.search(
            limit=self._sample_size * 4,
        )
        recent = [c for c in candidates if c.created_at >= since]
        return recent[: self._sample_size]

    def _judge_case(self, case: DecisionCase) -> EvalResult:
        input_text = self._build_input(case)
        output_text = self._build_output(case)
        return self._judge.evaluate(
            input_text=input_text,
            output_text=output_text,
            reference="",
        )

    @staticmethod
    def _build_input(case: DecisionCase) -> str:
        return (
            f"Guest message: {case.message_text}\n"
            f"Property: {case.property_id}\n"
            f"Scenario: {case.scenario}\n"
            f"Stage: {case.stage}"
        )

    @staticmethod
    def _build_output(case: DecisionCase) -> str:
        return (
            f"Decision: {case.decision.action_type.value}\n"
            f"Response: {case.response_text}\n"
            f"Tools used: {','.join(case.executed_actions)}"
        )
