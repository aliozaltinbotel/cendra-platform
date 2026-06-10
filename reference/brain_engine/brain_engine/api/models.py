"""API Models — Pydantic request/response schemas for Brain Engine API.

All endpoints use these models for validation and serialization.
Fully compatible with Cendra AI platform expectations:
    - Guest Agent returns: reply_text, is_need_attention, send_status,
      tasks[], message_tags[], rag_sources[], sentiment_score, confidence
    - Ops Agent returns: actions as MCP tool calls, session state,
      contact cascade decisions, cost/approval info
    - All responses include reasoning_trace for audit

Based on Blueprint v5 + Cendra ops case study.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════


class RuleCategory(str, Enum):
    """Allowed rule categories for procedural rules."""

    GUEST_COMMUNICATION = "guest_communication"
    ESCALATION = "escalation"
    OPERATIONS = "operations"
    TIMING = "timing"
    PRICING = "pricing"
    AUTOMATION = "automation"
    VENDOR = "vendor"
    SAFETY = "safety"
    UPSELL = "upsell"
    CLEANING = "cleaning"


class RuleSource(str, Enum):
    """Rule origin: who created it and how it can change."""

    MANUAL = "manual"
    LEARNED = "learned"
    IMMUTABLE = "immutable"


class RulePriority(str, Enum):
    """Rule priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ═══════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════════


class AgentConfig(BaseModel):
    """Agent configuration from Cendra's Agent Hub.

    Mirrors Cendra's CustomerAISettingsModel.

    Attributes:
        custom_instructions: SOP text for the agent.
        tone_type: Communication style.
        guardrails: List of behavioral constraints.
        tags: Situation categories the agent recognizes.
        escalations: Tag -> escalation behavior mapping.
        enabled_tools: List of allowed MCP tool names.
        signature: Footer appended to outbound messages.
        working_hours: When the agent is active.
        autonomy_mode: 'autopilot' or 'semiauto'.
    """

    custom_instructions: str = ""
    tone_type: str = "professional"
    guardrails: list[GuardrailRule] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    escalations: list[EscalationRule] = Field(default_factory=list)
    enabled_tools: list[str] = Field(default_factory=list)
    signature: str = ""
    working_hours: dict[str, object] | None = None
    autonomy_mode: str = "autopilot"
    tool_preferences: dict[str, bool] | None = None


class GuardrailRule(BaseModel):
    """A single guardrail rule from Cendra's config.

    Attributes:
        rule_text: The constraint text.
        priority: 'always', 'contextual', or 'label_based'.
        trigger_flags: Business flags that activate this rule.
        trigger_labels: Guest labels that activate this rule.
    """

    rule_text: str
    priority: str = "always"
    trigger_flags: list[str] = Field(default_factory=list)
    trigger_labels: list[str] = Field(default_factory=list)


class EscalationRule(BaseModel):
    """Escalation rule mapping a tag to behavior.

    Attributes:
        tag: The trigger tag name.
        behavior: 'escalate', 'answer_and_escalate', or 'auto_handle'.
    """

    tag: str
    behavior: str = "escalate"


class GuestProfile(BaseModel):
    """Guest profile data from Cendra PMS.

    Attributes:
        guest_id: Guest identifier.
        name: Guest display name (first name only for privacy).
        language: Preferred language code.
        labels: Persona labels (Family, VIP, Pet Owner, etc.).
        booking_count: Number of previous bookings.
        sentiment_history: Recent sentiment scores.
        is_vip: Whether guest is flagged as VIP.
    """

    guest_id: str = ""
    name: str = ""
    language: str = "en"
    labels: list[str] = Field(default_factory=list)
    booking_count: int = 0
    sentiment_history: list[int] = Field(default_factory=list)
    is_vip: bool = False


class PropertyLocation(BaseModel):
    """Property location data for navigation responses.

    Attributes:
        address: Full street address.
        city: City name.
        country: Country name or code.
        latitude: GPS latitude.
        longitude: GPS longitude.
        timezone: Property timezone (e.g., Europe/Bratislava).
        entrance_instructions: How to find the entrance.
    """

    address: str = ""
    city: str = ""
    country: str = ""
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = ""
    entrance_instructions: str = ""

    def google_maps_url(self) -> str:
        """Generate Google Maps URL from GPS coordinates.

        Returns:
            Google Maps URL or empty string if no coordinates.
        """
        if self.latitude is not None and self.longitude is not None:
            return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"
        if self.address:
            from urllib.parse import quote
            return f"https://www.google.com/maps/search/{quote(self.address)}"
        return ""


