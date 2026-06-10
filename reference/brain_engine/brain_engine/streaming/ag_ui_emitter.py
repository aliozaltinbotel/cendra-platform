"""AG-UI Emitter - Emits AG-UI protocol events for SSE streaming.

Implements the event emission layer of the AG-UI protocol, producing
properly formatted events that can be streamed to frontends via
Server-Sent Events (SSE). Uses async generators for non-blocking
event delivery.

AG-UI Protocol Events:
- TEXT_MESSAGE_CONTENT: Streaming text chunks
- STATE_DELTA: Partial state updates
- STATE_SNAPSHOT: Full state snapshots
- TOOL_CALL_START / TOOL_CALL_END: Tool execution lifecycle
- RUN_STARTED / RUN_FINISHED / RUN_ERROR: Agent run lifecycle
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from brain_engine.streaming.event_types import EventType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AGUIEvent:
    """A single AG-UI protocol event.

    Attributes:
        type: The AG-UI event type.
        data: Event payload dictionary.
        event_id: Unique event identifier.
        timestamp: Unix timestamp of event creation.
    """

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        """Format as a Server-Sent Event string.

        Returns:
            SSE-formatted string with event type and JSON data payload.
        """
        payload = {
            "type": self.type.value,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            **self.data,
        }
        return f"event: {self.type.value}\ndata: {json.dumps(payload)}\n\n"

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dictionary for serialization."""
        return {
            "type": self.type.value,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            **self.data,
        }


