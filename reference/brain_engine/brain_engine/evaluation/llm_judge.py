"""LLM Judge — general-purpose LLM-based evaluation.

Uses GPT-4o as an impartial judge for open-ended evaluation without
predefined criteria. The judge scores based on overall quality,
accuracy, and relevance.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from brain_engine.evaluation.protocol import EvalResult

logger = logging.getLogger(__name__)


class LLMJudge:
    """General-purpose LLM evaluation judge.

    Evaluates output quality using a configurable system prompt.
    Can operate with or without a reference answer.

    Args:
        llm_model: Model identifier for the judge.
        system_prompt: Custom system prompt for the judge.

    Attributes:
        name: Evaluator identifier.
    """

    name: str = "llm_judge"

    def __init__(
        self,
        llm_model: str = "gpt-4o-mini",
        system_prompt: str = "",
    ) -> None:
        self._llm_model = llm_model
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Evaluate output using LLM as judge.

        Args:
            input_text: Original input/question.
            output_text: Agent's response.
            reference: Reference answer (optional).
            **kwargs: Additional context.

        Returns:
            EvalResult with judge's score and reasoning.
        """
        prompt = _build_judge_prompt(input_text, output_text, reference)
        return await self._call_judge(prompt)

    async def _call_judge(self, prompt: str) -> EvalResult:
        """Call the judge LLM and parse response.

        Args:
            prompt: Evaluation prompt.

        Returns:
            Parsed EvalResult.
        """
        try:
            response = await litellm.acompletion(
                model=self._llm_model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content or ""
            return _parse_response(text)
        except Exception:
            logger.error("LLM Judge call failed", exc_info=True)
            return EvalResult(
                reasoning="Judge call failed",
                criteria="llm_judge",
            )


# ── Helpers ───────────────────────────────────────────────────────── #


def _build_judge_prompt(
    input_text: str,
    output_text: str,
    reference: str,
) -> str:
    """Build evaluation prompt for the judge.

    Args:
        input_text: Original input.
        output_text: Agent output.
        reference: Reference answer.

    Returns:
        Formatted prompt.
    """
    ref_section = ""
    if reference:
        ref_section = f"\nExpected answer: {reference}\n"

    return (
        f"Input: {input_text}\n\n"
        f"Response: {output_text}\n"
        f"{ref_section}\n"
        f"Score the quality of this response. "
        f"Return JSON: {{\"score\": 0.0-1.0, \"value\": \"Y\" or \"N\", "
        f"\"reasoning\": \"brief explanation\"}}"
    )


def _parse_response(text: str) -> EvalResult:
    """Parse the judge's JSON response.

    Args:
        text: Raw JSON.

    Returns:
        EvalResult.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return EvalResult(
            reasoning=f"Could not parse: {text[:200]}",
            criteria="llm_judge",
        )

    score = float(data.get("score", 0.0))
    return EvalResult(
        score=min(1.0, max(0.0, score)),
        value=data.get("value", "Y" if score >= 0.5 else "N"),
        reasoning=data.get("reasoning", ""),
        criteria="llm_judge",
    )


_DEFAULT_SYSTEM = (
    "You are an impartial quality judge. Evaluate the given response "
    "for accuracy, helpfulness, and relevance. Return valid JSON with: "
    "score (0.0-1.0), value ('Y' or 'N'), reasoning (brief explanation)."
)
