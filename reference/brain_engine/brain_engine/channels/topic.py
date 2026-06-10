"""Topic Channel — multi-value accumulator (pub/sub style).

Accepts multiple writes per step. Returns a list of all values.
Can operate in accumulate mode (values persist across steps) or
clear mode (values reset each step, like a message bus).

Based on: LangGraph Topic (langgraph/channels/topic.py).
"""

from __future__ import annotations

from typing import Any, Generic, Sequence, TypeVar

from brain_engine.channels.base import BaseChannel, EmptyChannelError

V = TypeVar("V")


class Topic(BaseChannel[list[V], V, list[V]], Generic[V]):
    """Channel that accumulates multiple values per step.

    Supports two modes:
    - accumulate=True: values persist and grow across steps
    - accumulate=False: values clear at the start of each step

    Good for: message lists, event logs, task queues.

    Args:
        typ: Type of individual values.
        accumulate: Whether to persist values across steps.
    """

    def __init__(self, typ: Any = Any, accumulate: bool = False) -> None:
        self.typ = typ
        self._accumulate = accumulate
        self._values: list[V] = []
        self._has_been_updated = False

    @property
    def value_type(self) -> Any:
        """Type of stored value (list of typ)."""
        return list

    @property
    def update_type(self) -> Any:
        """Type of accepted updates (single value or list)."""
        return self.typ

    def get(self) -> list[V]:
        """Read all accumulated values.

        Returns:
            List of values (may be empty in accumulate mode).

        Raises:
            EmptyChannelError: If no values and not accumulating.
        """
        if not self._values and not self._accumulate:
            if not self._has_been_updated:
                raise EmptyChannelError(f"Topic '{self.key}' is empty")
        return list(self._values)

    def update(self, values: Sequence[V | list[V]]) -> bool:
        """Append values to the topic.

        Flattens nested lists. In non-accumulate mode, clears
        existing values first.

        Args:
            values: Values to append (may include nested lists).

        Returns:
            True if any values were added.
        """
        if not values:
            return False

        if not self._accumulate:
            self._values = []

        flattened = _flatten(values)
        self._values.extend(flattened)
        self._has_been_updated = True
        return len(flattened) > 0

    def consume(self) -> bool:
        """Clear non-accumulating topic after read.

        Returns:
            True if values were cleared.
        """
        if not self._accumulate and self._values:
            self._values = []
            self._has_been_updated = False
            return True
        return False

    def checkpoint(self) -> list[V]:
        """Serialize values for persistence.

        Returns:
            Copy of the values list.
        """
        return list(self._values)

    def from_checkpoint(self, data: list[V]) -> None:
        """Restore from checkpoint.

        Args:
            data: Previously checkpointed values list.
        """
        self._values = list(data) if data else []
        self._has_been_updated = bool(self._values)

    def reset(self) -> None:
        """Clear all values."""
        self._values = []
        self._has_been_updated = False


def _flatten(values: Sequence[Any]) -> list[Any]:
    """Flatten one level of nested lists.

    Args:
        values: Sequence possibly containing nested lists.

    Returns:
        Flattened list.
    """
    result: list[Any] = []
    for v in values:
        if isinstance(v, list):
            result.extend(v)
        else:
            result.append(v)
    return result
