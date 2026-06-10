"""Conversation pipeline data models.

Pydantic models for the guest conversation pipeline:
request/response contracts, internal state, and result types.
All models are serializable for Redis caching and API transport.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ── Enums ────────────────────────────────────────────────────── #


class SenderType(str, Enum):
    """Who sent the message."""

    GUEST = "guest"
    PROPERTY = "property"
    BOT = "bot"
    SYSTEM = "system"


class MessageTag(str, Enum):
    """Standardized message tags for categorization.

    Meta tags describe response quality; primary tags describe topic.
    """

    # Meta tags
    MISSING_INFO = "MISSING_INFO"
    PARTIAL_MISSING_INFO = "PARTIAL_MISSING_INFO"
    QUESTION_ANSWERED = "QUESTION_ANSWERED"
    CLARIFICATION_QUESTION = "CLARIFICATION_QUESTION"
    IS_EMERGENCY = "IS_EMERGENCY"

    # Primary tags
    AVAILABILITY_REQUEST = "AVAILABILITY_REQUEST"
    BOOKING_MODIFICATION_REQUEST = "BOOKING_MODIFICATION_REQUEST"
    EXTRA_SERVICE_REQUEST = "EXTRA_SERVICE_REQUEST"
    DISCOUNT_REQUEST = "DISCOUNT_REQUEST"
    INVOICE_REQUEST = "INVOICE_REQUEST"
    PAYMENT_METHOD_REQUEST = "PAYMENT_METHOD_REQUEST"
    POLICY_INFO_REQUEST = "POLICY_INFO_REQUEST"
    PROPERTY_INFO_REQUEST = "PROPERTY_INFO_REQUEST"
    UPSELL_REQUEST = "UPSELL_REQUEST"
    OPERATIONAL_TASK = "OPERATIONAL_TASK"
    COMPLAINT = "COMPLAINT"
    ESCALATED_COMPLAINT = "ESCALATED_COMPLAINT"


class UrgencyLevel(str, Enum):
    """Message urgency classification."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class SentimentCategory(str, Enum):
    """Sentiment classification."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


# ── Request Models ───────────────────────────────────────────── #


class ConversationMessage(BaseModel):
    """Single message in a conversation thread."""

    text: str
    sender_type: SenderType
    created_at: str = ""
    message_id: str = ""


class ReservationContext(BaseModel):
    """Reservation snapshot the caller already holds.

    Carried inside :class:`ConversationRequest` so the pipeline can
    inject grounded reservation facts into the system prompt **without**
    a round-trip to the PMS.  This is the source of truth when present;
    PMS lookup is a fallback for callers that only ship a
    ``reservation_id``.

    All fields are optional so the request stays valid for callers that
    legitimately have no reservation context (web widget pre-booking).

    Attributes:
        status: Booking status (confirmed, checked_in, etc.).
        check_in: ISO date / datetime, stay start.
        check_out: ISO date / datetime, stay end.
        check_in_time: Wall-clock check-in time (e.g. ``"15:00"``).
        check_out_time: Wall-clock check-out time.
        guest_name: Guest display name.
        num_guests: Adult count (children excluded).
        num_children: Children count.
        property_name: Property display name, when known.
        booking_channel: Channel name (manual / airbnb / booking).
        current_time: ISO timestamp at which the guest message was sent.
        total_price: Total reservation price as decimal string.
        currency: ISO 4217 currency code.
        payment_status: Whether the booking has been paid for.  Three
            states: ``"paid"`` / ``"unpaid"`` / ``""`` (unknown).
            String rather than ``bool`` so a missing value is
            distinguishable from an explicit ``False``.
    """

    status: str = ""
    check_in: str = ""
    check_out: str = ""
    check_in_time: str = ""
    check_out_time: str = ""
    guest_name: str = ""
    num_guests: int = 0
    num_children: int = 0
    property_name: str = ""
    booking_channel: str = ""
    current_time: str = ""
    total_price: str = ""
    currency: str = ""
    payment_status: str = ""

    def has_data(self) -> bool:
        """Return True when any non-default field is populated."""
        return bool(
            self.status
            or self.check_in
            or self.check_out
            or self.check_in_time
            or self.check_out_time
            or self.guest_name
            or self.num_guests
            or self.num_children
            or self.property_name
            or self.booking_channel
            or self.payment_status
        )


class CalendarDay(BaseModel):
    """Per-day availability projection sourced from ``unified_rateplans``.

    The model is intentionally a flat record so it can be rendered into
    the system prompt with no further joins.  ``status`` is normalised
    upstream so the LLM never has to reason about ``stopSell`` /
    ``countAvailableUnits`` on its own — when units are zero or
    ``stopSell`` is set, the day is reported as ``"blocked"``.

    Attributes:
        date: ISO date (``YYYY-MM-DD``).
        status: ``"available"`` | ``"blocked"`` | ``"unknown"``.
        available_units: Units the channel reports for the day.
        stop_sell: Channel-side hard stop (cleaning, owner block, …).
        price: Per-night price as a decimal string (empty when unset).
        currency: ISO 4217 currency code mirroring the rate plan.
        note: Free-text note from the channel (handover etc.).
    """

    date: str = ""
    status: str = "unknown"
    available_units: int = 0
    stop_sell: bool = False
    price: str = ""
    currency: str = ""
    note: str = ""


class ConversationRequest(BaseModel):
    """Input to POST /api/v1/conversations.

    Contains everything needed to process a guest message:
    the message itself, conversation history, and context IDs.
    """

    customer_id: str = Field(..., description="Tenant/customer identifier")
    org_id: str = Field(default="", description="Organization identifier")
    property_id: str = Field(default="", description="Property identifier")
    reservation_id: str = Field(default="", description="Booking/reservation ID")
    listing_id: str = Field(default="", description="Listing ID (may differ from property)")
    conversation_id: str = Field(default="", description="Conversation thread ID")
    message_id: str = Field(default="", description="Unique message ID")
    messages: list[ConversationMessage] = Field(
        default_factory=list,
        description="Conversation history, last message is the new one",
    )
    guest_name: str = ""
    guest_language: str = ""
    channel: str = Field(default="whatsapp", description="Channel: whatsapp, email, sms, web")
    reservation_context: ReservationContext | None = Field(
        default=None,
        description=(
            "Reservation snapshot from the caller. When present, the "
            "pipeline grounds the system prompt on this and skips the "
            "PMS round-trip."
        ),
    )
    availability_calendar: list[CalendarDay] = Field(
        default_factory=list,
        description=(
            "Per-day availability window resolved upstream from the "
            "unified GraphQL rate-plan calendar. Empty when no window "
            "could be fetched; the prompt then forces a deferral on "
            "any availability question."
        ),
    )

    @property
    def latest_message(self) -> str:
        """Extract the latest guest message text."""
        for msg in reversed(self.messages):
            if msg.sender_type == SenderType.GUEST:
                return msg.text
        return ""

    @property
    def history_for_llm(self) -> list[dict[str, str]]:
        """Convert messages to LLM chat format."""
        result: list[dict[str, str]] = []
        for msg in self.messages:
            role = "user" if msg.sender_type == SenderType.GUEST else "assistant"
            result.append({"role": role, "content": msg.text})
        return result


# ── Classification & Flags ───────────────────────────────────── #


class BusinessFlags(BaseModel):
    """All 16 business flags extracted from message classification."""

    is_emergency: bool = False
    is_property_related: bool = False
    is_availability_related: bool = False
    is_reservation_related: bool = False
    is_price_related: bool = False
    is_check_in_out_related: bool = False
    is_location_based: bool = False
    is_alternative_property_requested: bool = False
    is_invoice_request: bool = False
    is_discount_request: bool = False
    is_additional_services: bool = False
    is_thanks_only: bool = False
    is_complaint: bool = False
    is_cleaning_issue: bool = False
    is_maintenance_issue: bool = False
    is_noise_complaint: bool = False
    is_security_issue: bool = False
    is_navigation_query: bool = False
    # Stage-2 LLM hints (semantic shortcut for non-EN messages).
    # Empty when the upstream BusinessFlagClassifier did not emit
    # them (older code path, JSON parse failure, or LLM disabled);
    # DecisionClassifier then falls back to its keyword chain so
    # existing behaviour is preserved verbatim.
    scenario_hint: str = ""
    decision_type_hint: str = ""

    def active_flags(self) -> list[str]:
        """Return names of all flags that are True.

        Converts field names (is_emergency) to flag names (IS_EMERGENCY).
        """
        return [
            name.upper()
            for name, val in self.model_dump().items()
            if val is True
        ]

    def matches_any(self, flag_names: list[str]) -> bool:
        """Check if any of the given flag names are active."""
        active = set(self.active_flags())
        return bool(active & set(flag_names))


# ── RAG Source ───────────────────────────────────────────────── #


class RagSource(BaseModel):
    """A document retrieved by RAG search."""

    document_id: str = ""
    document_name: str = ""
    category: str = ""
    source: str = ""
    content_snippet: str = ""
    score: float = 0.0


# ── Task ─────────────────────────────────────────────────────── #


class TaskLevel(str, Enum):
    """Task priority level."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    URGENT = "Urgent"


