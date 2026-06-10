"""
AG-UI Adapter - bridges the Brain Engine to the AG-UI SSE protocol.

Wraps the MainAgent and converts every processing step into Server-Sent
Events conforming to the AG-UI specification. This is the primary
interface consumed by the HTTP server layer.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from brain_engine.orchestrator.main_agent import MainAgent, AGUIEvent, AGUIEventType, BrainEngineConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


@dataclass
class AGUIRequest:
    """Parsed AG-UI request from the frontend."""

    thread_id: str
    run_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    context: list[dict[str, Any]] = field(default_factory=list)
    forwarded_props: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AGUIRequest:
        """Parse a raw request payload into an :class:`AGUIRequest`."""
        return cls(
            thread_id=data.get("threadId", data.get("thread_id", uuid.uuid4().hex)),
            run_id=data.get("runId", data.get("run_id", uuid.uuid4().hex)),
            messages=data.get("messages", []),
            tools=data.get("tools", []),
            context=data.get("context", []),
            forwarded_props=data.get("forwardedProps", {}),
        )

    @property
    def last_user_message(self) -> str:
        """Extract the content of the most recent user message."""
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                # Handle structured content (list of parts)
                if isinstance(content, list):
                    return " ".join(
                        p.get("text", "") for p in content if p.get("type") == "text"
                    )
        return ""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class AGUIAdapter:
    """
    Wraps the Brain Engine and produces AG-UI compliant SSE byte streams.

    This is the main bridge between the engine internals and the HTTP
    transport. The ``dispatch_request`` method accepts raw input, runs the
    agent, and yields SSE-formatted bytes ready to be written to the
    response stream.

    Usage::

        adapter = AGUIAdapter(config)

        # In your HTTP handler (e.g. FastAPI / Starlette):
        async def sse_endpoint(request):
            body = await request.json()
            return StreamingResponse(
                adapter.dispatch_request(body),
                media_type="text/event-stream",
            )
    """

    def __init__(
        self,
        config: BrainEngineConfig | None = None,
        *,
        agent: MainAgent | None = None,
    ) -> None:
        if agent is not None:
            self._agent = agent
        elif config is not None:
            self._agent = MainAgent(config)
        else:
            self._agent = MainAgent(BrainEngineConfig())

    @property
    def agent(self) -> MainAgent:
        """Access the underlying MainAgent instance."""
        return self._agent

    async def dispatch_request(
        self, input_data: dict[str, Any]
    ) -> AsyncGenerator[bytes, None]:
        """Process an AG-UI request and yield SSE bytes.

        Args:
            input_data: Raw request body from the frontend.

        Yields:
            UTF-8 encoded SSE event bytes.
        """
        request = AGUIRequest.from_dict(input_data)

        logger.info(
            "AG-UI request: thread=%s run=%s message=%r",
            request.thread_id,
            request.run_id,
            request.last_user_message[:80],
        )

        event = {
            "type": "message",
            "content": request.last_user_message,
            "session_id": request.thread_id,
            "messages": request.messages,
            "tools": request.tools,
            "context": request.context,
            "forwarded_props": request.forwarded_props,
        }

        async for ag_event in self._agent.run(event, session_id=request.thread_id):
            yield self._encode_event(ag_event)

        # Final done signal
        yield self._encode_raw_event("done", "[DONE]")

    async def dispatch_simple(
        self,
        message: str,
        *,
        session_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Simplified dispatch for non-AG-UI callers (e.g. webhooks).

        Args:
            message: Plain text message.
            session_id: Optional session identifier.

        Yields:
            UTF-8 encoded SSE event bytes.
        """
        event = {
            "type": "message",
            "content": message,
            "session_id": session_id or uuid.uuid4().hex,
        }

        async for ag_event in self._agent.run(event, session_id=session_id):
            yield self._encode_event(ag_event)

        yield self._encode_raw_event("done", "[DONE]")

    async def collect_response(
        self, input_data: dict[str, Any]
    ) -> list[AGUIEvent]:
        """Run and collect all events (useful for testing).

        Args:
            input_data: Raw request body.

        Returns:
            List of all :class:`AGUIEvent` objects produced.
        """
        request = AGUIRequest.from_dict(input_data)
        event = {
            "type": "message",
            "content": request.last_user_message,
            "session_id": request.thread_id,
        }
        events: list[AGUIEvent] = []
        async for ag_event in self._agent.run(event, session_id=request.thread_id):
            events.append(ag_event)
        return events

    # ------------------------------------------------------------------
    # SSE encoding helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_event(event: AGUIEvent) -> bytes:
        """Encode an AGUIEvent to SSE wire format bytes."""
        return event.to_sse().encode("utf-8")

    @staticmethod
    def _encode_raw_event(event_type: str, data: str) -> bytes:
        """Encode a raw SSE event."""
        return f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")
