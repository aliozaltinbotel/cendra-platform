"""Stream modes — typed streaming output from graph execution.

Provides 5 stream modes for CompiledGraph execution:
- ``values``: Full state after each super-step
- ``updates``: Only the delta (node output) per step
- ``messages``: LLM token streaming (placeholder for future)
- ``custom``: User-injected stream data via StreamWriter
- ``debug``: Full event trace with checkpoints

Example::

    streamer = GraphStreamer(mode="updates")
    async for event in streamer.stream(graph, input_data):
        print(event.mode, event.node, event.data)

Based on: LangGraph stream_mode parameter
(langgraph/pregel/__init__.py).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class StreamMode(StrEnum):
    """Available graph streaming modes."""

    VALUES = "values"
    UPDATES = "updates"
    MESSAGES = "messages"
    CUSTOM = "custom"
    DEBUG = "debug"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """A single event emitted during graph streaming.

    Attributes:
        mode: Which stream mode produced this event.
        node: Node that produced the event (empty for state-level).
        data: Event payload.
        step: Super-step number.
        timestamp_ms: When the event was produced.
        event_id: Unique event identifier.
    """

    mode: str
    node: str = ""
    data: Any = None
    step: int = 0
    timestamp_ms: int = 0
    event_id: str = ""


class StreamWriter:
    """Allows nodes to inject custom events into the stream.

    Passed to node functions when using ``custom`` stream mode.
    Nodes call ``writer.write(data)`` to emit custom events.
    """

    def __init__(self) -> None:
        self._buffer: list[Any] = []

    def write(self, data: Any) -> None:
        """Write a custom event to the stream.

        Args:
            data: Any data to emit as a stream event.
        """
        self._buffer.append(data)

    def flush(self) -> list[Any]:
        """Retrieve and clear all buffered events.

        Returns:
            List of buffered data items.
        """
        items = list(self._buffer)
        self._buffer.clear()
        return items


class GraphStreamer:
    """Multi-mode streaming wrapper for CompiledGraph.

    Wraps a compiled graph's execution and emits typed
    ``StreamEvent`` objects based on the selected mode(s).

    Args:
        modes: One or more StreamMode values.
    """

    def __init__(
        self,
        modes: list[str] | str | None = None,
    ) -> None:
        if modes is None:
            self._modes = [StreamMode.VALUES]
        elif isinstance(modes, str):
            self._modes = [StreamMode(modes)]
        else:
            self._modes = [StreamMode(m) for m in modes]
        self._writer = StreamWriter()

    @property
    def writer(self) -> StreamWriter:
        """Access the StreamWriter for custom mode."""
        return self._writer

    async def stream(
        self,
        graph: Any,
        input_data: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream graph execution with the configured modes.

        Iterates through the graph's native stream and converts
        each step into typed StreamEvent objects.

        Args:
            graph: CompiledGraph instance.
            input_data: Initial state values.
            config: Optional execution config.

        Yields:
            StreamEvent objects for each configured mode.
        """
        step = 0
        previous_state: dict[str, Any] = {}

        async for state in graph.stream(input_data, config):
            step += 1
            for mode in self._modes:
                events = self._produce_events(
                    mode, state, previous_state, step,
                )
                for event in events:
                    yield event
            previous_state = _safe_copy(state)

    def _produce_events(
        self,
        mode: StreamMode,
        state: dict[str, Any],
        previous: dict[str, Any],
        step: int,
    ) -> list[StreamEvent]:
        """Produce events for a given mode and state.

        Args:
            mode: Stream mode to produce for.
            state: Current full state.
            previous: Previous state (for diff).
            step: Current step number.

        Returns:
            List of StreamEvent objects.
        """
        if mode == StreamMode.VALUES:
            return [self._values_event(state, step)]
        if mode == StreamMode.UPDATES:
            return [self._updates_event(state, previous, step)]
        if mode == StreamMode.CUSTOM:
            return self._custom_events(step)
        if mode == StreamMode.DEBUG:
            return self._debug_events(state, previous, step)
        return [self._values_event(state, step)]

    def _values_event(
        self,
        state: dict[str, Any],
        step: int,
    ) -> StreamEvent:
        """Create a VALUES mode event (full state snapshot).

        Args:
            state: Full current state.
            step: Step number.

        Returns:
            StreamEvent with full state.
        """
        return StreamEvent(
            mode=StreamMode.VALUES,
            data=state,
            step=step,
            timestamp_ms=_now_ms(),
            event_id=_event_id(),
        )

    def _updates_event(
        self,
        state: dict[str, Any],
        previous: dict[str, Any],
        step: int,
    ) -> StreamEvent:
        """Create an UPDATES mode event (state delta only).

        Args:
            state: Current state.
            previous: Previous state for diffing.
            step: Step number.

        Returns:
            StreamEvent with only changed keys.
        """
        delta = _compute_delta(state, previous)
        return StreamEvent(
            mode=StreamMode.UPDATES,
            data=delta,
            step=step,
            timestamp_ms=_now_ms(),
            event_id=_event_id(),
        )

    def _custom_events(self, step: int) -> list[StreamEvent]:
        """Collect custom events from StreamWriter buffer.

        Args:
            step: Step number.

        Returns:
            List of StreamEvents from writer buffer.
        """
        items = self._writer.flush()
        return [
            StreamEvent(
                mode=StreamMode.CUSTOM,
                data=item,
                step=step,
                timestamp_ms=_now_ms(),
                event_id=_event_id(),
            )
            for item in items
        ]

    def _debug_events(
        self,
        state: dict[str, Any],
        previous: dict[str, Any],
        step: int,
    ) -> list[StreamEvent]:
        """Create DEBUG mode events (full state + delta).

        Args:
            state: Current state.
            previous: Previous state.
            step: Step number.

        Returns:
            List with both values and updates events.
        """
        return [
            self._values_event(state, step),
            self._updates_event(state, previous, step),
        ]


# ── Helpers ──────────────────────────────────────────────────────────── #


def _compute_delta(
    current: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Compute the difference between two state dicts.

    Args:
        current: New state.
        previous: Old state.

    Returns:
        Dict with only changed or new keys.
    """
    delta: dict[str, Any] = {}
    for key, value in current.items():
        if key not in previous or previous[key] != value:
            delta[key] = value
    return delta


def _safe_copy(state: dict[str, Any]) -> dict[str, Any]:
    """Create a shallow copy of state for comparison.

    Args:
        state: State dict to copy.

    Returns:
        Shallow copy.
    """
    return dict(state)


def _now_ms() -> int:
    """Current timestamp in milliseconds."""
    return int(time.time() * 1000)


def _event_id() -> str:
    """Generate a short unique event ID."""
    return str(uuid.uuid4())[:12]