class AutoTask(BaseModel):
    """Auto-created task when AI cannot fully resolve a request."""

    task_level: TaskLevel = TaskLevel.MEDIUM
    description: str = ""
    main_category: str = ""
    sub_category: str = ""
    tags: list[str] = Field(default_factory=list)
    existing_task_id: str | None = None


# ── Sentiment ────────────────────────────────────────────────── #


class SentimentResult(BaseModel):
    """Sentiment analysis result."""

    score: int = Field(default=5, ge=1, le=10, description="1=very negative, 10=very positive")
    category: SentimentCategory = SentimentCategory.NEUTRAL
    reasoning: str = ""


# ── Response Models ──────────────────────────────────────────── #


class ResponseFlags(BaseModel):
    """Flags describing response quality and routing."""

    was_helpful: float = Field(default=1.0, ge=0.0, le=1.0)
    is_need_attention: bool = False
    send_status: bool = True
    completeness: str = Field(default="full", description="full, partial, none")


class ConversationResponse(BaseModel):
    """Output of POST /api/v1/conversations.

    Contains the AI response, all metadata, and routing flags.
    """

    status: bool = True
    message: str = ""
    error: str | None = None

    # Classification
    business_flags: BusinessFlags = Field(default_factory=BusinessFlags)
    response_language: str = "en"
    confidence: float = 0.9

    # Response quality
    response_flags: ResponseFlags = Field(default_factory=ResponseFlags)
    message_tags: list[str] = Field(default_factory=list)

    # Context used
    rag_sources: list[RagSource] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)

    # Post-processing
    sentiment: SentimentResult = Field(default_factory=SentimentResult)
    tasks_created: list[AutoTask] = Field(default_factory=list)

    # Metadata
    process_time_ms: int = 0
    model_used: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )

    # Orchestrator (§10 priority chain) verdict surface.  Set by
    # :meth:`ConversationService._enforce_orchestrator_verdict` and
    # propagated here so downstream channel adapters can distinguish
    # "block" / "approval" turns from generic "needs attention" cases
    # — the latter is advisory while these two are authoritative.
    orchestrator_blocked: bool = False
    requires_pm_approval: bool = False


