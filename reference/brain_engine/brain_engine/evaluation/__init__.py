"""Evaluation system for Brain Engine — criteria, trajectory, and LLM judge.

Provides multiple evaluation strategies for assessing agent performance:
criteria-based (14 built-in), trajectory analysis, and general-purpose
LLM-as-judge. Includes a batch runner for dataset evaluation.

Example::

    from brain_engine.evaluation import (
        CriteriaEvaluator, EvalRunner, EvalCase,
    )

    evaluator = CriteriaEvaluator(criteria="helpfulness")
    runner = EvalRunner(evaluators=[evaluator])
    report = await runner.run([
        EvalCase(input_text="What time is checkout?",
                 output_text="Checkout is at 11 AM.",
                 reference="11:00 AM"),
    ])
"""

from brain_engine.evaluation.criteria import (
    BUILTIN_CRITERIA,
    CriteriaEvaluator,
)
from brain_engine.evaluation.golden_cases_runner import (
    EvaluationResultStore,
    GoldenCasesReport,
    GoldenCasesRunner,
    InMemoryEvaluationResultStore,
    golden_cases_enabled,
)
from brain_engine.evaluation.llm_judge import LLMJudge
from brain_engine.evaluation.protocol import EvalResult, Evaluator
from brain_engine.evaluation.runner import EvalCase, EvalReport, EvalRunner
from brain_engine.evaluation.trajectory import (
    TrajectoryEvaluator,
    TrajectoryStep,
)

__all__ = [
    "BUILTIN_CRITERIA",
    "CriteriaEvaluator",
    "EvalCase",
    "EvalReport",
    "EvalResult",
    "EvalRunner",
    "EvaluationResultStore",
    "Evaluator",
    "GoldenCasesReport",
    "GoldenCasesRunner",
    "InMemoryEvaluationResultStore",
    "LLMJudge",
    "TrajectoryEvaluator",
    "TrajectoryStep",
    "golden_cases_enabled",
]
