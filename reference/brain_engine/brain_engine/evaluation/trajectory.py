"""Trajectory Evaluator — evaluates agent intermediate steps.

Scores the quality of the agent's decision-making process (trajectory),
not just the final output. Inspired by DeepAgents TrajectoryScorer
and LangChain's AgentTrajectoryEvalChain.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import litellm

from brain_engine.evaluation.protocol import EvalResult

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryStep:
    """A single step in an agent's trajectory.

    Attributes:
        action: The action/tool chosen.
        action_input: Input to the action.
        observation: Result of the action.
        reasoning: Agent's reasoning for this step.
    """

    action: str
    action_input: dict[str, Any]
    observation: str
    reasoning: str = ""


class TrajectoryEvaluator:
    """Evaluates the quality of an agent's decision trajectory.

    Scores intermediate steps for efficiency, correctness, and
    logical progression. Uses LLM-as-judge.

    Args:
        llm_model: Model to use as judge.

    Attributes:
        name: Evaluator identifier.
    """

    name: str = "trajectory"

    def __init__(self, llm_model: str = "gpt-4o-mini") -> None:
        self._llm_model = llm_model

    async def evaluate(
        self,
        input_text: str,
        output_text: str,
        reference: str = "",
        **kwargs: Any,
    ) -> EvalResult:
        """Evaluate a trajectory of agent steps.

        Args:
            input_text: The original task/question.
            output_text: The final agent output.
            reference: Reference answer (optional).
            **kwargs: Must include 'steps' as list of TrajectoryStep or dicts.

        Returns:
            EvalResult with trajectory score and analysis.
        """
        steps = kwargs.get("steps", [])
        normalized = _normalize_steps(steps)
        prompt = _build_trajectory_prompt(
            input_text, output_text, normalized, reference,
        )
        return await _call_trajectory_judge(prompt, self._llm_model)

    async def evaluate_steps(
        self,
        input_text: str,
        steps: list[TrajectoryStep],
        output_text: str,
        reference: str = "",
    ) -> EvalResult:
        """Convenience method with typed steps parameter.

        Args:
            input_text: Original input.
            steps: List of trajectory steps.
            output_text: Final output.
            reference: Reference answer.

        Returns:
            EvalResult.
        """
        return await self.evaluate(
            input_text, output_text, reference, steps=steps,
        )


# ── Helpers ───────────────────────────────────────────────────────── #


def _normalize_steps(
    steps: list[Any],
) -> list[dict[str, Any]]:
    """Normalize steps to list of dicts.

    Args:
        steps: TrajectoryStep objects or raw dicts.

    Returns:
        List of step dicts.
    """
    result: list[dict[str, Any]] = []

    for step in steps:
        if isinstance(step, TrajectoryStep):
            result.append({
                "action": step.action,
                "input": step.action_input,
                "observation": step.observation,
                "reasoning": step.reasoning,
            })
        elif isinstance(step, dict):
            result.append(step)

    return result


def _build_trajectory_prompt(
    input_text: str,
    output_text: str,
    steps: list[dict[str, Any]],
    reference: str,
) -> str:
    """Build the trajectory evaluation prompt.

    Args:
        input_text: Original input.
        output_text: Final output.
        steps: Normalized step dicts.
        reference: Reference answer.

    Returns:
        Formatted prompt.
    """
    steps_text = _format_steps(steps)
    ref_section = f"\nExpected answer: {reference}" if reference else ""

    return (
        f"Evaluate the quality of this agent's trajectory.\n\n"
        f"Task: {input_text}\n\n"
        f"Steps taken:\n{steps_text}\n\n"
        f"Final output: {output_text}"
        f"{ref_section}\n\n"
        f"Score on: efficiency (no unnecessary steps), correctness "
        f"(right tools used), logical progression.\n\n"
        f"Return JSON: {{\"score\": 0.0-1.0, \"value\": \"Y\" or \"N\", "
        f"\"reasoning\": \"analysis\"}}"
    )


def _format_steps(steps: list[dict[str, Any]]) -> str:
    """Format steps as numbered text.

    Args:
        steps: List of step dicts.

    Returns:
        Formatted step text.
    """
    lines: list[str] = []

    for i, step in enumerate(steps, 1):
        action = step.get("action", "unknown")
        obs = str(step.get("observation", ""))[:200]
        lines.append(f"{i}. Action: {action} -> {obs}")

    return "\n".join(lines) if lines else "(no steps)"


async def _call_trajectory_judge(
    prompt: str,
    model: str,
) -> EvalResult:
    """Call the judge LLM for trajectory evaluation.

    Args:
        prompt: Evaluation prompt.
        model: LLM model.

    Returns:
        Parsed EvalResult.
    """
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _TRAJ_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        return _parse_traj_response(text)
    except Exception:
        logger.error("Trajectory judge failed", exc_info=True)
        return EvalResult(
            reasoning="Trajectory evaluation failed: LLM error",
            criteria="trajectory",
        )


def _parse_traj_response(text: str) -> EvalResult:
    """Parse trajectory judge response.

    Args:
        text: Raw JSON from the judge.

    Returns:
        EvalResult.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return EvalResult(
            reasoning=f"Could not parse: {text[:200]}",
            criteria="trajectory",
        )

    score = float(data.get("score", 0.0))
    return EvalResult(
        score=min(1.0, max(0.0, score)),
        value=data.get("value", "Y" if score >= 0.5 else "N"),
        reasoning=data.get("reasoning", ""),
        criteria="trajectory",
    )


_TRAJ_SYSTEM = (
    "You are an agent trajectory evaluator. Analyze the efficiency and "
    "correctness of the agent's decision-making process. Score the "
    "trajectory (not the final answer). Return valid JSON with: "
    "score (0.0-1.0), value ('Y' or 'N'), reasoning (analysis)."
)