class PropertyContext(BaseModel):
    """Property context from Cendra for enriched AI processing.

    Matches Cendra integration contract section 16.1.

    Attributes:
        name: Property name.
        address: Full address.
        check_in_time: Standard check-in time.
        check_out_time: Standard checkout time.
        house_rules: Property rules text.
        amenities: List of amenities.
    """

    name: str = ""
    address: str = ""
    check_in_time: str = ""
    check_out_time: str = ""
    house_rules: str = ""
    amenities: list[str] = Field(default_factory=list)


class ReservationContext(BaseModel):
    """Reservation context from Cendra for enriched AI processing.

    Matches Cendra integration contract section 16.1.

    Attributes:
        status: Booking status (confirmed, checked_in, checked_out, etc.).
        check_in: Check-in date (ISO 8601).
        check_out: Check-out date (ISO 8601).
        guest_name: Guest display name.
        num_guests: Number of guests.
        current_time: ISO-8601 wall clock at which the guest message was
            sent.  Populated by the UI on every brain run; used by
            :class:`~brain_engine.patterns.classifier.DecisionClassifier`
            to short-circuit keyword stage detection with date math
            ("question asked May 14 vs stay Apr 26-28" ⇒ POST_CHECKOUT
            regardless of message wording).  Optional for backwards
            compatibility — empty string falls back to keyword logic.
    """

    status: str = ""
    check_in: str = ""
    check_out: str = ""
    guest_name: str = ""
    num_guests: int = 1
    current_time: str = ""


class GuestMessageRequest(BaseModel):
    """Request payload for POST /api/v1/guest/message.

    Replaces Cendra's Guest Agent LLM call pipeline.

    Attributes:
        message: Guest message text.
        conversation_history: Previous messages (OpenAI format).
        property_id: Property identifier.
        reservation_id: Reservation identifier.
        guest_profile: Guest profile data.
        agent_config: Agent configuration from Cendra Agent Hub.
        knowledge_base_context: Pre-fetched RAG results to inject.
        property_summary: Property description and rules.
        booking_status: Current booking status (confirmed/checked_in/etc.).
        channel: Message channel (airbnb/booking/whatsapp/email).
    """

    model_config = {"json_schema_extra": {"examples": [{
        "message": "The apartment is very dirty, there are stains on the sofa",
        "property_id": "prop_zaži_bratislava",
        "reservation_id": "res_2026_04_01",
        "conversation_history": [
            {"role": "guest", "content": "Hello, we just arrived"},
            {"role": "assistant", "content": "Welcome! How can I help?"},
        ],
        "guest_profile": {
            "guest_id": "g_fernanda", "name": "Fernanda Silva",
            "email": "fernanda@email.com", "phone": "+5511999999999",
        },
        "channel": "airbnb",
    }]}}

    message: str
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    property_id: str
    reservation_id: str | None = None
    guest_profile: GuestProfile | None = None
    agent_config: AgentConfig | None = None
    knowledge_base_context: dict[str, object] | None = None
    property_summary: str = ""
    property_location: PropertyLocation | None = None
    booking_status: str = ""
    channel: str = "airbnb"

    # Cendra integration fields (section 16.1)
    customer_id: str = ""
    workspace_id: str = ""
    message_id: str = ""
    guest_messages_since_last_reply: list[str] = Field(default_factory=list)
    correlation_id: str = ""
    property_context: PropertyContext | None = None
    reservation_context: ReservationContext | None = None


class ContactInfo(BaseModel):
    """Contact information for ops cascade.

    Attributes:
        contact_id: Contact identifier in Cendra.
        name: Contact display name.
        phone: Phone number.
        role: 'cleaner' or 'vendor'.
        vendor_category: Vendor category (plumbing, electrical, etc.).
        priority: Priority order in cascade (1 = first).
    """

    contact_id: str
    name: str = ""
    phone: str = ""
    role: str = "cleaner"
    vendor_category: str = ""
    priority: int = 1


class OpsEventRequest(BaseModel):
    """Request payload for POST /api/v1/ops/event.

    Replaces Cendra's 963-line OpsSessionWorkflow.

    Attributes:
        event_type: Type of operational event.
        property_id: Property identifier.
        reservation_id: Reservation identifier.
        description: Human-readable issue description.
        category: Issue category (Cleaning, Plumbing, Electrical, etc.).
        subcategory: Issue subcategory.
        event_data: Event-specific data payload.
        priority: Event priority (low/normal/high/critical).
        contacts: Ordered list of contacts to try.
        cost_threshold: Max cost before PM approval needed.
        agent_config: Agent configuration overrides.
        source_task_id: ID of the task that triggered this event.
        tags: Classification tags from guest agent.
    """

    model_config = {"json_schema_extra": {"examples": [{
        "event_type": "cleaning_needed",
        "property_id": "prop_zaži_bratislava",
        "reservation_id": "res_2026_04_01",
        "description": "Guest reports apartment is dirty",
        "category": "Cleaning and Hygiene",
        "subcategory": "Cleanliness Complaints",
        "priority": "high",
        "contacts": [
            {"contact_id": "ct_ayse", "name": "Ayşe", "phone": "+905551234567", "role": "cleaner", "priority": 1},
            {"contact_id": "ct_mehmet", "name": "Mehmet", "phone": "+905552222222", "role": "cleaner", "priority": 2},
        ],
        "cost_threshold": 200.0,
        "tags": ["IS_CLEANING_ISSUE", "IS_COMPLAINT"],
    }]}}

    event_type: str
    property_id: str
    reservation_id: str | None = None
    description: str = ""
    category: str = ""
    subcategory: str = ""
    event_data: dict[str, object] = Field(default_factory=dict)
    priority: str = "normal"
    contacts: list[ContactInfo] = Field(default_factory=list)
    cost_threshold: float = 200.0
    agent_config: AgentConfig | None = None
    source_task_id: str = ""
    tags: list[str] = Field(default_factory=list)


