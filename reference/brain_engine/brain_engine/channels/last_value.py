"""LastValue Channel — stores only the most recent value.

Enforces single-writer-per-step: raises InvalidUpdateError if
more than one value is written in a single superstep. This
prevents accidental concurrent overwrites.

Based on: LangGraph LastValue (langgraph/channels/last_value.py).
"""

from __future__ import annotations

from typing import Any, Generic, Sequence, TypeVar

from brain_engine.channels.base import (
    BaseChannel,
    EmptyChannelError,
    InvalidUpdateError,
)

V = TypeVar("V")

_MISSING = object()


class LastValue(BaseChannel[V, V, V], Generic[V]):
    """Channel that stores only the last written value.

    Rejects multiple writes per step (single-writer constraint).
    Good for scalar state: counters, flags, current status.

    Args:
        typ: Type of the value (for schema generation).
        default: Default value (MISSING = no default).
    """

    def __init__(self, typ: Any = Any, default: Any = _MISSING) -> None:
        self.typ = typ
        self._value: Any = default
        self._default = default

    @property
    def value_type(self) -> Any:
        """Type of the stored value."""
        return self.typ

    @property
    def update_type(self) -> Any:
        """Type of accepted updates (same as value)."""
        return self.typ

    def get(self) -> V:
        """Read the current value.

        Returns:
            The stored value.

        Raises:
            EmptyChannelError: If no value has been set.
        """
        if self._value is _MISSING:
            raise EmptyChannelError(f"Channel '{self.key}' has no value")
        return self._value  # type: ignore[return-value]

    def update(self, values: Sequence[V]) -> bool:
        """Store the single provided value.

        Args:
            values: Must contain exactly 0 or 1 values.

        Returns:
            True if the value changed.

        Raises:
            InvalidUpdateError: If more than 1 value provided.
        """
        if len(values) == 0:
            return False
        if len(values) > 1:
            raise InvalidUpdateError(
                f"LastValue channel '{self.key}' received {len(values)} "
                f"values; expected at most 1"
            )
        old = self._value
        self._value = values[0]
        return self._value != old

    def checkpoint(self) -> V:
        """Serialize current value.

        Returns:
            The current value (or None if missing).
        """
        if self._value is _MISSING:
            return None  # type: ignore[return-value]
        return self._value  # type: ignore[return-value]

    def from_checkpoint(self, data: V) -> None:
        """Restore from checkpoint.

        Args:
            data: Previously checkpointed value.
        """
        self._value = data if data is not None else self._default

    def reset(self) -> None:
        """Reset to default value."""
        self._value = self._default
