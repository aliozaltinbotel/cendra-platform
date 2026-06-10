"""Base Channel — abstract interface for typed state containers.

Channels decouple state management from node execution. Each channel
stores a single logical value with custom update semantics (replace,
accumulate, reduce). Supports versioning, consumption, and deferred
availability for the Pregel BSP execution model.

Based on: LangGraph BaseChannel (langgraph/channels/base.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, Generic, Sequence, TypeVar

Value = TypeVar("Value")
Update = TypeVar("Update")
Checkpoint = TypeVar("Checkpoint")


class EmptyChannelError(Exception):
    """Raised when reading from a channel with no value."""


class InvalidUpdateError(Exception):
    """Raised when a channel receives an invalid update."""


class BaseChannel(ABC, Generic[Value, Update, Checkpoint]):
    """Abstract base for all channel implementations.

    Channels are the state primitives in the Pregel execution model.
    Each channel stores a value, accepts updates, and controls its
    own lifecycle (availability, consumption, finishing).

    Attributes:
        key: Channel identifier in the state dict.
        typ: Type specification for the value.
    """

    key: str = ""
    typ: Any = None

    @property
    @abstractmethod
    def value_type(self) -> Any:
        """Type of the stored value."""
        ...

    @property
    @abstractmethod
    def update_type(self) -> Any:
        """Type of accepted updates."""
        ...

    @abstractmethod
    def get(self) -> Value:
        """Read the current channel value.

        Returns:
            Current value.

        Raises:
            EmptyChannelError: If no value is available.
        """
        ...

    @abstractmethod
    def update(self, values: Sequence[Update]) -> bool:
        """Apply one or more updates to the channel.

        Args:
            values: Sequence of update values.

        Returns:
            True if the channel state changed.

        Raises:
            InvalidUpdateError: If the update is invalid.
        """
        ...

    @abstractmethod
    def checkpoint(self) -> Checkpoint:
        """Serialize channel state for persistence.

        Returns:
            Serializable checkpoint representation.
        """
        ...

    @abstractmethod
    def from_checkpoint(self, data: Checkpoint) -> None:
        """Restore channel state from a checkpoint.

        Args:
            data: Previously serialized checkpoint data.
        """
        ...

    def is_available(self) -> bool:
        """Whether the channel has a value ready to read.

        Returns:
            True if get() will succeed.
        """
        try:
            self.get()
            return True
        except EmptyChannelError:
            return False

    def consume(self) -> bool:
        """Mark channel as consumed after reading.

        Override in subclasses that support one-time triggers.

        Returns:
            True if state changed (channel was consumed).
        """
        return False

    def finish(self) -> bool:
        """Called when a superstep ends.

        Override in subclasses that defer availability until
        all nodes complete (e.g. LastValueAfterFinish).

        Returns:
            True if the channel became available.
        """
        return False

    def copy(self) -> BaseChannel[Value, Update, Checkpoint]:
        """Create a deep copy of this channel.

        Returns:
            Independent copy.
        """
        return deepcopy(self)
