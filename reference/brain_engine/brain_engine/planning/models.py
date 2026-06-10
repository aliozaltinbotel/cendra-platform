"""Planning data models — TodoItem, TodoStatus, TodoProgress.

Defines the core data structures for task planning and tracking.
Uses Pydantic for validation and serialization, following the
project's dataclass/Pydantic pattern from models/ and api/models.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TodoStatus(StrEnum):
    """Lifecycle states for a todo item.

    Follows a strict forward-only progression:
    pending → in_progress → completed
                          → cancelled
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


_VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.PENDING: {TodoStatus.IN_PROGRESS, TodoStatus.CANCELLED},
    TodoStatus.IN_PROGRESS: {TodoStatus.COMPLETED, TodoStatus.CANCELLED},
    TodoStatus.COMPLETED: set(),
    TodoStatus.CANCELLED: set(),
}


def validate_transition(current: TodoStatus, target: TodoStatus) -> bool:
    """Check whether a status transition is allowed.

    Args:
        current: Current status of the todo item.
        target: Desired next status.

    Returns:
        True if the transition is valid.
    """
    return target in _VALID_TRANSITIONS.get(current, set())


class TodoItem(BaseModel):
    """A single actionable task within a plan.

    Attributes:
        id: Unique identifier (auto-generated UUID).
        title: Short imperative description of the task.
        description: Detailed explanation of what needs to be done.
        status: Current lifecycle status.
        priority: Urgency level (0=low, 1=medium, 2=high).
        parent_id: Optional parent task ID for subtask hierarchy.
        tags: Freeform labels for categorization.
        created_at: UTC timestamp of creation.
        updated_at: UTC timestamp of last modification.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str = ""
    status: TodoStatus = TodoStatus.PENDING
    priority: int = Field(default=0, ge=0, le=2)
    parent_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    model_config = {"frozen": False, "str_strip_whitespace": True}

    @property
    def is_terminal(self) -> bool:
        """Whether the item is in a terminal (completed/cancelled) state."""
        return self.status in {TodoStatus.COMPLETED, TodoStatus.CANCELLED}

    def transition_to(self, target: TodoStatus) -> None:
        """Advance status if the transition is valid.

        Args:
            target: Desired next status.

        Raises:
            ValueError: If the transition is not allowed.
        """
        if not validate_transition(self.status, target):
            msg = f"Cannot transition from {self.status} to {target}"
            raise ValueError(msg)
        self.status = target
        self.updated_at = datetime.now(timezone.utc)

    def to_summary(self) -> str:
        """Return a concise one-line summary for prompt injection.

        Returns:
            Formatted string: ``[status] (priority) title``.
        """
        priority_label = {0: "LOW", 1: "MED", 2: "HIGH"}
        return (
            f"[{self.status.value}] "
            f"({priority_label.get(self.priority, '?')}) "
            f"{self.title}"
        )


class TodoProgress(BaseModel):
    """Aggregate progress snapshot of a todo list.

    Attributes:
        total: Total number of items.
        pending: Count of pending items.
        in_progress: Count of in-progress items.
        completed: Count of completed items.
        cancelled: Count of cancelled items.
        completion_pct: Percentage of completed items (0-100).
    """

    total: int = 0
    pending: int = 0
    in_progress: int = 0
    completed: int = 0
    cancelled: int = 0
    completion_pct: float = 0.0

    @classmethod
    def from_items(cls, items: list[TodoItem]) -> TodoProgress:
        """Compute progress from a list of todo items.

        Args:
            items: List of TodoItem objects.

        Returns:
            TodoProgress snapshot.
        """
        total = len(items)
        counts: dict[str, int] = {
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "cancelled": 0,
        }
        for item in items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1

        pct = (counts["completed"] / total * 100) if total > 0 else 0.0
        return cls(
            total=total,
            pending=counts["pending"],
            in_progress=counts["in_progress"],
            completed=counts["completed"],
            cancelled=counts["cancelled"],
            completion_pct=round(pct, 1),
        )

    def to_report(self) -> str:
        """Format a human-readable progress report.

        Returns:
            Multi-line progress summary.
        """
        return (
            f"Progress: {self.completed}/{self.total} done "
            f"({self.completion_pct}%)\n"
            f"  Pending: {self.pending} | "
            f"In Progress: {self.in_progress} | "
            f"Completed: {self.completed} | "
            f"Cancelled: {self.cancelled}"
        )


def create_todo(
    title: str,
    description: str = "",
    priority: int = 0,
    parent_id: str | None = None,
    tags: list[str] | None = None,
) -> TodoItem:
    """Factory function to create a new TodoItem with defaults.

    Args:
        title: Short imperative task description.
        description: Detailed explanation.
        priority: 0=low, 1=medium, 2=high.
        parent_id: Optional parent for subtask hierarchy.
        tags: Optional categorization labels.

    Returns:
        A new TodoItem in PENDING status.
    """
    return TodoItem(
        title=title,
        description=description,
        priority=priority,
        parent_id=parent_id,
        tags=tags or [],
    )
