"""TodoList — ordered task manager with CRUD and filtering.

Manages an in-memory list of TodoItem objects with support for
hierarchical subtasks, priority-based sorting, bulk operations,
and persistence via JSON serialization.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from brain_engine.planning.models import (
    TodoItem,
    TodoProgress,
    TodoStatus,
    validate_transition,
)

logger = logging.getLogger(__name__)


class TodoList:
    """Ordered collection of todo items with management operations.

    Provides CRUD, filtering, sorting, subtask support, and
    JSON serialization for persistence to any backend.

    Args:
        session_id: Identifier linking this list to a session.
    """

    def __init__(self, session_id: str = "") -> None:
        self._session_id = session_id
        self._items: dict[str, TodoItem] = {}
        self._order: list[str] = []

    @property
    def session_id(self) -> str:
        """Return the owning session identifier."""
        return self._session_id

    @property
    def count(self) -> int:
        """Return the total number of items."""
        return len(self._items)

    # ── CRUD ─────────────────────────────────────────────────────────

    def add(self, item: TodoItem) -> TodoItem:
        """Add a todo item to the list.

        Args:
            item: The TodoItem to add.

        Returns:
            The added item.

        Raises:
            ValueError: If an item with the same ID already exists.
        """
        if item.id in self._items:
            msg = f"Todo item '{item.id}' already exists"
            raise ValueError(msg)
        self._items[item.id] = item
        self._order.append(item.id)
        logger.debug("Added todo: %s — %s", item.id[:8], item.title)
        return item

    def get(self, todo_id: str) -> TodoItem | None:
        """Retrieve a todo item by ID.

        Args:
            todo_id: Unique identifier of the item.

        Returns:
            The TodoItem if found, otherwise None.
        """
        return self._items.get(todo_id)

    def remove(self, todo_id: str) -> bool:
        """Remove a todo item and its subtasks.

        Args:
            todo_id: Unique identifier of the item to remove.

        Returns:
            True if removed, False if not found.
        """
        if todo_id not in self._items:
            return False

        child_ids = [
            cid for cid, item in self._items.items()
            if item.parent_id == todo_id
        ]
        for cid in child_ids:
            self._remove_single(cid)

        self._remove_single(todo_id)
        logger.debug("Removed todo (+ %d children): %s", len(child_ids), todo_id[:8])
        return True

    def _remove_single(self, todo_id: str) -> None:
        """Remove a single item without cascade.

        Args:
            todo_id: Unique identifier of the item.
        """
        self._items.pop(todo_id, None)
        if todo_id in self._order:
            self._order.remove(todo_id)

    def update_status(self, todo_id: str, status: TodoStatus) -> bool:
        """Transition an item to a new status.

        Args:
            todo_id: Unique identifier of the item.
            status: Target status.

        Returns:
            True if updated, False if item not found.

        Raises:
            ValueError: If the transition is invalid.
        """
        item = self._items.get(todo_id)
        if item is None:
            return False
        item.transition_to(status)
        logger.debug("Updated %s → %s", todo_id[:8], status.value)
        return True

    # ── Bulk operations ──────────────────────────────────────────────

    def write_todos(self, todos: list[TodoItem]) -> int:
        """Replace all items with a new list (agent write_todos tool).

        Args:
            todos: Complete replacement list.

        Returns:
            Number of items written.
        """
        self._items.clear()
        self._order.clear()
        for item in todos:
            self._items[item.id] = item
            self._order.append(item.id)
        logger.info("Wrote %d todos for session %s", len(todos), self._session_id)
        return len(todos)

    def read_todos(self) -> list[TodoItem]:
        """Return all items in insertion order (agent read_todos tool).

        Returns:
            Ordered list of all TodoItem objects.
        """
        return [self._items[tid] for tid in self._order if tid in self._items]

    # ── Filtering ────────────────────────────────────────────────────

    def get_by_status(self, status: TodoStatus) -> list[TodoItem]:
        """Filter items by status.

        Args:
            status: Status to filter by.

        Returns:
            List of matching items in insertion order.
        """
        return [
            self._items[tid]
            for tid in self._order
            if tid in self._items and self._items[tid].status == status
        ]

    def get_pending(self) -> list[TodoItem]:
        """Return all pending items."""
        return self.get_by_status(TodoStatus.PENDING)

    def get_in_progress(self) -> list[TodoItem]:
        """Return all in-progress items."""
        return self.get_by_status(TodoStatus.IN_PROGRESS)

    def get_completed(self) -> list[TodoItem]:
        """Return all completed items."""
        return self.get_by_status(TodoStatus.COMPLETED)

    def get_children(self, parent_id: str) -> list[TodoItem]:
        """Return subtasks of a given parent.

        Args:
            parent_id: ID of the parent item.

        Returns:
            List of child items in insertion order.
        """
        return [
            self._items[tid]
            for tid in self._order
            if tid in self._items and self._items[tid].parent_id == parent_id
        ]

    def get_roots(self) -> list[TodoItem]:
        """Return all top-level items (no parent)."""
        return [
            self._items[tid]
            for tid in self._order
            if tid in self._items and self._items[tid].parent_id is None
        ]

    def get_by_priority(self, priority: int) -> list[TodoItem]:
        """Filter items by priority level.

        Args:
            priority: Priority level (0=low, 1=medium, 2=high).

        Returns:
            List of matching items.
        """
        return [
            self._items[tid]
            for tid in self._order
            if tid in self._items and self._items[tid].priority == priority
        ]

    def get_by_tag(self, tag: str) -> list[TodoItem]:
        """Filter items containing a specific tag.

        Args:
            tag: Tag string to match.

        Returns:
            List of matching items.
        """
        return [
            self._items[tid]
            for tid in self._order
            if tid in self._items and tag in self._items[tid].tags
        ]

    def get_next_actionable(self) -> TodoItem | None:
        """Return the highest-priority pending item.

        Returns:
            The most urgent pending item, or None.
        """
        pending = self.get_pending()
        if not pending:
            return None
        return max(pending, key=lambda t: (t.priority, t.created_at))

    # ── Progress ─────────────────────────────────────────────────────

    def get_progress(self) -> TodoProgress:
        """Compute aggregate progress across all items.

        Returns:
            TodoProgress snapshot.
        """
        return TodoProgress.from_items(list(self._items.values()))

    # ── Serialization ────────────────────────────────────────────────

    def to_json(self) -> str:
        """Serialize the entire list to JSON.

        Returns:
            JSON string of all items with order preserved.
        """
        payload = {
            "session_id": self._session_id,
            "items": [item.model_dump(mode="json") for item in self.read_todos()],
        }
        return json.dumps(payload, default=str)

    @classmethod
    def from_json(cls, data: str) -> TodoList:
        """Deserialize a TodoList from JSON.

        Args:
            data: JSON string produced by to_json().

        Returns:
            Reconstructed TodoList.
        """
        payload = json.loads(data)
        todo_list = cls(session_id=payload.get("session_id", ""))
        for raw in payload.get("items", []):
            item = TodoItem.model_validate(raw)
            todo_list.add(item)
        return todo_list

    def to_prompt_summary(self) -> str:
        """Format all items as a compact prompt-friendly summary.

        Returns:
            Multi-line string showing all items with status and priority.
        """
        if not self._items:
            return "No tasks planned."

        lines: list[str] = []
        progress = self.get_progress()
        lines.append(progress.to_report())
        lines.append("")

        for item in self.read_todos():
            indent = "  " if item.parent_id else ""
            lines.append(f"{indent}{item.to_summary()}")

        return "\n".join(lines)
