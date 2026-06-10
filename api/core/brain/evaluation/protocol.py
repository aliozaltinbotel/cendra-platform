"""Evaluator Protocol — defines the interface for all evaluation strategies.

All evaluators implement this protocol, returning a standardized
EvalResult with score, reasoning, and pass/fail value. Inspired by
LangChain's BaseEvaluator + DeepAgents TrajectoryScorer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class EvalResult:
    """Standard evaluation result.

    Attributes:
        score: Numeric score (0.0 to 1.0).
        value: Pass/fail label ('Y' or 'N').
        reasoning: Explanation of the evaluation.
        criteria: Name of the criteria evaluated.
        metadata: Extra evaluation data.
    """

    score: float = 0.0
    value: str = "N"
    reasoning: str = ""
    criteria: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Whether the evaluation passed (score >= 0.5)."""
        return self.score >= 0.5


@runtime_checkable
class Evaluator(Protocol):
    """Protocol for evaluation strategies.

    Evaluators score agent outputs against reference data or criteria.
    """

    @property
    def name(self) -> str:
        """Evaluator name for identification."""
        ...

    def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Evaluate an agent output.

        Args:
            input_text: The input/question.
            output_text: The agent's output/answer.
            reference: Reference/expected answer (if available).
            **kwargs: Additional context.

        Returns:
            EvalResult with score and reasoning.
        """
        ...
