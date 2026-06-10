"""Planning module — task decomposition and todo management.

Provides a structured task planning system inspired by DeepAgents'
write_todos/read_todos pattern. Allows the Brain Engine agent to
decompose complex operations into manageable steps, track progress,
and adapt plans as new information arrives.

Components:
    - TodoItem: Immutable task model with status lifecycle.
    - TodoList: Ordered task manager with CRUD and filtering.
    - write_todos_tool / read_todos_tool: Agent-callable tool definitions.
    - PLANNING_PROMPT: System prompt instructions for task planning.
"""

from brain_engine.planning.models import TodoItem, TodoProgress, TodoStatus
from brain_engine.planning.todo_list import TodoList
from brain_engine.planning.tools import read_todos_tool, write_todos_tool

__all__ = [
    "TodoItem",
    "TodoList",
    "TodoProgress",
    "TodoStatus",
    "read_todos_tool",
    "write_todos_tool",
]
