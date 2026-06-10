"""OPS pipeline data models.

Models for the operations pipeline: message generation,
reply parsing, issue classification, message verification,
and PM agent.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────── #


class RecipientType(str, Enum):
    """Who receives the OPS message."""

    CLEANER = "cleaner"
    VENDOR = "vendor"
    OWNER = "owner"
    GUEST = "guest"


class OpsMessageType(str, Enum):
    """Type of OPS message to generate."""

    TURNOVER_NOTIFICATION = "turnover_notification"
    VENDOR_DISPATCH = "vendor_dispatch"
    ESCALATION_TO_OWNER = "escalation_to_owner"
    STATUS_UPDATE = "status_update"
    APPROVAL_REQUEST = "approval_request"


class VendorCategory(str, Enum):
    """Vendor specialty categories for issue routing."""

    HVAC = "hvac"
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    LOCKSMITH = "locksmith"
    APPLIANCE_REPAIR = "appliance_repair"
    PEST_CONTROL = "pest_control"
    GENERAL_MAINTENANCE = "general_maintenance"
    CLEANING = "cleaning"


class OpsUrgency(str, Enum):
    """Urgency level for OPS tasks."""

    LOW = "low"
    NORMAL = "normal"
    URGENT = "urgent"


class PmAction(str, Enum):
    """Actions parsed from PM instructions."""

    ASSIGN_CONTACT = "assign_contact"
    APPROVE = "approve"
    REJECT = "reject"
    MEMORY_NOTE = "memory_note"
    ASK_FOLLOWUP = "ask_followup"
    UNKNOWN = "unknown"


# ── OPS Generate Message ─────────────────────────────────────── #


class OpsGenerateRequest(BaseModel):
    """Input to POST /api/v1/ops/generate-message."""

    customer_id: str
    org_id: str = ""
    property_id: str = ""
    recipient_type: RecipientType
    message_type: OpsMessageType
    context: dict[str, Any] = Field(default_factory=dict)
    language_override: str = ""


class OpsGenerateResponse(BaseModel):
    """Output of generate-message."""

    status: bool = True
    message: str = ""
    requires_approval: bool = False
    suggested_urgency: OpsUrgency = OpsUrgency.NORMAL
    detected_language: str = "en"
    error: str | None = None


# ── OPS Parse Reply ──────────────────────────────────────────── #


class OpsParseReplyRequest(BaseModel):
    """Input to POST /api/v1/ops/parse-reply."""

    customer_id: str
    org_id: str = ""
    contact_id: str = ""
    original_message_type: str = ""
    original_context: dict[str, Any] = Field(default_factory=dict)
    reply_text: str = ""
    cost_threshold: float | None = None


class OpsParseReplyData(BaseModel):
    """Structured data extracted from vendor/cleaner reply."""

    confirmed: bool = False
    arrival_time: str | None = None
    cost_mentioned: float | None = None
    cost_exceeds_threshold: bool = False
    availability_issue: str | None = None
    additional_notes: str = ""
    needs_followup: bool = False
    suggested_actions: list[str] = Field(default_factory=list)


class OpsParseReplyResponse(BaseModel):
    """Output of parse-reply."""

    status: bool = True
    data: OpsParseReplyData = Field(default_factory=OpsParseReplyData)
    error: str | None = None


# ── OPS Classify Issue ───────────────────────────────────────── #


class OpsClassifyRequest(BaseModel):
    """Input to POST /api/v1/ops/classify-issue."""

    customer_id: str
    org_id: str = ""
    property_id: str = ""
    task_description: str = ""
    guest_messages: list[str] = Field(default_factory=list)
    sub_category: str = ""
    main_category: str = ""


class VendorMatch(BaseModel):
    """A matched vendor category with urgency."""

    category: VendorCategory
    urgency: OpsUrgency = OpsUrgency.NORMAL
    reason: str = ""
    confidence: float = 0.8


class OpsClassifyResponse(BaseModel):
    """Output of classify-issue."""

    status: bool = True
    vendor_categories: list[VendorMatch] = Field(default_factory=list)
    overall_urgency: OpsUrgency = OpsUrgency.NORMAL
    reasoning: str = ""
    error: str | None = None


# ── OPS Verify Message ───────────────────────────────────────── #


class OpsVerifyRequest(BaseModel):
    """Input to POST /api/v1/ops/verify-message."""

    generated_message: str = ""
    provided_context: dict[str, Any] = Field(default_factory=dict)
    recipient_type: RecipientType = RecipientType.GUEST


class OpsVerifyResponse(BaseModel):
    """Output of verify-message."""

    status: bool = True
    is_safe: bool = True
    issues: list[str] = Field(default_factory=list)
    error: str | None = None


# ── OPS PM Agent ─────────────────────────────────────────────── #


class OpsContext(BaseModel):
    """Context for the PM agent."""

    property_id: str = ""
    property_name: str = ""
    customer_id: str = ""
    org_id: str = ""
    trigger_type: str = ""
    escalation_reason: str = ""
    task_description: str = ""
    reservation_id: str = ""
    contacts_tried: list[dict[str, Any]] = Field(default_factory=list)


class PlannedAction(BaseModel):
    """A single action in the PM agent's plan."""

    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    tier: str = "medium"
    description: str = ""


class PmAgentRequest(BaseModel):
    """Input to POST /api/v1/ops/pm-agent."""

    pm_message: str = ""
    ops_context: OpsContext = Field(default_factory=OpsContext)
    autonomy_mode: str = Field(default="copilot", description="copilot or autopilot")


class PmAgentResponse(BaseModel):
    """Output of pm-agent."""

    status: bool = True
    action_plan: list[PlannedAction] = Field(default_factory=list)
    message_to_pm: str = ""
    needs_clarification: bool = False
    reads_performed: list[str] = Field(default_factory=list)
    error: str | None = None


# ── OPS Parse PM Instruction ─────────────────────────────────── #


class OpsParsePmInstructionRequest(BaseModel):
    """Input to POST /api/v1/ops/parse-pm-instruction."""

    pm_message: str = ""
    current_context: dict[str, Any] = Field(default_factory=dict)
    property_id: str = ""
    property_name: str = ""
    customer_id: str = ""
    org_id: str = ""


class OpsParsePmInstructionData(BaseModel):
    """Parsed PM instruction data."""

    action: PmAction = PmAction.UNKNOWN
    confidence: float = 0.0
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_role: str | None = None
    vendor_category: str | None = None
    approved: bool | None = None
    memory_content: str | None = None
    follow_up_question: str | None = None
    clarification_message: str | None = None


class OpsParsePmInstructionResponse(BaseModel):
    """Output of parse-pm-instruction."""

    status: bool = True
    data: OpsParsePmInstructionData = Field(
        default_factory=OpsParsePmInstructionData,
    )
    error: str | None = None
