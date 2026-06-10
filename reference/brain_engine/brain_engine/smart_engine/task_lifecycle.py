"""Task Lifecycle Manager — full Cendra task inbox workflow.

Implements task lifecycle from Cendra's Task Inbox:
    Pending → Waiting → Monitor → Done

Brain Engine auto-resolves FAQ-answerable tasks and escalates
complex ones. Task assignee logic based on category and contacts.

Lifecycle:
    1. PENDING  — New task, unassigned
    2. WAITING  — Assigned, waiting for response
    3. MONITOR  — Response received, monitoring outcome
    4. DONE     — Task resolved
    5. ESCALATED — Cannot be resolved, needs PM

Brain Engine decides: auto-resolve (L1 FAQ) or assign + track.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TaskState:
    """Current state of a task in the lifecycle.

    Attributes:
        task_id: Unique task identifier.
        status: Current lifecycle status.
        title: Task title.
        description: Detailed description.
        category: Main category (Cleaning, Maintenance, etc.).
        subcategory: Sub-category.
        property_id: Property context.
        reservation_id: Related booking (if any).
        assignee_id: Assigned contact ID.
        assignee_name: Assigned contact name.
        priority: Task priority (low/normal/high/critical).
        created_at: ISO timestamp.
        updated_at: ISO timestamp.
        resolved_at: ISO timestamp (when done).
        resolution: How the task was resolved.
        auto_resolved: Whether Brain Engine resolved it automatically.
        missing_info: List of missing information items.
        history: State transition history.
    """

    task_id: str = ""
    status: str = "pending"
    title: str = ""
    description: str = ""
    category: str = ""
    subcategory: str = ""
    property_id: str = ""
    reservation_id: str = ""
    assignee_id: str = ""
    assignee_name: str = ""
    priority: str = "normal"
    created_at: str = ""
    updated_at: str = ""
    resolved_at: str = ""
    resolution: str = ""
    auto_resolved: bool = False
    missing_info: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)


_VALID_TRANSITIONS: dict[str, list[str]] = {
    "pending": ["waiting", "done", "escalated"],
    "waiting": ["monitor", "done", "escalated", "pending"],
    "monitor": ["done", "escalated", "waiting"],
    "done": [],
    "escalated": ["pending", "done"],
}

_FAQ_CATEGORIES: set[str] = {
    "Property Information",
    "WiFi & Access",
    "Check-in Instructions",
    "Amenities",
    "House Rules",
    "Parking",
    "Transportation",
}


class TaskLifecycleManager:
    """Manages task lifecycle transitions and auto-resolution.

    Decides whether a task can be auto-resolved (FAQ) or needs
    human assignment. Tracks state transitions and missing info.
    """

    def create_task(
        self,
        title: str,
        description: str,
        category: str,
        property_id: str,
        subcategory: str = "",
        reservation_id: str = "",
        priority: str = "normal",
    ) -> TaskState:
        """Create a new task in PENDING status.

        Args:
            title: Task title.
            description: Detailed description.
            category: Main category.
            property_id: Property context.
            subcategory: Sub-category.
            reservation_id: Related booking.
            priority: Task priority.

        Returns:
            New TaskState in pending status.
        """
        import uuid
        now = datetime.now(timezone.utc).isoformat()

        task = TaskState(
            task_id=str(uuid.uuid4()),
            status="pending",
            title=title,
            description=description,
            category=category,
            subcategory=subcategory,
            property_id=property_id,
            reservation_id=reservation_id,
            priority=priority,
            created_at=now,
            updated_at=now,
            missing_info=self._detect_missing_info(description, category),
        )

        task.history.append({"status": "pending", "at": now, "by": "system"})
        return task

    def can_auto_resolve(self, task: TaskState) -> bool:
        """Check if task can be resolved automatically by Brain Engine.

        FAQ-answerable categories are auto-resolved using KB search.

        Args:
            task: Task to evaluate.

        Returns:
            True if task can be auto-resolved.
        """
        return task.category in _FAQ_CATEGORIES

    def assign(
        self,
        task: TaskState,
        assignee_id: str,
        assignee_name: str,
    ) -> TaskState:
        """Assign task to a contact and move to WAITING.

        Args:
            task: Task to assign.
            assignee_id: Contact ID.
            assignee_name: Contact name.

        Returns:
            Updated task in waiting status.
        """
        self._validate_transition(task, "waiting")
        task.assignee_id = assignee_id
        task.assignee_name = assignee_name
        return self._transition(task, "waiting", f"Assigned to {assignee_name}")

    def mark_monitoring(self, task: TaskState) -> TaskState:
        """Move task to MONITOR after response received.

        Args:
            task: Task with response from assignee.

        Returns:
            Updated task in monitor status.
        """
        self._validate_transition(task, "monitor")
        return self._transition(task, "monitor", "Response received, monitoring")

    def resolve(
        self,
        task: TaskState,
        resolution: str,
        auto: bool = False,
    ) -> TaskState:
        """Resolve task and move to DONE.

        Args:
            task: Task to resolve.
            resolution: How the task was resolved.
            auto: Whether Brain Engine resolved it automatically.

        Returns:
            Updated task in done status.
        """
        self._validate_transition(task, "done")
        task.resolution = resolution
        task.auto_resolved = auto
        task.resolved_at = datetime.now(timezone.utc).isoformat()
        return self._transition(task, "done", resolution)

    def escalate(self, task: TaskState, reason: str) -> TaskState:
        """Escalate task to PM.

        Args:
            task: Task that cannot be resolved.
            reason: Why escalation is needed.

        Returns:
            Updated task in escalated status.
        """
        self._validate_transition(task, "escalated")
        return self._transition(task, "escalated", reason)

    def reopen(self, task: TaskState, reason: str) -> TaskState:
        """Reopen an escalated or waiting task back to PENDING.

        Args:
            task: Task to reopen.
            reason: Why task is being reopened.

        Returns:
            Updated task in pending status.
        """
        self._validate_transition(task, "pending")
        task.assignee_id = ""
        task.assignee_name = ""
        return self._transition(task, "pending", reason)

    @staticmethod
    def _validate_transition(task: TaskState, target: str) -> None:
        """Validate that a state transition is allowed.

        Args:
            task: Current task state.
            target: Target status.

        Raises:
            ValueError: If transition is not valid.
        """
        allowed = _VALID_TRANSITIONS.get(task.status, [])
        if target not in allowed:
            raise ValueError(
                f"Cannot transition from '{task.status}' to '{target}'. "
                f"Allowed: {allowed}"
            )

    @staticmethod
    def _transition(
        task: TaskState,
        target: str,
        note: str,
    ) -> TaskState:
        """Execute a state transition.

        Args:
            task: Task to transition.
            target: Target status.
            note: Transition note.

        Returns:
            Updated task.
        """
        now = datetime.now(timezone.utc).isoformat()
        task.status = target
        task.updated_at = now
        task.history.append({"status": target, "at": now, "note": note})
        return task

    @staticmethod
    def _detect_missing_info(
        description: str,
        category: str,
    ) -> list[str]:
        """Detect missing information in task description.

        Args:
            description: Task description text.
            category: Task category.

        Returns:
            List of missing information items.
        """
        missing: list[str] = []
        lower = description.lower()

        if category in ("Cleaning and Hygiene", "Maintenance") and "photo" not in lower:
            missing.append("Photo of the issue")

        if category == "Maintenance" and not any(
            kw in lower for kw in ("broken", "leak", "not working", "damage")
        ):
            missing.append("Specific issue description")

        return missing
