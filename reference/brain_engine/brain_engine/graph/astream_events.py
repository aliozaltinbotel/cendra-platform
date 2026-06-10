"""Event streaming — astream_events for graph execution.

Emits typed events for every significant action during graph
execution: LLM calls, tool calls, node start/end, checkpoints.
Enables real-time UI updates and observability.

Example::

    async for event in astream_events(graph, input_data):
        if event.kind == "on_llm_stream":
            print(event.data["chunk"], end="")

Based on: LangGraph/LangChain astream_events v2.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class EventKind(StrEnum):
    """Types of events emitted during execution."""

    ON_CHAIN_START = "on_chain_start"
    ON_CHAIN_END = "on_chain_end"
    ON_LLM_START = "on_llm_start"
    ON_LLM_STREAM = "on_llm_stream"
    ON_LLM_END = "on_llm_end"
    ON_TOOL_START = "on_tool_start"
    ON_TOOL_END = "on_tool_end"
    ON_NODE_START = "on_node_start"
    ON_NODE_END = "on_node_end"
    ON_CHECKPOINT = "on_checkpoint"
    ON_INTERRUPT = "on_interrupt"
    ON_CUSTOM = "on_custom"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """A single event from graph execution.

    Attributes:
        kind: Event type.
        name: Name of the component (node, tool, model).
        data: Event payload.
        run_id: Unique run identifier.
        parent_run_id: Parent run for nested events.
        timestamp_ms: When the event occurred.
        tags: Optional tags for filtering.
        metadata: Additional metadata.
    """

    kind: str
    name: str = ""
    data: Any = None
    run_id: str = ""
    parent_run_id: str = ""
    timestamp_ms: int = 0
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class EventEmitter:
    """Collects and buffers events during execution.

    Nodes, tools, and middleware push events into the emitter.
    The ``astream_events`` consumer reads them asynchronously.
    """

    def __init__(self) -> None:
        self._events: list[StreamEvent] = []
        self._run_id: str = str(uuid.uuid4())[:12]

    @property
    def run_id(self) -> str:
        """Current run ID."""
        return self._run_id

    def emit(
        self,
        kind: str,
        name: str = "",
        data: Any = None,
        *,
        parent_run_id: str = "",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> StreamEvent:
        """Emit a new event.

        Args:
            kind: Event type (from EventKind).
            name: Component name.
            data: Event payload.
            parent_run_id: Parent run for nesting.
            tags: Optional tags.
            metadata: Optional metadata.

        Returns:
            The emitted StreamEvent.
        """
        event = StreamEvent(
            kind=kind,
            name=name,
            data=data,
            run_id=self._run_id,
            parent_run_id=parent_run_id,
            timestamp_ms=int(time.time() * 1000),
            tags=tags,
            metadata=metadata or {},
        )
        self._events.append(event)
        return event

    def emit_node_start(self, node_name: str) -> StreamEvent:
        """Emit a node start event.

        Args:
            node_name: Name of the starting node.

        Returns:
            Emitted event.
        """
        return self.emit(
            EventKind.ON_NODE_START,
            name=node_name,
            data={"node": node_name},
        )

    def emit_node_end(
        self,
        node_name: str,
        output: Any = None,
    ) -> StreamEvent:
        """Emit a node end event.

        Args:
            node_name: Name of the completed node.
            output: Node output data.

        Returns:
            Emitted event.
        """
        return self.emit(
            EventKind.ON_NODE_END,
            name=node_name,
            data={"node": node_name, "output": output},
        )

    def emit_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> StreamEvent:
        """Emit a tool call start event.

        Args:
            tool_name: Tool being called.
            tool_input: Tool arguments.

        Returns:
            Emitted event.
        """
        return self.emit(
            EventKind.ON_TOOL_START,
            name=tool_name,
            data={"tool": tool_name, "input": tool_input},
        )

    def emit_tool_end(
        self,
        tool_name: str,
        output: str,
    ) -> StreamEvent:
        """Emit a tool call end event.

        Args:
            tool_name: Tool that finished.
            output: Tool result.

        Returns:
            Emitted event.
        """
        return self.emit(
            EventKind.ON_TOOL_END,
            name=tool_name,
            data={"tool": tool_name, "output": output},
        )

    def flush(self) -> list[StreamEvent]:
        """Get and clear all buffered events.

        Returns:
            List of events since last flush.
        """
        events = list(self._events)
        self._events.clear()
        return events

    @property
    def event_count(self) -> int:
        """Total events emitted (including flushed)."""
        return len(self._events)


async def astream_events(
    graph: Any,
    input_data: dict[str, Any],
    config: dict[str, Any] | None = None,
    *,
    include_kinds: list[str] | None = None,
    exclude_kinds: list[str] | None = None,
    include_tags: list[str] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream events from graph execution.

    Wraps the graph's stream method and emits events for
    each step transition.

    Args:
        graph: CompiledGraph instance.
        input_data: Initial state.
        config: Execution config.
        include_kinds: Only emit these event kinds.
        exclude_kinds: Skip these event kinds.
        include_tags: Only emit events with these tags.

    Yields:
        StreamEvent objects matching the filters.
    """
    emitter = EventEmitter()
    step = 0

    emitter.emit(EventKind.ON_CHAIN_START, name="graph", data=input_data)
    yield emitter.flush()[-1]

    async for state in graph.stream(input_data, config):
        step += 1
        event = emitter.emit(
            EventKind.ON_NODE_END,
            name=f"step_{step}",
            data=state,
        )
        if _should_emit(event, include_kinds, exclude_kinds, include_tags):
            yield event

    end_event = emitter.emit(
        EventKind.ON_CHAIN_END,
        name="graph",
        data={"steps": step},
    )
    yield end_event


def _should_emit(
    event: StreamEvent,
    include_kinds: list[str] | None,
    exclude_kinds: list[str] | None,
    include_tags: list[str] | None,
) -> bool:
    """Check if an event passes the configured filters.

    Args:
        event: Event to check.
        include_kinds: Whitelist of kinds.
        exclude_kinds: Blacklist of kinds.
        include_tags: Required tags.

    Returns:
        True if event should be yielded.
    """
    if include_kinds and event.kind not in include_kinds:
        return False
    if exclude_kinds and event.kind in exclude_kinds:
        return False
    if include_tags:
        if not any(tag in event.tags for tag in include_tags):
            return False
    return True