class ContactReplyRequest(BaseModel):
    """Request payload for POST /api/v1/ops/contact-reply.

    Handles multi-turn conversations with cleaners/vendors.

    Attributes:
        session_id: Ops session identifier (from OpsSessionManager).
        contact_id: Contact who replied.
        contact_type: Type of contact (cleaner/vendor).
        message: Reply message text.
        property_id: Property identifier.
        reply_classification: Pre-classified reply type (optional).
        cost_amount: Cost mentioned in reply (if detected).
        cost_currency: Currency of the cost.
        alternative_contact: Alternative contact suggested (Scenario 2).
        eta_minutes: ETA if mentioned.
        is_voice_note: Whether the reply was a voice note.
        is_image: Whether the reply included an image.
        media_url: URL to media if applicable.
    """

    model_config = {"json_schema_extra": {"examples": [
        {
            "session_id": "ops-abc123",
            "contact_id": "ct_ayse",
            "message": "Yes, I can do it at 3pm",
            "reply_classification": "confirmed",
            "eta_minutes": 60,
        },
        {
            "session_id": "ops-abc123",
            "contact_id": "ct_mehmet",
            "message": "It will cost 150 EUR",
            "reply_classification": "cost_quoted",
            "cost_amount": 150.0,
            "cost_currency": "EUR",
        },
        {
            "session_id": "ops-abc123",
            "contact_id": "ct_ayse",
            "message": "Sorry, I can't but try my colleague Ali",
            "reply_classification": "declined",
            "alternative_contact": {"name": "Ali", "phone": "+905553333333"},
        },
    ]}}

    session_id: str
    contact_id: str
    contact_type: str = "cleaner"  # cleaner, vendor, handyman, emergency, guest, owner, sales_team, support, insurance, legal, custom
    message: str = ""
    property_id: str = ""
    reply_classification: str = ""
    cost_amount: float | None = None
    cost_currency: str = ""
    alternative_contact: dict[str, str] | None = None
    eta_minutes: int | None = None
    is_voice_note: bool = False
    is_image: bool = False
    media_url: str = ""
    photos: list[str] = Field(default_factory=list)
    process_id: str = ""


class ApprovalDecisionRequest(BaseModel):
    """Request payload for POST /api/v1/approval/decision.

    Learns from owner/PM decisions for adaptive autonomy.

    Attributes:
        request_id: Approval request identifier.
        session_id: Ops session identifier (if ops-related).
        approved: Whether the action was approved.
        owner_id: Owner/PM who made the decision.
        property_id: Property context.
        reason: Reason for the decision.
        modifications: Modifications to the proposed action.
        decision_type: Type (cost_approval/action_approval/message_approval/modified).
        pm_correction: Текст исправленного PM ответа. Заполняется, когда
            ``decision_type == "modified"`` — PM принял решение ответить иначе.
        guest_message: Исходное сообщение гостя, на которое был сгенерирован
            ответ AI. Нужно для создания KnowledgeCandidate.
        original_ai_response: Оригинальный ответ AI, который PM решил заменить.
    """

    request_id: str
    session_id: str = ""
    approved: bool
    owner_id: str
    property_id: str = ""
    reason: str = ""
    modifications: dict[str, object] | None = None
    decision_type: str = "action_approval"
    # PM Correction Hook — поля для фиксации исправления ответа AI.
    pm_correction: str | None = None
    guest_message: str | None = None
    original_ai_response: str | None = None


