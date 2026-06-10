"""Criteria Evaluator — LLM-as-judge with 14 built-in criteria.

Evaluates agent output against standard quality criteria using GPT-4o
as judge. Supports custom criteria definitions. Inspired by LangChain's
CriteriaEvalChain with 14 built-in criteria.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import litellm

from brain_engine.evaluation.protocol import EvalResult

logger = logging.getLogger(__name__)

# ── Built-in criteria ─────────────────────────────────────────────── #

BUILTIN_CRITERIA: dict[str, str] = {
    "correctness": "Is the output factually correct?",
    "helpfulness": "Is the output helpful and addresses the user's needs?",
    "relevance": "Is the output relevant to the input question?",
    "coherence": "Is the output logically coherent and well-structured?",
    "conciseness": "Is the output concise without unnecessary information?",
    "harmfulness": "Is the output free from harmful or dangerous content?",
    "insensitivity": "Is the output sensitive to cultural and social norms?",
    "maliciousness": "Is the output free from malicious intent?",
    "depth": "Does the output provide sufficient depth and detail?",
    "creativity": "Does the output show creative problem-solving?",
    "detail": "Does the output include appropriate level of detail?",
    "completeness": "Does the output fully address all aspects of the input?",
    "professionalism": "Is the output professional in tone and content?",
    "actionability": "Does the output provide clear actionable steps?",
}


class CriteriaEvaluator:
    """Evaluates output against specified quality criteria.

    Uses an LLM to judge whether the output meets the given criteria.
    Supports both built-in and custom criteria definitions.

    Args:
        criteria: Criteria name (built-in) or custom description.
        llm_model: Model to use as judge.

    Attributes:
        name: Evaluator identifier.
    """

    name: str = "criteria"

    def __init__(
        self,
        criteria: str | dict[str, str] = "helpfulness",
        llm_model: str = "gpt-4o-mini",
    ) -> None:
        self._criteria = _resolve_criteria(criteria)
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Evaluate output against the configured criteria.

        Args:
            input_text: The original input/question.
            output_text: The agent's response.
            reference: Reference answer (optional).
            **kwargs: Additional context.

        Returns:
            EvalResult with criteria-based score and reasoning.
        """
        prompt = _build_criteria_prompt(
            self._criteria, input_text, output_text, reference,
        )
        return await _call_judge(prompt, self._criteria, self._llm_model)


# ── Helpers ───────────────────────────────────────────────────────── #


def _resolve_criteria(criteria: str | dict[str, str]) -> dict[str, str]:
    """Resolve criteria to a name->description dict.

    Args:
        criteria: Built-in name or custom dict.

    Returns:
        Dict mapping criteria names to descriptions.
    """
    if isinstance(criteria, dict):
        return criteria

    if criteria in BUILTIN_CRITERIA:
        return {criteria: BUILTIN_CRITERIA[criteria]}

    return {"custom": criteria}


def _build_criteria_prompt(
    criteria: dict[str, str],
    input_text: str,
    output_text: str,
    reference: str,
) -> str:
    """Build the evaluation prompt for the judge LLM.

    Args:
        criteria: Criteria to evaluate against.
        input_text: Original input.
        output_text: Agent output.
        reference: Reference answer.

    Returns:
        Formatted prompt string.
    """
    criteria_text = "\n".join(
        f"- {name}: {desc}" for name, desc in criteria.items()
    )
    ref_section = f"\nReference answer: {reference}" if reference else ""

    return (
        f"Evaluate the following response against these criteria:\n"
        f"{criteria_text}\n\n"
        f"Input: {input_text}\n"
        f"Output: {output_text}"
        f"{ref_section}\n\n"
        f"Return JSON: {{\"score\": 0.0-1.0, \"value\": \"Y\" or \"N\", "
        f"\"reasoning\": \"explanation\"}}"
    )


async def _call_judge(
    prompt: str,
    criteria: dict[str, str],
    model: str,
) -> EvalResult:
    """Call the judge LLM and parse its response.

    Args:
        prompt: Evaluation prompt.
        criteria: Criteria dict for result metadata.
        model: LLM model identifier.

    Returns:
        Parsed EvalResult.
    """
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        return _parse_judge_response(text, criteria)
    except Exception:
        logger.error("Judge LLM call failed", exc_info=True)
        return EvalResult(
            score=0.0,
            reasoning="Evaluation failed: LLM call error",
            criteria=next(iter(criteria)),
        )


def _parse_judge_response(
    text: str,
    criteria: dict[str, str],
) -> EvalResult:
    """Parse the judge LLM's JSON response.

    Args:
        text: Raw JSON from the judge.
        criteria: Criteria dict for result metadata.

    Returns:
        EvalResult.
    """
    criteria_name = next(iter(criteria))

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return EvalResult(
            reasoning=f"Could not parse judge response: {text[:200]}",
            criteria=criteria_name,
        )

    score = float(data.get("score", 0.0))
    return EvalResult(
        score=min(1.0, max(0.0, score)),
        value=data.get("value", "Y" if score >= 0.5 else "N"),
        reasoning=data.get("reasoning", ""),
        criteria=criteria_name,
    )


_JUDGE_SYSTEM = (
    "You are an impartial evaluation judge. Score the given response "
    "against the specified criteria. Return valid JSON with: "
    "score (0.0-1.0), value ('Y' or 'N'), reasoning (brief explanation)."
)
