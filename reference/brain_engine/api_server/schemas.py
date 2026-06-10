"""Pydantic models for the AG-UI protocol server."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Role(StrEnum):
    """Message role in the AG-UI protocol."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Message(BaseModel):
    """A single message in the AG-UI conversation."""

    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class RunAgentInput(BaseModel):
    """Input payload for the AG-UI /run endpoint.

    Follows the AG-UI protocol specification for agent invocation.
    """

    messages: list[Message] = Field(
        default_factory=list,
        description="Conversation history as a list of messages.",
    )
    thread_id: str = Field(
        default="",
        description="Unique identifier for the conversation thread.",
    )
    run_id: str = Field(
        default="",
        description="Unique identifier for this specific run invocation.",
    )
    state: dict[str, Any] | None = Field(
        default=None,
        description="Optional shared state from the frontend.",
    )
    tools: list[dict[str, Any]] | None = Field(
        default=None,
        description="Frontend-defined tools available for the agent.",
    )
    context: list[dict[str, Any]] | None = Field(
        default=None,
        description="Additional context items (knowledge, documents).",
    )
    forwarded_props: dict[str, Any] | None = Field(
        default=None,
        description="Additional properties forwarded from the frontend.",
    )


class AgentState(BaseModel):
    """Shared state between agent and frontend, synced via STATE_DELTA events."""

    incident_id: str | None = None
    current_phase: str = "idle"
    slot_status: dict[str, Any] = Field(default_factory=dict)
    cleaner_assigned: str | None = None
    cleaner_phone: str | None = None
    photo_check_result: str | None = None
    guest_name: str | None = None
    guest_phone: str | None = None
    reservation_id: str | None = None
    nuki_lock_status: str | None = None
    damage_detected: bool = False
    damage_description: str | None = None
    resolution_status: str = "pending"
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)


class RunAgentOutput(BaseModel):
    """Output envelope returned after an agent run completes.

    Not sent directly over SSE -- the SSE stream emits individual events.
    This is used for non-streaming fallback or final summary.
    """

    thread_id: str
    run_id: str
    status: str = "completed"
    final_state: AgentState | None = None
    messages: list[Message] = Field(default_factory=list)


# ── Call API schemas ─────────────────────────────────────────────────────────


class MakeCallRequest(BaseModel):
    """Request to initiate an outbound phone call."""

    phone_number: str = Field(
        description="Recipient phone number in E.164 format (e.g. +905551234567).",
    )
    script: str | None = Field(
        default=None,
        description="System prompt override for this call (what the agent should do).",
    )
    first_message: str | None = Field(
        default=None,
        description="First message the agent says when call connects.",
    )


class MakeCallResponse(BaseModel):
    """Response after initiating an outbound call."""

    call_id: str
    status: str
    phone_number: str
    agent_id: str = ""


class CallStatusResponse(BaseModel):
    """Current status of a call."""

    call_id: str
    status: str
    duration_seconds: float | None = None
    ended_reason: str | None = None


class TranscriptResponse(BaseModel):
    """Transcript of a completed call."""

    call_id: str
    text: str
    turns: list[dict[str, Any]] = Field(default_factory=list)
