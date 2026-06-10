"""Planning tools — agent-callable tool definitions for task management.

Provides write_todos and read_todos as structured tool definitions
compatible with the Brain Engine's middleware tool system. These tools
allow the LLM agent to create, read, and manage task plans during
complex multi-step operations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from brain_engine.planning.models import TodoItem, TodoStatus
from brain_engine.planning.todo_list import TodoList

logger = logging.getLogger(__name__)

# ── Tool JSON schemas ────────────────────────────────────────────────

WRITE_TODOS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "description": "List of todo items to create or replace.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short imperative task description.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed explanation of what to do.",
                    },
                    "priority": {
                        "type": "integer",
                        "enum": [0, 1, 2],
                        "description": "0=low, 1=medium, 2=high.",
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "ID of parent task for subtasks.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Categorization labels.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    "required": ["todos"],
}

READ_TODOS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status_filter": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed", "cancelled", "all"],
            "description": "Filter by status. Default: all.",
        },
    },
}

UPDATE_TODO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "todo_id": {
            "type": "string",
            "description": "ID of the todo to update.",
        },
        "status": {
            "type": "string",
            "enum": ["in_progress", "completed", "cancelled"],
            "description": "New status for the todo.",
        },
    },
    "required": ["todo_id", "status"],
}


# ── Tool handler factories ──────────────────────────────────────────


def _build_write_handler(
    todo_list: TodoList,
) -> Any:
    """Create the write_todos handler bound to a TodoList.

    Args:
        todo_list: Target TodoList instance.

    Returns:
        Async handler function.
    """

    async def handle_write_todos(args: dict[str, Any]) -> dict[str, Any]:
        """Replace the plan with a new set of todos.

        Args:
            args: Parsed tool arguments with ``todos`` list.

        Returns:
            Dict with count and summary.
        """
        raw_items = args.get("todos", [])
        items = [
            TodoItem(
                title=raw["title"],
                description=raw.get("description", ""),
                priority=raw.get("priority", 0),
                parent_id=raw.get("parent_id"),
                tags=raw.get("tags", []),
            )
            for raw in raw_items
        ]
        count = todo_list.write_todos(items)
        logger.info("write_todos: wrote %d items", count)
        return {
            "written": count,
            "summary": todo_list.to_prompt_summary(),
        }

    return handle_write_todos


def _build_read_handler(
    todo_list: TodoList,
) -> Any:
    """Create the read_todos handler bound to a TodoList.

    Args:
        todo_list: Source TodoList instance.

    Returns:
        Async handler function.
    """

    async def handle_read_todos(args: dict[str, Any]) -> dict[str, Any]:
        """Read current plan items, optionally filtered by status.

        Args:
            args: Parsed tool arguments with optional ``status_filter``.

        Returns:
            Dict with items list and progress report.
        """
        status_filter = args.get("status_filter", "all")
        if status_filter == "all":
            items = todo_list.read_todos()
        else:
            items = todo_list.get_by_status(TodoStatus(status_filter))

        return {
            "items": [item.model_dump(mode="json") for item in items],
            "progress": todo_list.get_progress().model_dump(),
            "summary": todo_list.to_prompt_summary(),
        }

    return handle_read_todos


def _build_update_handler(
    todo_list: TodoList,
) -> Any:
    """Create the update_todo handler bound to a TodoList.

    Args:
        todo_list: Target TodoList instance.

    Returns:
        Async handler function.
    """

    async def handle_update_todo(args: dict[str, Any]) -> dict[str, Any]:
        """Update the status of a single todo item.

        Args:
            args: Dict with ``todo_id`` and ``status``.

        Returns:
            Dict with success flag and updated summary.
        """
        todo_id = args["todo_id"]
        target = TodoStatus(args["status"])
        updated = todo_list.update_status(todo_id, target)
        if not updated:
            return {"success": False, "error": f"Todo '{todo_id}' not found"}
        logger.info("update_todo: %s → %s", todo_id[:8], target.value)
        return {
            "success": True,
            "summary": todo_list.to_prompt_summary(),
        }

    return handle_update_todo


# ── Tool definition builders ────────────────────────────────────────


def write_todos_tool(todo_list: TodoList) -> dict[str, Any]:
    """Build the write_todos tool definition.

    Args:
        todo_list: TodoList instance to bind to.

    Returns:
        Tool dict with name, description, parameters, and handler.
    """
    return {
        "name": "write_todos",
        "description": (
            "Create or replace the task plan. Use this to decompose complex "
            "operations into ordered steps. Each todo has a title, optional "
            "description, priority (0-2), and optional parent_id for subtasks."
        ),
        "parameters": WRITE_TODOS_SCHEMA,
        "handler": _build_write_handler(todo_list),
    }


def read_todos_tool(todo_list: TodoList) -> dict[str, Any]:
    """Build the read_todos tool definition.

    Args:
        todo_list: TodoList instance to bind to.

    Returns:
        Tool dict with name, description, parameters, and handler.
    """
    return {
        "name": "read_todos",
        "description": (
            "Read the current task plan. Returns all items with their status "
            "and a progress summary. Use status_filter to see only specific "
            "statuses (pending, in_progress, completed, cancelled)."
        ),
        "parameters": READ_TODOS_SCHEMA,
        "handler": _build_read_handler(todo_list),
    }


def update_todo_tool(todo_list: TodoList) -> dict[str, Any]:
    """Build the update_todo tool definition.

    Args:
        todo_list: TodoList instance to bind to.

    Returns:
        Tool dict with name, description, parameters, and handler.
    """
    return {
        "name": "update_todo",
        "description": (
            "Update the status of a single todo item. Use this to mark "
            "tasks as in_progress when starting, completed when done, or "
            "cancelled if no longer needed."
        ),
        "parameters": UPDATE_TODO_SCHEMA,
        "handler": _build_update_handler(todo_list),
    }