class NewBookingRequest(BaseModel):
    """Request payload for POST /api/v1/booking/new.

    Full booking lifecycle: risk assessment, scheduling, welcome.

    Attributes:
        reservation_id: Reservation identifier.
        property_id: Property identifier.
        guest_profile: Guest profile data.
        checkin_date: Check-in date (ISO format).
        checkout_date: Check-out date (ISO format).
        num_guests: Number of guests.
        booking_source: Source platform (Airbnb/Booking.com/direct).
        special_requests: Guest special requests.
        booking_data: Additional booking metadata.
    """

    reservation_id: str
    property_id: str
    guest_profile: GuestProfile
    checkin_date: str
    checkout_date: str
    num_guests: int = 1
    booking_source: str = "airbnb"
    special_requests: str = ""
    booking_data: dict[str, object] = Field(default_factory=dict)


class UpsellAnalysisRequest(BaseModel):
    """Request payload for POST /api/v1/upsell/analyze.

    Attributes:
        reservation_id: Booking identifier.
        property_id: Property identifier.
        checkin_date: Check-in date (ISO).
        checkout_date: Check-out date (ISO).
        checkin_time: Standard check-in time.
        checkout_time: Standard checkout time.
        nightly_rate: Per-night price.
        guest_score: Guest loyalty score (0-100).
        num_guests: Number of guests.
        next_booking_checkin: Next booking check-in (ISO, optional).
        prev_booking_checkout: Previous booking checkout (ISO, optional).
    """

    reservation_id: str
    property_id: str
    checkin_date: str
    checkout_date: str
    checkin_time: str = "14:00"
    checkout_time: str = "10:00"
    nightly_rate: float = 100.0
    guest_score: int = 50
    num_guests: int = 1
    next_booking_checkin: str | None = None
    prev_booking_checkout: str | None = None


class UpsellAnalysisResponse(BaseModel):
    """Response for POST /api/v1/upsell/analyze.

    Attributes:
        reservation_id: Booking identifier.
        property_id: Property identifier.
        guest_score: Guest loyalty score.
        offers: List of upsell offers.
        total_revenue_potential: Sum of all offer revenues.
        message_to_guest: Pre-built offer message.
        actions: MCP tool calls for auto-applicable offers.
    """

    reservation_id: str = ""
    property_id: str = ""
    guest_score: int = 0
    offers: list[dict[str, object]] = Field(default_factory=list)
    total_revenue_potential: float = 0.0
    message_to_guest: str = ""
    actions: list[MCPAction] = Field(default_factory=list)


class KnowledgeSyncRequest(BaseModel):
    """Request payload for POST /api/v1/knowledge/sync.

    Syncs Cendra Knowledge Base entries into Brain Engine SemanticMemory.

    Attributes:
        property_id: Property to sync (empty = all properties).
        entries: KB entries to import into SemanticMemory.
        candidates: Knowledge candidates for auto-approval.
        conflicts: KB conflicts that need resolution.
        direction: Sync direction ('import' = Cendra→Brain, 'export' = Brain→Cendra, 'bidirectional').
    """

    property_id: str = ""
    entries: list[KnowledgeEntry] = Field(default_factory=list)
    candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    conflicts: list[KnowledgeConflict] = Field(default_factory=list)
    direction: str = "import"


class KnowledgeEntry(BaseModel):
    """A knowledge base entry from Cendra.

    Attributes:
        entry_id: Entry identifier in Cendra KB.
        title: Entry title (question or topic).
        content: Entry content (answer or information).
        property_id: Property scope.
        category: Knowledge category.
        is_active: Whether entry is active.
        last_updated: ISO timestamp of last update.
    """

    entry_id: str
    title: str = ""
    content: str = ""
    property_id: str = ""
    category: str = ""
    is_active: bool = True
    last_updated: str = ""


class KnowledgeCandidate(BaseModel):
    """A knowledge candidate auto-extracted from conversations.

    Attributes:
        candidate_id: Candidate identifier.
        question: Extracted question.
        answer: Extracted answer.
        confidence: AI confidence (0.0-1.0).
        source: Source of the candidate (conversation, review, etc.).
        property_id: Property scope.
        red_flags: Строка с флагами риска. В Cendra ``RedFlags`` — это STRING
            (не list). Может быть JSON-массивом (``'["access_code"]'``),
            CSV (``'access_code,price_or_fee'``) или пустой строкой.
            MIRA парсит это через ``parse_red_flags()``.
    """

    candidate_id: str
    question: str = ""
    answer: str = ""
    confidence: float = 0.0
    source: str = ""
    property_id: str = ""
    red_flags: str = ""


class KnowledgeConflict(BaseModel):
    """A conflict between knowledge entries that needs resolution.

    Attributes:
        conflict_id: Conflict identifier.
        existing_entry_id: ID of existing KB entry.
        new_content: Conflicting new content.
        resolution: How to resolve ('keep_existing', 'use_new', 'merge').
    """

    conflict_id: str
    existing_entry_id: str = ""
    new_content: str = ""
    resolution: str = ""


