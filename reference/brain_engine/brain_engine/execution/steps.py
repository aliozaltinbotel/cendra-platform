"""StepCollector — intermediate steps tracking.

Records every action-observation pair during execution for
evaluation, debugging, and trajectory analysis.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.execution.models import AgentAction, AgentStep, StepType

logger = logging.getLogger(__name__)


class StepCollector:
    """Collects and queries intermediate execution steps.

    Provides an ordered history of all steps taken during an
    agent execution run, with filtering and summarization.
    """

    def __init__(self) -> None:
        self._steps: list[AgentStep] = []

    @property
    def count(self) -> int:
        """Return the total number of steps."""
        return len(self._steps)

    @property
    def steps(self) -> list[AgentStep]:
        """Return all steps in order."""
        return list(self._steps)

    def add(
        self,
        action: AgentAction,
        observation: str,
        step_type: StepType = StepType.ACTION,
        elapsed_ms: int = 0,
    ) -> AgentStep:
        """Record a new step.

        Args:
            action: The action taken.
            observation: The result of the action.
            step_type: Type of step.
            elapsed_ms: Time spent on this step.

        Returns:
            The created AgentStep.
        """
        step = AgentStep(
            action=action,
            observation=observation,
            step_type=step_type,
            elapsed_ms=elapsed_ms,
            step_number=len(self._steps) + 1,
        )
        self._steps.append(step)
        logger.debug(
            "Step %d: %s → %s (%dms)",
            step.step_number, action.tool, observation[:50], elapsed_ms,
        )
        return step

    def get_by_tool(self, tool_name: str) -> list[AgentStep]:
        """Filter steps by tool name.

        Args:
            tool_name: Tool to filter by.

        Returns:
            List of matching steps.
        """
        return [s for s in self._steps if s.action.tool == tool_name]

    def get_by_type(self, step_type: StepType) -> list[AgentStep]:
        """Filter steps by type.

        Args:
            step_type: Type to filter by.

        Returns:
            List of matching steps.
        """
        return [s for s in self._steps if s.step_type == step_type]

    def total_elapsed_ms(self) -> int:
        """Return total time across all steps."""
        return sum(s.elapsed_ms for s in self._steps)

    def to_trajectory(self) -> list[tuple[dict[str, Any], str]]:
        """Export as LangChain-compatible trajectory format.

        Returns:
            List of (action_dict, observation) tuples.
        """
        return [
            (
                {
                    "tool": s.action.tool,
                    "tool_input": s.action.tool_input,
                    "log": s.action.log,
                },
                s.observation,
            )
            for s in self._steps
        ]

    def to_summary(self) -> str:
        """Format a human-readable summary of all steps.

        Returns:
            Multi-line step summary.
        """
        if not self._steps:
            return "No steps taken."
        lines: list[str] = [f"Steps: {self.count}"]
        for step in self._steps:
            lines.append(
                f"  {step.step_number}. [{step.step_type.value}] "
                f"{step.action.tool}({_truncate(str(step.action.tool_input), 50)}) "
                f"→ {_truncate(step.observation, 80)} "
                f"({step.elapsed_ms}ms)"
            )
        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all recorded steps."""
        self._steps.clear()


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
