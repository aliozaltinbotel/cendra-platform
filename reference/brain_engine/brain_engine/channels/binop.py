"""BinaryOperatorAggregate Channel — reducer-based value aggregation.

Applies a binary operator (e.g. operator.add, list extend) to
merge multiple updates into a single accumulated value. Supports
the Overwrite sentinel to bypass the operator for a direct set.

Based on: LangGraph BinaryOperatorAggregate (langgraph/channels/binop.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, Sequence, TypeVar

from brain_engine.channels.base import BaseChannel, EmptyChannelError

V = TypeVar("V")

_MISSING = object()


@dataclass(frozen=True, slots=True)
class Overwrite:
    """Sentinel that bypasses the operator for a direct set.

    Usage::

        # Normally: messages = operator.add(old, new)  → concatenation
        # With Overwrite: messages = new  → replacement

        channel.update([Overwrite(["fresh", "start"])])

    Attributes:
        value: The value to set directly.
    """

    value: Any


class BinaryOperatorAggregate(
    BaseChannel[V, V, V], Generic[V],
):
    """Channel that reduces updates via a binary operator.

    Each update is folded into the current value using the operator.
    Multiple writes per step are supported and applied sequentially.

    Args:
        typ: Type of the value.
        operator: Binary function (a, b) -> a. Example: operator.add.
        default: Initial value factory (called once to create default).
    """

    def __init__(
        self,
        typ: Any,
        operator: Callable[[V, V], V],
        default: Callable[[], V] | None = None,
    ) -> None:
        self.typ = typ
        self._operator = operator
        self._default = default
        self._value: Any = default() if default else _MISSING

    @property
    def value_type(self) -> Any:
        """Type of stored value."""
        return self.typ

    @property
    def update_type(self) -> Any:
        """Type of accepted updates (same as value)."""
        return self.typ

    def get(self) -> V:
        """Read the current aggregated value.

        Returns:
            Aggregated value.

        Raises:
            EmptyChannelError: If no value has been set.
        """
        if self._value is _MISSING:
            raise EmptyChannelError(
                f"BinaryOperatorAggregate '{self.key}' has no value"
            )
        return self._value  # type: ignore[return-value]

    def update(self, values: Sequence[V]) -> bool:
        """Apply updates using the binary operator.

        Each value is folded: result = op(current, value).
        Overwrite sentinel bypasses the operator.

        Args:
            values: Values to aggregate.

        Returns:
            True if the value changed.
        """
        if not values:
            return False

        changed = False
        for val in values:
            changed = True
            self._apply_single(val)

        return changed

    def checkpoint(self) -> V:
        """Serialize current value.

        Returns:
            Current aggregated value.
        """
        if self._value is _MISSING:
            return None  # type: ignore[return-value]
        return self._value  # type: ignore[return-value]

    def from_checkpoint(self, data: V) -> None:
        """Restore from checkpoint.

        Args:
            data: Previously checkpointed value.
        """
        if data is not None:
            self._value = data
        elif self._default:
            self._value = self._default()
        else:
            self._value = _MISSING

    def reset(self) -> None:
        """Reset to initial default value."""
        self._value = self._default() if self._default else _MISSING

    # ── Internal ──────────────────────────────────────────────────────

    def _apply_single(self, val: Any) -> None:
        """Apply a single value using operator or Overwrite.

        Args:
            val: Value or Overwrite sentinel.
        """
        is_overwrite, overwrite_val = _check_overwrite(val)

        if is_overwrite:
            self._value = overwrite_val
        elif self._value is _MISSING:
            self._value = val
        else:
            self._value = self._operator(self._value, val)


def _check_overwrite(val: Any) -> tuple[bool, Any]:
    """Check if a value is an Overwrite sentinel.

    Args:
        val: Value to check.

    Returns:
        Tuple of (is_overwrite, unwrapped_value).
    """
    if isinstance(val, Overwrite):
        return True, val.value
    return False, val