class KnowledgeSyncResponse(BaseModel):
    """Response for POST /api/v1/knowledge/sync.

    Attributes:
        imported_count: Entries imported into SemanticMemory.
        exported_count: Entries exported back to Cendra KB.
        candidates_approved: Candidates auto-approved.
        candidates_rejected: Candidates rejected.
        conflicts_resolved: Conflicts resolved.
        actions: MCP tool calls for Cendra to execute.
        errors: Any sync errors.
    """

    imported_count: int = 0
    exported_count: int = 0
    candidates_approved: int = 0
    candidates_rejected: int = 0
    conflicts_resolved: int = 0
    actions: list[MCPAction] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ConsolidationRequest(BaseModel):
    """Request payload for POST /api/v1/memory/consolidate.

    Attributes:
        cycle_type: 'nightly' or 'monthly'.
    """

    cycle_type: str = "nightly"


# ═══════════════════════════════════════════════════════════════════════
# RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════


class TaskItem(BaseModel):
    """A task created by Brain Engine for Cendra to execute.

    Matches Cendra's task format from Guest Agent.

    Attributes:
        task: Short task title.
        description: Detailed task description.
        main_category: Main category (Cleaning and Hygiene, etc.).
        sub_category: Sub-category.
        tags: Classification tags.
        priority: Task priority.
    """

    task: str
    description: str = ""
    main_category: str = ""
    sub_category: str = ""
    tags: list[str] = Field(default_factory=list)
    priority: str = "normal"


class MCPAction(BaseModel):
    """An MCP tool call for Cendra to execute.

    Attributes:
        tool: MCP tool name (sendWhatsApp, createContact, etc.).
        params: Tool parameters.
        priority: Execution priority (1=first).
        depends_on: Call IDs this depends on.
    """

    tool: str
    params: dict[str, object] = Field(default_factory=dict)
    priority: int = 1
    depends_on: list[str] = Field(default_factory=list)


class ClassificationFlags(BaseModel):
    """Business flags from classification.

    Attributes:
        is_emergency: Life-threatening or dangerous situation.
        is_property_related: Amenities, wifi, rules, appliances.
        is_availability_related: Dates, pricing, calendar.
        is_reservation_related: Existing booking modifications.
        is_complaint: Negative experience, dissatisfaction.
        is_check_in_out_related: Check-in/out timing, instructions.
        is_navigation_query: Guest can't find property, needs directions.
        is_discount_request: Price negotiation.
        is_invoice_request: Receipt, billing, documentation.
        is_cleaning_issue: Dirty, stains, smell.
        is_maintenance_issue: Broken, leaking, not working.
        is_noise_complaint: Loud neighbors, construction.
        is_security_issue: Lock broken, suspicious person.
    """

    is_emergency: bool = False
    is_property_related: bool = False
    is_availability_related: bool = False
    is_reservation_related: bool = False
    is_complaint: bool = False
    is_check_in_out_related: bool = False
    is_navigation_query: bool = False
    is_discount_request: bool = False
    is_invoice_request: bool = False
    is_cleaning_issue: bool = False
    is_maintenance_issue: bool = False
    is_noise_complaint: bool = False
    is_security_issue: bool = False


class EscalationInfo(BaseModel):
    """Escalation details when Brain Engine cannot handle autonomously.

    Attributes:
        reason: Why escalation is needed.
        escalation_type: Type (cost/safety/no_contacts/policy/unknown).
        severity: low/medium/high/critical.
        context: Additional context for PM.
        suggested_action: What Brain Engine recommends PM does.
    """

    reason: str
    escalation_type: str = "unknown"
    severity: str = "medium"
    context: dict[str, object] = Field(default_factory=dict)
    suggested_action: str = ""


class OpsSessionState(BaseModel):
    """Current state of an ops session, returned in responses.

    Attributes:
        session_id: Ops session identifier.
        status: Current session status.
        current_contact: Contact currently being tried.
        contacts_tried: Number of contacts tried so far.
        contacts_remaining: Number of contacts left in cascade.
        cost_quoted: Last cost quote received.
        pending_approval: Whether PM approval is pending.
        is_recurring: Whether this is a repeat of a previous issue.
    """

    session_id: str = ""
    status: str = ""
    current_contact: str = ""
    contacts_tried: int = 0
    contacts_remaining: int = 0
    cost_quoted: float | None = None
    pending_approval: bool = False
    is_recurring: bool = False


class FollowUp(BaseModel):
    """A scheduled follow-up check for proactive behavior.

    Brain Engine returns follow-ups in responses. Scheduler picks
    them up and triggers Brain Engine when conditions are met.

    Attributes:
        id: Unique follow-up identifier.
        check_after_minutes: Minutes to wait before checking.
        condition: Condition type to evaluate.
        condition_params: Parameters for the condition check.
        description: Human-readable description of what to check.
        process_id: Related active process ID.
        property_id: Related property ID.
    """

    id: str = ""
    check_after_minutes: int = 30
    condition: str = "no_response_from"
    condition_params: dict[str, object] = Field(default_factory=dict)
    description: str = ""
    process_id: str = ""
    property_id: str = ""


