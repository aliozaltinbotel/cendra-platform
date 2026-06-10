"""State management for the graph execution engine.

Defines typed state schemas, reducer functions for merging partial
updates, and the built-in ``MessagesState`` for chat-style graphs.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, TypedDict, get_type_hints


class MessagesState(TypedDict, total=False):
    """Built-in state schema for chat-style graphs.

    Attributes:
        messages: Chat message list. Uses ``add_messages`` reducer
            so partial updates append rather than replace.
    """

    messages: list[dict[str, Any]]


def add_messages(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reducer that appends new messages to the existing list.

    Args:
        existing: Current messages in state.
        new: New messages from a node's partial update.

    Returns:
        Combined message list (existing + new).
    """
    return [*existing, *new]


# Registry: field_name → reducer function
# If a field has no reducer, new values replace old ones.
_DEFAULT_REDUCERS: dict[str, Callable[..., Any]] = {
    "messages": add_messages,
}


def apply_update(
    current_state: dict[str, Any],
    update: dict[str, Any],
    reducers: dict[str, Callable[..., Any]] | None = None,
) -> dict[str, Any]:
    """Merge a partial update into the current state.

    For fields with a registered reducer, the reducer combines old and
    new values. For all other fields, the new value replaces the old.

    Args:
        current_state: Full current graph state.
        update: Partial update from a node function.
        reducers: Optional custom reducer map. Falls back to defaults.

    Returns:
        New state dict with the update applied (deep-copied).
    """
    merged = copy.deepcopy(current_state)
    reducer_map = reducers or _DEFAULT_REDUCERS

    for key, value in update.items():
        if key in reducer_map and key in merged:
            merged[key] = reducer_map[key](merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)

    return merged


def initialize_state(
    schema: type,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Create an initial state from a schema and input data.

    Fills missing fields with empty defaults based on type hints.

    Args:
        schema: TypedDict class defining the state shape.
        input_data: Initial values provided by the caller.

    Returns:
        State dict with all schema fields initialized.
    """
    hints = get_type_hints(schema)
    state: dict[str, Any] = {}

    for field_name, field_type in hints.items():
        if field_name in input_data:
            state[field_name] = copy.deepcopy(input_data[field_name])
        else:
            state[field_name] = _default_for_type(field_type)

    return state


def _default_for_type(type_hint: Any) -> Any:
    """Return a sensible default value for a type hint.

    Args:
        type_hint: A Python type annotation.

    Returns:
        Empty default: [] for list, {} for dict, "" for str, etc.
    """
    origin = getattr(type_hint, "__origin__", None)
    if origin is list:
        return []
    if origin is dict:
        return {}
    if type_hint is str:
        return ""
    if type_hint is int:
        return 0
    if type_hint is float:
        return 0.0
    if type_hint is bool:
        return False
    return None