class AGUIEmitter:
    """Emits AG-UI protocol events as an async SSE stream.

    Provides typed helper methods for each AG-UI event type and an
    async generator interface for streaming events to connected clients.
    Events are queued internally and yielded through the stream() method.

    Args:
        run_id: Unique identifier for the current agent run.
        buffer_size: Maximum number of events to buffer before backpressure.
    """

    def __init__(
        self,
        run_id: str | None = None,
        buffer_size: int = 256,
    ) -> None:
        self.run_id = run_id or str(uuid.uuid4())
        self._queue: asyncio.Queue[AGUIEvent | None] = asyncio.Queue(
            maxsize=buffer_size
        )
        self._history: list[AGUIEvent] = []
        self._closed = False

    def emit(
        self,
        event_type: EventType,
        data: dict[str, Any] | None = None,
    ) -> AGUIEvent:
        """Create, store, and enqueue an event.

        Args:
            event_type: The AG-UI event type.
            data: Event payload data.

        Returns:
            The created AGUIEvent.
        """
        event = AGUIEvent(
            type=event_type,
            data={"run_id": self.run_id, **(data or {})},
        )
        self._history.append(event)

        if not self._closed:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Event queue full, dropping event: %s", event_type.value
                )

        logger.debug("Emitted: %s", event_type.value)
        return event

    # ── Lifecycle events ────────────────────────────────────────────

    def run_started(self) -> AGUIEvent:
        """Emit a RUN_STARTED event."""
        return self.emit(EventType.RUN_STARTED)

    def run_finished(self) -> AGUIEvent:
        """Emit a RUN_FINISHED event and close the stream."""
        event = self.emit(EventType.RUN_FINISHED)
        self.close()
        return event

    def run_error(self, error: str, details: dict[str, Any] | None = None) -> AGUIEvent:
        """Emit a RUN_ERROR event.

        Args:
            error: Error message.
            details: Optional error details dict.
        """
        return self.emit(
            EventType.RUN_ERROR,
            {"error": error, **(details or {})},
        )

    # ── Text streaming events ───────────────────────────────────────

    def text_message_start(self, message_id: str | None = None) -> AGUIEvent:
        """Start a new text message stream."""
        return self.emit(
            EventType.TEXT_MESSAGE_START,
            {
                "message_id": message_id or str(uuid.uuid4()),
                "role": "assistant",
            },
        )

    def text_message_content(self, content: str) -> AGUIEvent:
        """Emit a chunk of streamed text content.

        Args:
            content: The text chunk to stream.
        """
        return self.emit(
            EventType.TEXT_MESSAGE_CONTENT,
            {"content": content},
        )

    def text_message_end(self) -> AGUIEvent:
        """End the current text message stream."""
        return self.emit(EventType.TEXT_MESSAGE_END)

    # ── State events ────────────────────────────────────────────────

    def state_snapshot(self, state: dict[str, Any]) -> AGUIEvent:
        """Emit a full state snapshot.

        Args:
            state: Complete state dictionary.
        """
        return self.emit(EventType.STATE_SNAPSHOT, {"state": state})

    def state_delta(self, delta: dict[str, Any]) -> AGUIEvent:
        """Emit a partial state update.

        Args:
            delta: Dictionary of changed state fields.
        """
        return self.emit(EventType.STATE_DELTA, {"delta": delta})

    # ── Tool events ─────────────────────────────────────────────────

    def tool_call_start(
        self,
        tool_name: str,
        call_id: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> AGUIEvent:
        """Emit a TOOL_CALL_START event.

        Args:
            tool_name: Name of the tool being invoked.
            call_id: Unique call identifier.
            arguments: Tool call arguments.
        """
        return self.emit(
            EventType.TOOL_CALL_START,
            {
                "tool_name": tool_name,
                "call_id": call_id or str(uuid.uuid4()),
                "arguments": arguments or {},
            },
        )

    def tool_call_end(
        self,
        tool_name: str,
        result: Any = None,
        call_id: str | None = None,
    ) -> AGUIEvent:
        """Emit a TOOL_CALL_END event.

        Args:
            tool_name: Name of the tool that completed.
            result: Tool execution result.
            call_id: The call identifier matching the start event.
        """
        return self.emit(
            EventType.TOOL_CALL_END,
            {
                "tool_name": tool_name,
                "result": result,
                "call_id": call_id,
            },
        )

    # ── Flow/slot events ────────────────────────────────────────────

    def flow_started(self, flow_name: str, initial_state: str) -> AGUIEvent:
        """Emit a FLOW_STARTED event."""
        return self.emit(
            EventType.FLOW_STARTED,
            {"flow_name": flow_name, "state": initial_state},
        )

    def flow_state_changed(
        self, flow_name: str, from_state: str, to_state: str
    ) -> AGUIEvent:
        """Emit a FLOW_STATE_CHANGED event."""
        return self.emit(
            EventType.FLOW_STATE_CHANGED,
            {
                "flow_name": flow_name,
                "from_state": from_state,
                "to_state": to_state,
            },
        )

    def flow_completed(
        self, flow_name: str, result: dict[str, Any] | None = None
    ) -> AGUIEvent:
        """Emit a FLOW_COMPLETED event."""
        return self.emit(
            EventType.FLOW_COMPLETED,
            {"flow_name": flow_name, "result": result or {}},
        )

    def slot_filled(self, slot_name: str, value: Any) -> AGUIEvent:
        """Emit a SLOT_FILLED event."""
        return self.emit(
            EventType.SLOT_FILLED,
            {"slot_name": slot_name, "value": value},
        )

    def slot_requested(self, slot_name: str, prompt: str = "") -> AGUIEvent:
        """Emit a SLOT_REQUESTED event."""
        return self.emit(
            EventType.SLOT_REQUESTED,
            {"slot_name": slot_name, "prompt": prompt},
        )

    # ── AI Reasoning events ──────────────────────────────────────────

    def reasoning_start(self, context: str = "") -> AGUIEvent:
        """Start a new reasoning chain."""
        return self.emit(
            EventType.REASONING_START,
            {"context": context},
        )

    def reasoning_step(
        self,
        step_type: str,
        content: str,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> AGUIEvent:
        """Emit a single reasoning step.

        Args:
            step_type: "REASONING" or "ACTION".
            content: Description of the reasoning/action.
            confidence: Confidence level 0-1.
            metadata: Additional step metadata.
        """
        return self.emit(
            EventType.REASONING_STEP,
            {
                "step_type": step_type,
                "content": content,
                "confidence": confidence,
                "metadata": metadata or {},
            },
        )

    def reasoning_end(self, summary: str = "") -> AGUIEvent:
        """End the current reasoning chain."""
        return self.emit(EventType.REASONING_END, {"summary": summary})

    def action_started(self, action: str, target: str = "", details: dict[str, Any] | None = None) -> AGUIEvent:
        """Emit when an action begins execution."""
        return self.emit(
            EventType.ACTION_STARTED,
            {"action": action, "target": target, "details": details or {}},
        )

    def action_completed(self, action: str, result: str = "", success: bool = True) -> AGUIEvent:
        """Emit when an action completes."""
        return self.emit(
            EventType.ACTION_COMPLETED,
            {"action": action, "result": result, "success": success},
        )

    def intent_classified(
        self,
        intent: str,
        confidence: float,
        raw_label: str | None = None,
    ) -> AGUIEvent:
        """Emit when intent classification completes."""
        data: dict[str, Any] = {"intent": intent, "confidence": confidence}
        if raw_label is not None:
            data["raw_label"] = raw_label
        return self.emit(EventType.INTENT_CLASSIFIED, data)

    def memory_retrieved(
        self,
        tier: str,
        query: str,
        hits: list[dict[str, Any]],
        latency_ms: float,
    ) -> AGUIEvent:
        """Emit when a memory tier returns hits for a query."""
        return self.emit(
            EventType.MEMORY_RETRIEVED,
            {"tier": tier, "query": query, "hits": hits, "latency_ms": latency_ms},
        )

    def rag_hit(
        self,
        query: str,
        source: str,
        docs: list[dict[str, Any]],
        latency_ms: float,
    ) -> AGUIEvent:
        """Emit when RAG / vector search returns documents."""
        return self.emit(
            EventType.RAG_HIT,
            {"query": query, "source": source, "docs": docs, "latency_ms": latency_ms},
        )

    def guardrail_check(
        self,
        check_name: str,
        decision: str,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AGUIEvent:
        """Emit when a guardrail rule runs."""
        data: dict[str, Any] = {"check_name": check_name, "decision": decision}
        if reason is not None:
            data["reason"] = reason
        if details is not None:
            data["details"] = details
        return self.emit(EventType.GUARDRAIL_CHECK, data)

    def cognitive_mode_changed(
        self,
        from_mode: str,
        to_mode: str,
        trigger: str,
        reasoning: str,
    ) -> AGUIEvent:
        """Emit when the cognitive controller switches modes."""
        return self.emit(
            EventType.COGNITIVE_MODE_CHANGED,
            {
                "from": from_mode,
                "to": to_mode,
                "trigger": trigger,
                "reasoning": reasoning,
            },
        )

    # ── Sentiment & guest events ─────────────────────────────────────

    def sentiment_update(
        self,
        guest_name: str,
        sentiment: str,
        score: float = 0.0,
        reason: str = "",
    ) -> AGUIEvent:
        """Emit guest sentiment update.

        Args:
            guest_name: Name of the guest.
            sentiment: "POSITIVE", "NEGATIVE", "NEUTRAL".
            score: Sentiment score -1.0 to 1.0.
            reason: Why this sentiment was detected.
        """
        return self.emit(
            EventType.SENTIMENT_UPDATE,
            {
                "guest_name": guest_name,
                "sentiment": sentiment,
                "score": score,
                "reason": reason,
            },
        )

    def guest_status_update(
        self,
        guest_name: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> AGUIEvent:
        """Emit guest status change (checked_in, checked_out, en_route, etc.)."""
        return self.emit(
            EventType.GUEST_STATUS_UPDATE,
            {"guest_name": guest_name, "status": status, "details": details or {}},
        )

    # ── Call events ──────────────────────────────────────────────────

    def call_started(
        self,
        call_id: str,
        phone_number: str,
        recipient_name: str,
        call_type: str = "",
    ) -> AGUIEvent:
        """Emit when a phone call is initiated."""
        return self.emit(
            EventType.CALL_STARTED,
            {
                "call_id": call_id,
                "phone_number": phone_number,
                "recipient_name": recipient_name,
                "call_type": call_type,
            },
        )

    def call_ended(
        self,
        call_id: str,
        duration_seconds: float = 0,
        status: str = "completed",
        summary: str = "",
    ) -> AGUIEvent:
        """Emit when a phone call ends."""
        return self.emit(
            EventType.CALL_ENDED,
            {
                "call_id": call_id,
                "duration_seconds": duration_seconds,
                "status": status,
                "summary": summary,
            },
        )

    def call_transcript_update(self, call_id: str, role: str, message: str) -> AGUIEvent:
        """Emit a transcript turn from an active call."""
        return self.emit(
            EventType.CALL_TRANSCRIPT_UPDATE,
            {"call_id": call_id, "role": role, "message": message},
        )

    # ── Streaming interface ─────────────────────────────────────────

    async def stream(self) -> AsyncIterator[AGUIEvent]:
        """Async generator that yields events as they are emitted.

        Yields events from the internal queue until the emitter is closed.
        This is the primary interface for SSE streaming to clients.

        Yields:
            AGUIEvent objects in emission order.
        """
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event

    async def stream_sse(self) -> AsyncIterator[str]:
        """Async generator that yields SSE-formatted strings.

        Convenience wrapper over stream() that formats events for
        direct use with SSE transports (e.g., FastAPI StreamingResponse).

        Yields:
            SSE-formatted event strings.
        """
        async for event in self.stream():
            yield event.to_sse()

    def close(self) -> None:
        """Signal the end of the event stream.

        Puts a None sentinel into the queue to terminate stream() iterators.
        """
        if not self._closed:
            self._closed = True
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                logger.warning("Could not enqueue close sentinel")

    # ── Utilities ───────────────────────────────────────────────────

    @property
    def events(self) -> list[AGUIEvent]:
        """All events emitted during this run (read-only copy)."""
        return list(self._history)

    def drain(self) -> list[AGUIEvent]:
        """Return and clear all buffered events from the history.

        Note: This does not affect the async queue. Use stream() for
        async consumption.
        """
        events = list(self._history)
        self._history.clear()
        return events

    @property
    def is_closed(self) -> bool:
        """Whether the emitter has been closed."""
        return self._closed