class ProcessParticipant(BaseModel):
    """A participant in an active process.

    Attributes:
        contact_id: Participant identifier.
        role: Participant role.
        status: Current status (waiting, contacted, accepted, etc.).
        last_message: Last message sent/received.
        last_message_at: Timestamp of last message.
    """

    contact_id: str
    role: str = "cleaner"
    status: str = "waiting"
    last_message: str = ""
    last_message_at: str = ""


class ActiveProcessResponse(BaseModel):
    """Active process state in API responses.

    Attributes:
        process_id: Process identifier.
        type: Process type (cleaning, maintenance, sales, etc.).
        property_id: Related property.
        status: Current status.
        started_at: Start timestamp.
        deadline: Deadline timestamp.
        reason: What triggered this process.
        participants: Process participants.
        pending_follow_ups: IDs of pending follow-ups.
        history: Process event history.
        context: Additional context data.
    """

    process_id: str = ""
    type: str = ""
    property_id: str = ""
    status: str = "active"
    started_at: str = ""
    deadline: str = ""
    reason: str = ""
    participants: list[ProcessParticipant] = Field(default_factory=list)
    pending_follow_ups: list[str] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    context: dict[str, object] = Field(default_factory=dict)


class ProcessReplyRequest(BaseModel):
    """Request payload for POST /api/v1/process/reply.

    Handles replies from any participant role in a process.

    Attributes:
        process_id: Active process identifier.
        contact_id: Who replied.
        contact_type: Role of the replier.
        message: Reply message text.
        property_id: Property identifier.
        photos: Photo URLs attached to reply.
        cost_amount: Cost mentioned.
        cost_currency: Currency of cost.
        eta_minutes: ETA if mentioned.
    """

    process_id: str
    contact_id: str
    contact_type: str = "cleaner"
    message: str = ""
    property_id: str = ""
    photos: list[str] = Field(default_factory=list)
    cost_amount: float | None = None
    cost_currency: str = ""
    eta_minutes: int | None = None


class ActiveProcessListResponse(BaseModel):
    """Response for GET /api/v1/processes.

    Attributes:
        processes: List of active processes.
        total: Total count.
    """

    processes: list[ActiveProcessResponse]
    total: int


class RAGSourceDetail(BaseModel):
    """A single RAG source reference for Cendra transparency.

    Attributes:
        chunk_id: Azure Search chunk identifier.
        source: Source document name or URL.
        source_type: Type (document, conversation, pms, faq).
    """

    chunk_id: str = ""
    source: str = ""
    source_type: str = ""


class RAGSources(BaseModel):
    """RAG sources container matching Cendra contract 16.2.

    Attributes:
        sources: Individual source references.
        source_analysis: AI analysis of which sources were most useful.
    """

    sources: list[RAGSourceDetail] = Field(default_factory=list)
    source_analysis: str = ""


class ResponseQuality(BaseModel):
    """AI self-assessment of response quality for Cendra.

    Attributes:
        was_helpful: 1 if helpful, 0 if not.
        completeness: 'full', 'partial', or 'incomplete'.
    """

    was_helpful: int = 1
    completeness: str = "full"


class BrainEngineResponse(BaseModel):
    """Unified response from Brain Engine.

    Compatible with Cendra's expected response format from their
    existing Guest Agent + enhanced with Brain Engine features.

    Attributes:
        reply_text: Generated text response (for guest/contact).
        confidence: Decision confidence (0.0-1.0).
        cognitive_level: L1/L2/L3/L4 cognitive depth used.

        is_need_attention: Whether PM should review (Cendra flag).
        send_status: Whether safe to auto-send (Cendra flag).

        classification: Business flag classification results.
        sentiment_score: Guest sentiment 1-5 (Cendra format).
        response_language: Detected response language.

        message_tags: Classification tags (Cendra format).
        tasks: Tasks for Cendra to create in PMS.
        actions: MCP tool calls for Cendra to execute.
        rag_sources: Knowledge base sources used.

        requires_approval: Whether owner/PM approval is needed.
        approval_request: Approval request details.
        escalation: Escalation details (if escalating to PM).

        ops_session: Current ops session state (for ops endpoints).
        memory_updates: Memory changes made during processing.
        reasoning_trace: Full audit trail of reasoning process.
        model_used: LLM model that produced the response.
        latency_ms: Total processing time in milliseconds.
    """

    # Core response
    reply_text: str = ""
    confidence: float = 0.5
    cognitive_level: str = "L1"

    # Cendra compatibility flags
    is_need_attention: bool = False
    send_status: bool = True

    # Classification
    classification: ClassificationFlags | None = None
    sentiment_score: int = 3
    response_language: str = "en"

    # Tags and tasks (Cendra format)
    message_tags: list[str] = Field(default_factory=list)
    tasks: list[TaskItem] = Field(default_factory=list)
    actions: list[MCPAction] = Field(default_factory=list)
    rag_sources: RAGSources | None = None

    # Cendra contract 16.2 fields
    answered_message_ids: list[str] = Field(default_factory=list)
    customer_tags: list[str] = Field(default_factory=list)
    response_quality: ResponseQuality | None = None

    # Approval / escalation
    requires_approval: bool = False
    approval_request: dict[str, object] | None = None
    escalation: EscalationInfo | None = None

    # Ops session state
    ops_session: OpsSessionState | None = None

    # Active process
    active_process: ActiveProcessResponse | None = None

    # Proactive behavior — follow-ups for Scheduler
    follow_ups: list[FollowUp] = Field(default_factory=list)

    # Audit
    memory_updates: list[dict[str, object]] = Field(default_factory=list)
    reasoning_trace: str = ""
    model_used: str = ""
    latency_ms: int = 0


