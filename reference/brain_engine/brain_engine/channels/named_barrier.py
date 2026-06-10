"""NamedBarrierValue — fan-in synchronization channel.

Waits for updates from all named sources before becoming available.
Used for multi-edge fan-in patterns: ``add_edge([A, B], C)`` requires
both A and B to complete before C can trigger.

Based on: LangGraph DynamicBarrierValue / WaitForNames
(langgraph/channels/dynamic_barrier_value.py).
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from brain_engine.channels.base import (
    BaseChannel,
    EmptyChannelError,
    InvalidUpdateError,
)

logger = logging.getLogger(__name__)


class NamedBarrierValue(BaseChannel[set[str], str, list[str]]):
    """Channel that blocks until all expected names have reported.

    When all registered names have called ``update()``, the channel
    becomes available and returns the set of names. Useful for
    synchronizing parallel branches before a join node.

    Args:
        names: Set of names that must report before availability.

    Example::

        barrier = NamedBarrierValue(names={"node_a", "node_b"})
        barrier.update(["node_a"])  # not yet available
        barrier.update(["node_b"])  # now available
        assert barrier.get() == {"node_a", "node_b"}
    """

    def __init__(self, names: set[str] | None = None) -> None:
        self._expected: set[str] = set(names or set())
        self._received: set[str] = set()
        self.key = ""
        self.typ = set

    @property
    def value_type(self) -> type:
        """Type of the stored value."""
        return set

    @property
    def update_type(self) -> type:
        """Type of accepted updates."""
        return str

    def get(self) -> set[str]:
        """Read the barrier value (all received names).

        Returns:
            Set of names that have reported.

        Raises:
            EmptyChannelError: If not all names have reported.
        """
        if not self._is_satisfied():
            raise EmptyChannelError(
                f"Barrier waiting: received {self._received}, "
                f"expected {self._expected}"
            )
        return set(self._received)

    def update(self, values: Sequence[str]) -> bool:
        """Register one or more names as completed.

        Args:
            values: Names that have completed their work.

        Returns:
            True if the barrier became satisfied.

        Raises:
            InvalidUpdateError: If a name is not in expected set.
        """
        changed = False
        for name in values:
            if self._expected and name not in self._expected:
                raise InvalidUpdateError(
                    f"Unexpected name '{name}'. "
                    f"Expected: {self._expected}"
                )
            if name not in self._received:
                self._received.add(name)
                changed = True
        return changed

    def checkpoint(self) -> list[str]:
        """Serialize barrier state.

        Returns:
            List of received names.
        """
        return sorted(self._received)

    def from_checkpoint(self, data: list[str]) -> None:
        """Restore barrier state.

        Args:
            data: Previously checkpointed name list.
        """
        self._received = set(data)

    def is_available(self) -> bool:
        """Whether all expected names have reported.

        Returns:
            True if barrier is satisfied.
        """
        return self._is_satisfied()

    def consume(self) -> bool:
        """Reset the barrier after it's been read.

        Returns:
            True (barrier always resets after consumption).
        """
        if self._is_satisfied():
            self._received.clear()
            return True
        return False

    def _is_satisfied(self) -> bool:
        """Check whether all expected names have reported.

        Returns:
            True if received >= expected.
        """
        if not self._expected:
            return bool(self._received)
        return self._expected.issubset(self._received)

    @property
    def expected_names(self) -> set[str]:
        """Return the set of expected names."""
        return set(self._expected)

    @property
    def received_names(self) -> set[str]:
        """Return the set of received names."""
        return set(self._received)

    @property
    def pending_names(self) -> set[str]:
        """Return names that haven't reported yet.

        Returns:
            Set of expected names not yet received.
        """
        return self._expected - self._received
