"""Runtime — context injection for agent nodes and tools.

Provides a Runtime object that is passed to every node/tool during
execution, giving access to the store, stream writer, config, and
execution metadata without global state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class ExecutionInfo:
    """Metadata about the current execution state.

    Attributes:
        run_id: Unique run identifier.
        node_name: Current node being executed.
        step_number: Current step number.
        remaining_steps: Steps remaining before max_iterations.
        node_attempt: Current retry attempt (1-based).
        node_first_attempt_time: Timestamp of first attempt.
    """

    run_id: str = ""
    node_name: str = ""
    step_number: int = 0
    remaining_steps: int = 0
    node_attempt: int = 1
    node_first_attempt_time: float = field(default_factory=time.time)


@dataclass
class Runtime(Generic[T]):
    """Runtime context injected into agent nodes and tools.

    Carries all cross-cutting concerns without global state.
    Generic over the user context type T.

    Attributes:
        context: User-defined immutable context (e.g., user_id, auth).
        store: Optional key-value store for long-term memory.
        stream_writer: Optional callback for custom streaming events.
        config: Arbitrary runtime configuration.
        execution_info: Current execution metadata.
    """

    context: T | None = None
    store: Any | None = None
    stream_writer: Callable[[dict[str, Any]], None] | None = None
    config: dict[str, Any] = field(default_factory=dict)
    execution_info: ExecutionInfo = field(default_factory=ExecutionInfo)

    def write_stream(self, data: dict[str, Any]) -> None:
        """Write custom data to the stream.

        Args:
            data: Data to stream to the client.
        """
        if self.stream_writer:
            self.stream_writer(data)

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a configuration value.

        Args:
            key: Config key.
            default: Default value.

        Returns:
            Config value or default.
        """
        return self.config.get(key, default)

    def with_step(self, step_number: int, remaining: int) -> Runtime[T]:
        """Create a copy with updated step info.

        Args:
            step_number: Current step number.
            remaining: Steps remaining.

        Returns:
            New Runtime with updated execution_info.
        """
        new_info = ExecutionInfo(
            run_id=self.execution_info.run_id,
            node_name=self.execution_info.node_name,
            step_number=step_number,
            remaining_steps=remaining,
            node_attempt=self.execution_info.node_attempt,
            node_first_attempt_time=self.execution_info.node_first_attempt_time,
        )
        return Runtime(
            context=self.context,
            store=self.store,
            stream_writer=self.stream_writer,
            config=self.config,
            execution_info=new_info,
        )