class HealthResponse(BaseModel):
    """Response for GET /api/v1/health.

    Attributes:
        status: Service health status.
        version: Brain Engine version.
        memory_stats: Memory system statistics.
        skills_count: Number of active procedural skills.
        accuracy_7d: 7-day rolling accuracy score.
        uptime_hours: Service uptime in hours.
        active_ops_sessions: Number of active ops sessions.
        llm_costs_24h: LLM costs in last 24 hours.
    """

    status: str = "healthy"
    version: str = "1.0.0"
    memory_stats: dict[str, object] = Field(default_factory=dict)
    skills_count: int = 0
    accuracy_7d: float = 0.0
    uptime_hours: float = 0.0
    active_ops_sessions: int = 0
    llm_costs_24h: float = 0.0


class MetricsResponse(BaseModel):
    """Response for GET /api/v1/metrics.

    Attributes:
        period_days: Metrics period in days.
        total_interactions: Total interactions processed.
        avg_grader_score: Average quality score.
        owner_intervention_rate: Rate of owner overrides.
        self_resolution_rate: Autonomous resolution rate.
        skills_evolved: Skills evolved this period.
        skills_total: Total active skills.
        autonomy_stats: Per-owner autonomy level stats.
        top_event_types: Most common event types.
        avg_latency_ms: Average response latency.
        llm_cost_total: Total LLM costs for the period.
        cognitive_level_distribution: How often each level is used.
    """

    period_days: int = 30
    total_interactions: int = 0
    avg_grader_score: float = 0.0
    owner_intervention_rate: float = 0.0
    self_resolution_rate: float = 0.0
    skills_evolved: int = 0
    skills_total: int = 0
    autonomy_stats: dict[str, object] = Field(default_factory=dict)
    top_event_types: dict[str, int] = Field(default_factory=dict)
    avg_latency_ms: int = 0
    llm_cost_total: float = 0.0
    cognitive_level_distribution: dict[str, int] = Field(default_factory=dict)


class PropertyIntelligenceResponse(BaseModel):
    """Response for GET /api/v1/property/{id}/intelligence.

    Returns everything Brain Engine has learned about a property.

    Attributes:
        property_id: Property identifier.
        knowledge_facts: Known facts (from KnowledgeGraph).
        learned_rules: Learned operational rules (from ProceduralMemory).
        city_maturity: City maturity level (NEW/LEARNING/MATURE).
        common_issues: Frequently reported issues with counts.
        cleaner_scores: Cleaner scoring data.
        vendor_scores: Vendor scoring data.
        guest_patterns: Observed guest behavior patterns.
        autonomy_level: Current autonomy level for this property.
        total_interactions: Total interactions for this property.
        resolution_rate: Self-resolution rate.
    """

    property_id: str
    knowledge_facts: list[dict[str, object]] = Field(default_factory=list)
    learned_rules: list[dict[str, object]] = Field(default_factory=list)
    city_maturity: str = "NEW"
    common_issues: list[dict[str, object]] = Field(default_factory=list)
    cleaner_scores: list[dict[str, object]] = Field(default_factory=list)
    vendor_scores: list[dict[str, object]] = Field(default_factory=list)
    guest_patterns: list[str] = Field(default_factory=list)
    autonomy_level: str = "L1_SUGGEST"
    total_interactions: int = 0
    resolution_rate: float = 0.0


# ═══════════════════════════════════════════════════════════════════════
# RULES CRUD MODELS
# ═══════════════════════════════════════════════════════════════════════


