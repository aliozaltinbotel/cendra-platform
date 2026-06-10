"""Subagent data models — specs, results, and status tracking.

Defines the core data structures for subagent configuration,
execution results, and lifecycle status.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SubAgentStatus(StrEnum):
    """Lifecycle states for a subagent execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubAgentSpec(BaseModel):
    """Declarative specification for a subagent.

    Defines what a subagent is, what it can do, and how it should
    behave. Used by the registry to describe available subagent types.

    Attributes:
        name: Unique identifier for the subagent type.
        description: When to use this subagent (shown to parent agent).
        system_prompt: Instructions for the subagent's LLM.
        model: Optional model override (e.g., "gpt-4o-mini").
        tools: Tool names available to the subagent.
        middleware: Middleware names to apply.
        max_steps: Maximum execution steps.
        timeout_seconds: Execution timeout.
        inherit_tools: Whether to inherit parent's tools.
        inherit_middleware: Whether to inherit parent's middleware.
    """

    name: str
    description: str
    system_prompt: str = ""
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    middleware: list[str] = Field(default_factory=list)
    max_steps: int = 20
    timeout_seconds: int = 300
    inherit_tools: bool = True
    inherit_middleware: bool = True

    def to_tool_description(self) -> str:
        """Format as a description entry for the task tool.

        Returns:
            Formatted string: ``- name: description``.
        """
        return f"- {self.name}: {self.description}"


class SubAgentResult(BaseModel):
    """Result of a subagent execution.

    Attributes:
        task_id: Unique execution identifier.
        subagent_name: Name of the subagent type used.
        status: Final execution status.
        output: The subagent's final output text.
        error: Error message if failed.
        steps_taken: Number of steps executed.
        elapsed_ms: Total execution time in milliseconds.
        clone_name: BrainZFS clone used for isolation.
        created_at: When the execution started.
        completed_at: When the execution finished.
    """

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    subagent_name: str = ""
    status: SubAgentStatus = SubAgentStatus.PENDING
    output: str = ""
    error: str = ""
    steps_taken: int = 0
    elapsed_ms: int = 0
    clone_name: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether the execution has finished."""
        return self.status in {
            SubAgentStatus.COMPLETED,
            SubAgentStatus.FAILED,
            SubAgentStatus.CANCELLED,
        }

    @property
    def succeeded(self) -> bool:
        """Whether the execution completed successfully."""
        return self.status == SubAgentStatus.COMPLETED

    def to_tool_message(self) -> str:
        """Format as a tool response message for the parent agent.

        Returns:
            Formatted result string.
        """
        if self.succeeded:
            return self.output
        return f"Subagent '{self.subagent_name}' failed: {self.error}"


GENERAL_PURPOSE_SPEC = SubAgentSpec(
    name="general-purpose",
    description=(
        "General-purpose agent for researching complex questions, "
        "searching for information, and executing multi-step tasks. "
        "Use when you need to delegate work that requires deep context "
        "or multiple tool calls."
    ),
    system_prompt=(
        "You are a general-purpose subagent. Complete the task described "
        "by the user thoroughly and return a concise result. You have "
        "access to all tools available to the parent agent."
    ),
    inherit_tools=True,
    inherit_middleware=True,
)
