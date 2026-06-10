"""Ephemeral Channel — single-use value that clears every step.

Like LastValue but automatically resets after each superstep.
Useful for one-time signals, trigger flags, and inter-step
communication that should not persist.

Based on: LangGraph EphemeralValue concept.
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


class EphemeralValue(BaseChannel[V, V, V], Generic[V]):
    """Channel that resets to empty after each step.

    Accepts a single write per step, then clears on consume().
    Good for: trigger signals, one-time notifications.

    Args:
        typ: Type of the value.
    """

    def __init__(self, typ: Any = Any) -> None:
        self.typ = typ
        self._value: Any = _MISSING

    @property
    def value_type(self) -> Any:
        """Type of stored value."""
        return self.typ

    @property
    def update_type(self) -> Any:
        """Type of accepted updates."""
        return self.typ

    def get(self) -> V:
        """Read the current value.

        Returns:
            The value.

        Raises:
            EmptyChannelError: If no value set this step.
        """
        if self._value is _MISSING:
            raise EmptyChannelError(
                f"EphemeralValue '{self.key}' is empty"
            )
        return self._value  # type: ignore[return-value]

    def update(self, values: Sequence[V]) -> bool:
        """Set the value (single-writer per step).

        Args:
            values: Must contain exactly 0 or 1 values.

        Returns:
            True if a value was set.

        Raises:
            InvalidUpdateError: If more than 1 value.
        """
        if len(values) == 0:
            return False
        if len(values) > 1:
            raise InvalidUpdateError(
                f"EphemeralValue '{self.key}' received "
                f"{len(values)} values; expected at most 1"
            )
        self._value = values[0]
        return True

    def consume(self) -> bool:
        """Clear value after read (always resets).

        Returns:
            True if there was a value to clear.
        """
        had_value = self._value is not _MISSING
        self._value = _MISSING
        return had_value

    def checkpoint(self) -> V:
        """Ephemeral values are not checkpointed.

        Returns:
            None (value doesn't persist).
        """
        return None  # type: ignore[return-value]

    def from_checkpoint(self, data: V) -> None:
        """Ephemeral values start empty regardless of checkpoint.

        Args:
            data: Ignored.
        """
        self._value = _MISSING