# ── Pipeline State ───────────────────────────────────────────── #


class PipelineState(BaseModel):
    """Internal state passed through the conversation pipeline.

    Accumulates data as each pipeline stage processes the message.
    Not exposed in the API — internal use only.
    """

    # Input
    request: ConversationRequest
    raw_message: str = ""
    cleaned_message: str = ""

    # Classification
    business_flags: BusinessFlags = Field(default_factory=BusinessFlags)
    response_language: str = "en"
    classification_confidence: float = 0.0

    # Guardrails
    active_guardrails: list[str] = Field(default_factory=list)
    active_operational_policies: list[str] = Field(default_factory=list)
    response_validation_failures: list[dict[str, str]] = Field(default_factory=list)
    system_prompt: str = ""
    tone_prompt: str = ""

    # Agent execution
    agent_response: str = ""
    rag_sources: list[RagSource] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)

    # Post-processing
    message_tags: list[str] = Field(default_factory=list)
    sentiment: SentimentResult = Field(default_factory=SentimentResult)
    tasks: list[AutoTask] = Field(default_factory=list)
    response_flags: ResponseFlags = Field(default_factory=ResponseFlags)

    # Property knowledge (injected from mockup or PMS)
    property_knowledge: str = ""

    # Customer context (cross-property history)
    customer_context: str = ""

    # Memory retrieval — populated by
    # ``ConversationService._load_memory_context`` when ``memory_system``
    # is injected and ``BRAIN_MEMORY_RETRIEVAL_ENABLED`` is truthy
    # (Task 4 of CLAUDE_CODE_WIRING_FIX_PLAN.md, see
    # docs/wiring_audit.md for the baseline).  Default empty so the
    # pre-Task-4 path keeps producing an empty ``[ESTABLISHED FACTS]``
    # section without raising.
    memory_facts: list[str] = Field(default_factory=list)
    conversation_summary: str = ""

    # PatternRule match (populated by PatternRuleRouter; None when no
    # learned rule fires for this turn).  Declared as ``Any`` so Pydantic
    # does not need to introspect PatternRule from the patterns layer —
    # keeps the conversation model self-contained.
    matched_rule: Any = None

    # ExecutionOrchestrator verdict (populated by
    # ``ConversationService._consult_orchestrator``; ``None`` when the
    # orchestrator dependency is absent — for example in unit tests
    # that exercise the LLM path in isolation).  Declared as ``Any``
    # so the conversation model stays decoupled from the orchestrator
    # package — runtime callers cast back to ``Decision`` when they
    # need the typed view.
    orchestrator_decision: Any = None

    # FL-16 Foundation Analysis Orchestrator output, populated by
    # ``ConversationService._run_foundation_analysis`` (Sprint 6 W1).
    # ``None`` when the orchestrator is not injected or
    # ``BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED`` is off — the default
    # configuration, in which the conversation pipeline behaves
    # bit-for-bit identically to pre-W1.  When populated, holds the
    # :class:`brain_engine.analysis.AnalysisResult` whose fields
    # (``foundation_match``, ``guardrail_block``, ``memory_routes``,
    # ``origin``) downstream stages can read without re-running the
    # match.  Declared as ``Any`` so the conversation model stays
    # decoupled from the analysis package, mirroring how
    # ``orchestrator_decision`` is wired.
    foundation_analysis: Any = None

    # Enforcement flags emitted by
    # ``ConversationService._enforce_orchestrator_verdict`` (Branch 4).
    # ``orchestrator_blocked`` short-circuits the LLM agent run when
    # the §10 chain returns ``mode == "block"`` — the runtime sends a
    # deterministic deny instead of letting the LLM improvise a refusal
    # that might leak unsafe context.  ``requires_pm_approval`` lets
    # the LLM draft a candidate reply but signals downstream gates
    # (PM panel, AG-UI adapter) that the message must not auto-send.
    orchestrator_blocked: bool = False
    requires_pm_approval: bool = False

    # Timing
    started_at: float = 0.0
