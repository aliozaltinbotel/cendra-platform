"""Execution data models — actions, steps, and results.

Defines typed data structures for the agent execution lifecycle:
actions (tool calls), finishes (final answers), steps (action +
observation pairs), and overall execution results.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class StepType(StrEnum):
    """Types of execution steps."""

    ACTION = "action"
    FINISH = "finish"
    INTERRUPT = "interrupt"
    ERROR = "error"


class AgentAction(BaseModel):
    """A tool call decision by the agent.

    Attributes:
        tool: Name of the tool to execute.
        tool_input: Arguments for the tool.
        log: The agent's reasoning/explanation for this action.
        step_id: Unique step identifier.
    """

    tool: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    log: str = ""
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class AgentFinish(BaseModel):
    """The agent's final answer.

    Attributes:
        return_values: Dict of output values.
        log: The agent's final reasoning.
        step_id: Unique step identifier.
    """

    return_values: dict[str, Any] = Field(default_factory=dict)
    log: str = ""
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def output(self) -> str:
        """Return the primary output value."""
        return str(self.return_values.get("output", ""))


class AgentStep(BaseModel):
    """An action-observation pair from one execution step.

    Attributes:
        action: The action the agent took.
        observation: The result of executing the action.
        step_type: Type of this step.
        elapsed_ms: Time spent on this step.
        step_number: Position in the execution sequence.
    """

    action: AgentAction
    observation: str = ""
    step_type: StepType = StepType.ACTION
    elapsed_ms: int = 0
    step_number: int = 0


class ExecutionConfig(BaseModel):
    """Configuration for an execution run.

    Attributes:
        max_iterations: Maximum number of think-act-observe cycles.
        max_execution_time_seconds: Wall-clock timeout.
        handle_parsing_errors: How to handle LLM output parsing failures.
        return_intermediate_steps: Whether to include steps in result.
        early_stopping_method: How to stop ("force" or "generate").
    """

    max_iterations: int = 15
    max_execution_time_seconds: int = 300
    handle_parsing_errors: bool = True
    return_intermediate_steps: bool = True
    early_stopping_method: str = "force"


class ExecutionResult(BaseModel):
    """Result of a complete agent execution run.

    Attributes:
        output: The final output text.
        return_values: Full return values dict.
        intermediate_steps: All steps taken during execution.
        iterations: Number of iterations completed.
        elapsed_ms: Total execution time.
        status: Final status (completed, max_iterations, timeout, error, interrupted).
        error: Error message if failed.
        interrupt_value: Value from interrupt if paused.
        run_id: Unique run identifier.
    """

    output: str = ""
    return_values: dict[str, Any] = Field(default_factory=dict)
    intermediate_steps: list[AgentStep] = Field(default_factory=list)
    iterations: int = 0
    elapsed_ms: int = 0
    status: str = "completed"
    error: str = ""
    interrupt_value: Any = None
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def succeeded(self) -> bool:
        """Whether the execution completed successfully."""
        return self.status == "completed"

    @property
    def was_interrupted(self) -> bool:
        """Whether the execution was interrupted for HITL."""
        return self.status == "interrupted"
