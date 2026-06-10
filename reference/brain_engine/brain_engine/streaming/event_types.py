"""AG-UI Event Types - Complete AG-UI protocol event type definitions.

Maps all Brain Engine events to AG-UI protocol event types for streaming
to connected UI clients via SSE or WebSocket.

The AG-UI protocol defines a standard set of event types for real-time
communication between AI agents and user interfaces.
"""

from enum import StrEnum


class EventType(StrEnum):
    """Complete AG-UI protocol event types.

    Covers all standard AG-UI events for agent lifecycle, text streaming,
    tool execution, state management, flow control, and custom extensions.
    """

    # ── Agent Run Lifecycle ─────────────────────────────────────────
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_ERROR = "run_error"

    # ── Text Message Streaming ──────────────────────────────────────
    TEXT_MESSAGE_START = "text_message_start"
    TEXT_MESSAGE_CONTENT = "text_message_content"
    TEXT_MESSAGE_END = "text_message_end"

    # ── Tool / Action Execution ─────────────────────────────────────
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_ARGS = "tool_call_args"
    TOOL_CALL_END = "tool_call_end"

    # ── State Management ────────────────────────────────────────────
    STATE_SNAPSHOT = "state_snapshot"
    STATE_DELTA = "state_delta"
    STATE_UPDATED = "state_updated"

    # ── Flow / State Machine ────────────────────────────────────────
    FLOW_STARTED = "flow_started"
    FLOW_STATE_CHANGED = "flow_state_changed"
    FLOW_COMPLETED = "flow_completed"
    FLOW_ERROR = "flow_error"

    # ── Slot Filling ────────────────────────────────────────────────
    SLOT_FILLED = "slot_filled"
    SLOT_REQUESTED = "slot_requested"
    SLOT_CLEARED = "slot_cleared"

    # ── Step / Progress ─────────────────────────────────────────────
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"

    # ── AI Reasoning ─────────────────────────────────────────────────
    REASONING_START = "reasoning_start"
    REASONING_STEP = "reasoning_step"
    REASONING_END = "reasoning_end"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    INTENT_CLASSIFIED = "intent_classified"
    MEMORY_RETRIEVED = "memory_retrieved"
    RAG_HIT = "rag_hit"
    GUARDRAIL_CHECK = "guardrail_check"
    COGNITIVE_MODE_CHANGED = "cognitive_mode_changed"

    # ── Sentiment / Guest Analysis ───────────────────────────────────
    SENTIMENT_UPDATE = "sentiment_update"
    GUEST_STATUS_UPDATE = "guest_status_update"

    # ── Call Events ──────────────────────────────────────────────────
    CALL_STARTED = "call_started"
    CALL_ENDED = "call_ended"
    CALL_TRANSCRIPT_UPDATE = "call_transcript_update"

    # ── Sandbox v2 Learning Pipeline ────────────────────────────────
    MISSING_INFO_DETECTED = "missing_info_detected"
    LEARNING_DECISION = "learning_decision"
    # ── Foundation Layer Q5-C — stage contradiction visibility ─────
    # Emitted to PM Chat when the FL-16 orchestrator's
    # ``_detect_stage_contradiction`` step finds a hard mismatch
    # between the booking stage implied by the event's calendar
    # and the booking stage the matched scenario expects (e.g.
    # calendar=post_checkout but scenario=pre_arrival — Mümin's
    # 2026-05-18 adversarial test).  Variant A: observation only;
    # Brain still produces a response.  The event lets the PM
    # operator (and Mümin's regression harness) see that Brain
    # detected the contradiction.
    STAGE_MISMATCH_DETECTED = "stage_mismatch_detected"
    # ── Phase 3 temporal analysis — PM-chat insight ────────────────
    # Emitted to PM Chat when the temporal analysis surface (Phase 3)
    # produces a grounded summary of one client's past + present
    # (fused as-of timeline → LLM analysis).  Flag-gated default-off
    # (BRAIN_TEMPORAL_PM_ENABLED); observation only — Brain still
    # answers the guest, this is the insight channel for the PM.
    TEMPORAL_ANALYSIS = "temporal_analysis"

    # ── Messages / Meta ─────────────────────────────────────────────
    RAW_MESSAGE = "raw_message"
    CUSTOM = "custom"

    @classmethod
    def lifecycle_events(cls) -> list["EventType"]:
        """Return all lifecycle event types."""
        return [cls.RUN_STARTED, cls.RUN_FINISHED, cls.RUN_ERROR]

    @classmethod
    def text_events(cls) -> list["EventType"]:
        """Return all text streaming event types."""
        return [
            cls.TEXT_MESSAGE_START,
            cls.TEXT_MESSAGE_CONTENT,
            cls.TEXT_MESSAGE_END,
        ]

    @classmethod
    def tool_events(cls) -> list["EventType"]:
        """Return all tool execution event types."""
        return [cls.TOOL_CALL_START, cls.TOOL_CALL_ARGS, cls.TOOL_CALL_END]

    @classmethod
    def state_events(cls) -> list["EventType"]:
        """Return all state management event types."""
        return [cls.STATE_SNAPSHOT, cls.STATE_DELTA, cls.STATE_UPDATED]

    @classmethod
    def flow_events(cls) -> list["EventType"]:
        """Return all flow control event types."""
        return [
            cls.FLOW_STARTED,
            cls.FLOW_STATE_CHANGED,
            cls.FLOW_COMPLETED,
            cls.FLOW_ERROR,
        ]

    @classmethod
    def slot_events(cls) -> list["EventType"]:
        """Return all slot event types."""
        return [cls.SLOT_FILLED, cls.SLOT_REQUESTED, cls.SLOT_CLEARED]
