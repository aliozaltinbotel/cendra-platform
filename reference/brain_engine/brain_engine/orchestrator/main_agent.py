"""
Main Agent - top-level orchestrator for the Airbnb Brain Engine.

Initializes all engine components (memory, intent classification, state
management) and wires them to integration services. The ``run`` method
accepts an incoming event and yields AG-UI protocol events as an async
generator.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from brain_engine.orchestrator.event_router import EventRouter, Flow
from brain_engine.orchestrator.action_executor import ActionExecutor, Action, ActionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AG-UI event types (subset matching brain_engine.streaming.event_types)
# ---------------------------------------------------------------------------


class AGUIEventType(str, Enum):
    """Server-Sent Event types for the AG-UI protocol."""

    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    CUSTOM = "CUSTOM"


@dataclass
class AGUIEvent:
    """A single AG-UI protocol event."""

    type: AGUIEventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        """Serialize to SSE wire format."""
        import json

        payload = json.dumps({"type": self.type.value, **self.data})
        return f"event: {self.type.value}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------


@dataclass
class BrainEngineConfig:
    """Configuration for the Brain Engine components."""

    # Integration instances (set at startup)
    voice_provider: Any | None = None
    messaging_provider: Any | None = None
    lock_provider: Any | None = None
    cleaning_provider: Any | None = None
    calendar_provider: Any | None = None
    airbnb_api: Any | None = None
    photo_comparator: Any | None = None

    # Engine settings
    max_turns: int = 50
    session_timeout_seconds: int = 3600
    enable_guardrails: bool = True


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """Mutable state for a single conversation session."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    turn_count: int = 0
    current_intent: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    memory_context: list[dict[str, str]] = field(default_factory=list)
    pending_actions: list[Action] = field(default_factory=list)
    completed_actions: list[ActionResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_snapshot(self) -> dict[str, Any]:
        """Serialize state for STATE_SNAPSHOT events."""
        return {
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "current_intent": self.current_intent,
            "slots": self.slots,
            "pending_actions": len(self.pending_actions),
            "completed_actions": len(self.completed_actions),
        }


# ---------------------------------------------------------------------------
# Main Agent
# ---------------------------------------------------------------------------


class MainAgent:
    """
    Entry point for the Airbnb Brain Engine.

    Orchestrates intent classification, slot filling, action execution,
    and response generation. Yields AG-UI events for each step so the
    frontend can render real-time progress.

    Usage::

        config = BrainEngineConfig(
            voice_provider=elevenlabs,
            messaging_provider=whatsapp,
            lock_provider=nuki,
            ...
        )
        agent = MainAgent(config)
        async for event in agent.run(incoming_event):
            send_sse(event.to_sse())
    """

    def __init__(self, config: BrainEngineConfig) -> None:
        self._config = config
        self._router = EventRouter()
        self._executor = ActionExecutor(
            voice=config.voice_provider,
            messaging=config.messaging_provider,
            lock=config.lock_provider,
            cleaning=config.cleaning_provider,
            calendar=config.calendar_provider,
            airbnb=config.airbnb_api,
            comparator=config.photo_comparator,
        )
        self._sessions: dict[str, SessionState] = {}

    def get_or_create_session(
        self, session_id: str | None = None
    ) -> SessionState:
        """Retrieve an existing session or create a new one."""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        state = SessionState(session_id=session_id or uuid.uuid4().hex)
        self._sessions[state.session_id] = state
        return state

    async def run(
        self,
        event: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> AsyncGenerator[AGUIEvent, None]:
        """Process an incoming event and yield AG-UI events.

        Args:
            event: The incoming event payload. Expected keys:
                - ``type``: Event type (``"message"``, ``"webhook"``, etc.)
                - ``content``: The user message or webhook body.
                - ``session_id``: Optional session identifier.
            session_id: Override session ID (also checked in event dict).

        Yields:
            :class:`AGUIEvent` instances for each processing step.
        """
        sid = session_id or event.get("session_id")
        state = self.get_or_create_session(sid)
        state.turn_count += 1
        run_id = uuid.uuid4().hex

        # --- RUN_STARTED ---
        yield AGUIEvent(
            type=AGUIEventType.RUN_STARTED,
            data={"run_id": run_id, "session_id": state.session_id},
        )

        try:
            # --- Intent classification ---
            user_content = event.get("content", "")
            flow = await self._classify_and_route(user_content, state)

            # --- STATE_SNAPSHOT ---
            yield AGUIEvent(
                type=AGUIEventType.STATE_SNAPSHOT,
                data={"snapshot": state.to_snapshot()},
            )

            # --- Execute actions from the flow ---
            if flow.actions:
                for action in flow.actions:
                    state.pending_actions.append(action)

                    # TOOL_CALL_START
                    yield AGUIEvent(
                        type=AGUIEventType.TOOL_CALL_START,
                        data={
                            "tool_call_id": action.action_id,
                            "tool_name": action.action_type,
                            "args": action.params,
                        },
                    )

                    # Execute
                    result = await self._executor.execute(action)
                    state.completed_actions.append(result)
                    state.pending_actions.remove(action)

                    # TOOL_CALL_END
                    yield AGUIEvent(
                        type=AGUIEventType.TOOL_CALL_END,
                        data={
                            "tool_call_id": action.action_id,
                            "result": result.to_dict(),
                        },
                    )

            # --- Generate response text ---
            response_text = await self._generate_response(flow, state)

            # TEXT_MESSAGE_START
            message_id = uuid.uuid4().hex
            yield AGUIEvent(
                type=AGUIEventType.TEXT_MESSAGE_START,
                data={"message_id": message_id, "role": "assistant"},
            )

            # Stream response in chunks
            for chunk in self._chunk_text(response_text):
                yield AGUIEvent(
                    type=AGUIEventType.TEXT_MESSAGE_CONTENT,
                    data={"message_id": message_id, "content": chunk},
                )

            # TEXT_MESSAGE_END
            yield AGUIEvent(
                type=AGUIEventType.TEXT_MESSAGE_END,
                data={"message_id": message_id},
            )

            # --- RUN_FINISHED ---
            yield AGUIEvent(
                type=AGUIEventType.RUN_FINISHED,
                data={"run_id": run_id},
            )

        except Exception as exc:
            logger.exception("Error in agent run %s", run_id)
            yield AGUIEvent(
                type=AGUIEventType.RUN_ERROR,
                data={"run_id": run_id, "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    async def _classify_and_route(
        self, content: str, state: SessionState
    ) -> Flow:
        """Classify intent and route to the appropriate flow."""
        flow = await self._router.route(
            {
                "content": content,
                "current_intent": state.current_intent,
                "slots": state.slots,
                "memory": state.memory_context,
            }
        )
        state.current_intent = flow.intent
        state.slots.update(flow.extracted_slots)
        return flow

    async def _generate_response(
        self, flow: Flow, state: SessionState
    ) -> str:
        """Generate the assistant response text based on the flow result."""
        if flow.response_template:
            try:
                return flow.response_template.format(**state.slots)
            except KeyError:
                return flow.response_template

        parts: list[str] = []
        if flow.actions:
            completed_summaries = [
                r.summary for r in state.completed_actions[-len(flow.actions) :]
            ]
            parts.extend(completed_summaries)

        if not parts:
            parts.append(
                f"I understand you're asking about {flow.intent or 'something'}. "
                "How can I help further?"
            )

        return " ".join(parts)

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 80) -> list[str]:
        """Split text into chunks for streaming."""
        if len(text) <= chunk_size:
            return [text]
        chunks: list[str] = []
        words = text.split()
        current: list[str] = []
        length = 0
        for word in words:
            if length + len(word) + 1 > chunk_size and current:
                chunks.append(" ".join(current))
                current = [word]
                length = len(word)
            else:
                current.append(word)
                length += len(word) + 1
        if current:
            chunks.append(" ".join(current))
        return chunks