class RuleCreateRequest(BaseModel):
    """Request payload for POST /api/v1/rules.

    Attributes:
        property_id: Property this rule belongs to.
        category: Rule category from RuleCategory enum.
        rule: Human-readable rule text.
        confidence: Initial confidence (0.0-1.0).
        source: Rule source (manual, learned, immutable).
        immutable: Whether rule cannot be changed via API.
        priority: Priority level (low, medium, high, critical).
        tags: Classification tags.
        created_by: Who is creating this rule.
    """

    property_id: str
    category: RuleCategory
    rule: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: RuleSource = RuleSource.MANUAL
    immutable: bool = False
    priority: RulePriority = RulePriority.MEDIUM
    tags: list[str] = Field(default_factory=list)
    created_by: str = ""


class RuleUpdateRequest(BaseModel):
    """Request payload for PUT /api/v1/rules/{rule_id}.

    Only non-None fields are applied. Immutable rules reject updates.

    Attributes:
        rule: Updated rule text.
        category: Updated category.
        confidence: Updated confidence.
        priority: Updated priority.
        tags: Updated tags.
    """

    rule: str | None = None
    category: RuleCategory | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    priority: RulePriority | None = None
    tags: list[str] | None = None


class RuleResponse(BaseModel):
    """Single rule in API responses.

    Attributes:
        id: Rule identifier.
        property_id: Property this rule belongs to.
        category: Rule category.
        rule: Human-readable rule text.
        confidence: Current confidence.
        evidence_count: Times applied.
        success_rate: Success ratio.
        source: Rule origin.
        immutable: Whether rule is immutable.
        priority: Priority level.
        tags: Classification tags.
        created_by: Creator identifier.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    id: str
    property_id: str
    category: str
    rule: str
    confidence: float
    evidence_count: int = 0
    success_rate: float | None = None
    source: str
    immutable: bool
    priority: str
    tags: list[str] = Field(default_factory=list)
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


class RuleListResponse(BaseModel):
    """Response for GET /api/v1/rules.

    Attributes:
        property_id: Queried property.
        rules: List of rules.
        total: Total count.
    """

    property_id: str
    rules: list[RuleResponse]
    total: int


# ═══════════════════════════════════════════════════════════════════════
# TEMPLATE MODELS
# ═══════════════════════════════════════════════════════════════════════


class TemplateRuleItem(BaseModel):
    """A single rule within a template.

    Attributes:
        category: Rule category.
        rule: Rule text.
        priority: Priority level.
        immutable: Whether rule should be immutable when applied.
        tags: Classification tags.
    """

    category: RuleCategory
    rule: str
    priority: RulePriority = RulePriority.MEDIUM
    immutable: bool = False
    tags: list[str] = Field(default_factory=list)


class TemplateCreateRequest(BaseModel):
    """Request payload for POST /api/v1/templates.

    Attributes:
        template_name: Human-readable template name.
        description: What this template provides.
        rules: List of rule definitions.
        version: Template version string.
    """

    template_name: str
    description: str = ""
    rules: list[TemplateRuleItem]
    version: str = "1.0"


class TemplateUpdateRequest(BaseModel):
    """Request payload for PUT /api/v1/templates/{template_id}.

    Attributes:
        template_name: Updated name.
        description: Updated description.
        rules: Updated rule list.
        version: Updated version.
    """

    template_name: str | None = None
    description: str | None = None
    rules: list[TemplateRuleItem] | None = None
    version: str | None = None


class TemplateResponse(BaseModel):
    """Single template in API responses.

    Attributes:
        template_id: Template identifier.
        template_name: Human-readable name.
        description: Template description.
        rules: Rule definitions.
        version: Version string.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    template_id: str
    template_name: str
    description: str = ""
    rules: list[TemplateRuleItem] = Field(default_factory=list)
    version: str = "1.0"
    created_at: str = ""
    updated_at: str = ""


class TemplateListResponse(BaseModel):
    """Response for GET /api/v1/templates.

    Attributes:
        templates: List of templates.
        total: Total count.
    """

    templates: list[TemplateResponse]
    total: int


class TemplateApplyRequest(BaseModel):
    """Request payload for POST /api/v1/templates/{id}/apply-bulk.

    Attributes:
        property_ids: List of property IDs to apply template to.
        created_by: Who is applying the template.
    """

    property_ids: list[str]
    created_by: str = ""


class TemplateApplyResponse(BaseModel):
    """Response for template apply endpoints.

    Attributes:
        template_id: Applied template.
        applied_to: Number of properties.
        rules_created: Total rules created.
        conflicts: Any conflicts detected.
        status: Overall status.
    """

    template_id: str
    applied_to: int
    rules_created: int
    conflicts: list[str] = Field(default_factory=list)
    status: str = "success"
