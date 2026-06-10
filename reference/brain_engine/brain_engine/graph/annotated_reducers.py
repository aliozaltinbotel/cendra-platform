"""Annotated field reducer extraction for StateGraph.

Enables using ``Annotated[list, operator.add]`` type hints in
TypedDict state schemas to auto-configure channel reducers.

Example::

    from typing import Annotated
    import operator

    class MyState(TypedDict):
        messages: Annotated[list, operator.add]  # accumulator
        count: int  # last-value (no annotation)

    graph = StateGraph(MyState)
    # 'messages' auto-gets BinaryOperatorAggregate with operator.add
    # 'count' auto-gets LastValue

Based on: LangGraph _get_channels
(langgraph/graph/state.py _get_channels function).
"""

from __future__ import annotations

import logging
import operator
from typing import Any, get_type_hints

logger = logging.getLogger(__name__)

# Known reducer functions
_KNOWN_REDUCERS: dict[str, Any] = {
    "add": operator.add,
    "or_": operator.or_,
    "and_": operator.and_,
}


def extract_reducers(
    state_schema: type,
) -> dict[str, Any | None]:
    """Extract reducer functions from Annotated type hints.

    Inspects a TypedDict or dataclass for ``Annotated`` fields.
    Returns a mapping of field_name -> reducer_function (or None
    for fields without a reducer).

    Args:
        state_schema: TypedDict or similar class with annotations.

    Returns:
        Dict mapping field names to reducer functions (or None).
    """
    reducers: dict[str, Any | None] = {}
    hints = _safe_get_hints(state_schema)

    for field_name, hint in hints.items():
        reducer = _extract_reducer_from_hint(hint)
        reducers[field_name] = reducer

    return reducers


def _extract_reducer_from_hint(hint: Any) -> Any | None:
    """Extract a reducer function from an Annotated type hint.

    Looks for ``Annotated[base_type, reducer_func]`` pattern where
    ``reducer_func`` is a callable (typically ``operator.add``).

    Args:
        hint: A type annotation, possibly ``Annotated[...]``.

    Returns:
        Reducer callable, or None if not annotated.
    """
    metadata = _get_annotation_metadata(hint)
    if not metadata:
        return None

    for item in metadata:
        if callable(item) and not isinstance(item, type):
            return item

    return None


def _get_annotation_metadata(hint: Any) -> tuple[Any, ...]:
    """Get metadata from an Annotated type hint.

    Args:
        hint: Type annotation.

    Returns:
        Tuple of metadata items, or empty tuple if not Annotated.
    """
    origin = getattr(hint, "__class__", None)
    if hasattr(hint, "__metadata__"):
        return hint.__metadata__
    return ()


def _safe_get_hints(schema: type) -> dict[str, Any]:
    """Safely get type hints from a class.

    Args:
        schema: Class to inspect.

    Returns:
        Type hints dict, or empty dict on failure.
    """
    try:
        return get_type_hints(schema, include_extras=True)
    except Exception:
        return getattr(schema, "__annotations__", {})


def build_channel_config(
    state_schema: type,
) -> dict[str, dict[str, Any]]:
    """Build channel configuration from a state schema.

    For each field, determines the appropriate channel type:
    - Fields with Annotated reducer → ``binop`` with that reducer
    - ``list`` fields → ``binop`` with ``operator.add`` (default)
    - All other fields → ``last_value``

    Args:
        state_schema: TypedDict with optional Annotated hints.

    Returns:
        Dict of field_name -> channel config dict with keys:
        ``channel_type`` (str) and ``reducer`` (callable|None).
    """
    reducers = extract_reducers(state_schema)
    hints = _safe_get_hints(state_schema)
    config: dict[str, dict[str, Any]] = {}

    for field_name, hint in hints.items():
        reducer = reducers.get(field_name)
        base_type = _get_base_type(hint)

        if reducer is not None:
            config[field_name] = {
                "channel_type": "binop",
                "reducer": reducer,
                "base_type": base_type,
            }
        elif base_type is list:
            config[field_name] = {
                "channel_type": "binop",
                "reducer": operator.add,
                "base_type": list,
            }
        else:
            config[field_name] = {
                "channel_type": "last_value",
                "reducer": None,
                "base_type": base_type,
            }

    return config


def _get_base_type(hint: Any) -> type:
    """Extract the base type from a possibly-Annotated hint.

    Args:
        hint: Type annotation (may be ``Annotated[list, ...]``).

    Returns:
        The unwrapped base type.
    """
    if hasattr(hint, "__origin__"):
        origin = hint.__origin__
        if origin is not None:
            if hasattr(origin, "__origin__"):
                return origin.__origin__
            return origin
    if isinstance(hint, type):
        return hint
    return type(hint)
