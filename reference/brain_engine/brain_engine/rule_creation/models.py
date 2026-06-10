"""Rule creation data models — workflows, agents, rules."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowPhase(str, Enum):
    """Phase of the rule creation conversation."""

    GREETING = "greeting"
    INTENT_DISCOVERY = "intent_discovery"
    TYPE_DETERMINATION = "type_determination"
    DETAIL_COLLECTION = "detail_collection"
    CONFIRMATION = "confirmation"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"


class RuleType(str, Enum):
    """Types of rules that can be created."""

    LABEL = "label"
    TAG = "tag"
    AI_RULE = "ai_rule"
    LABEL_THEN_AI_RULE = "label_then_ai_rule"
    TAG_THEN_AI_RULE = "tag_then_ai_rule"
    LABEL_TAG_AI_RULE = "label_tag_ai_rule"


class LabelOperator(str, Enum):
    """Operators for label conditions."""

    EQUALS = "Equals"
    NOT_EQUALS = "NotEquals"
    GREATER_THAN = "GreaterThan"
    LESS_THAN = "LessThan"
    CONTAINS = "Contains"
    NOT_CONTAINS = "NotContains"
    IN = "In"
    NOT_IN = "NotIn"


# Reservation fields available for label-rule conditions
LABEL_FIELDS = [
    "reservationId", "status", "numberOfGuest", "numberOfNights",
    "totalPrice", "bookingChannel", "channelCode", "source",
    "listingId", "propertyId", "numberOfChildren", "isPaid",
    "isReturning", "checkInDate",
]


class LabelCondition(BaseModel):
    """A single condition in a label rule."""

    field: str = ""
    operator: LabelOperator = LabelOperator.EQUALS
    value: str = ""


class LabelComponent(BaseModel):
    """Label rule component — data-driven conditions."""

    name: str = ""
    icon: str = ""
    conditions: list[LabelCondition] = Field(default_factory=list)


class TagComponent(BaseModel):
    """Tag rule component — message pattern detection."""

    name: str = ""
    description: str = ""
    priority: str = "medium"
    keywords: list[str] = Field(default_factory=list)


class AIRuleComponent(BaseModel):
    """AI rule component — behavioral policy override."""

    name: str = ""
    description: str = ""
    expected_behavior: str = ""


class EscalationComponent(BaseModel):
    """Escalation behavior for a tag."""

    escalate_to: str = "pm"
    auto_create_task: bool = False
    task_priority: str = "Medium"
    notification_channel: str = "default"


class RuleBundle(BaseModel):
    """Complete rule bundle — one or more components."""

    bundle_name: str = ""
    rule_type: RuleType = RuleType.AI_RULE
    label_component: LabelComponent | None = None
    tag_component: TagComponent | None = None
    ai_rule_component: AIRuleComponent | None = None
    escalation_component: EscalationComponent | None = None


class ConversationState(BaseModel):
    """State of a rule creation conversation."""

    workflow_id: str = ""
    customer_id: str = ""
    phase: WorkflowPhase = WorkflowPhase.GREETING
    detected_language: str = "en"
    turn_count: int = 0
    rule_type: RuleType | None = None
    is_composite: bool = False
    components: list[str] = Field(default_factory=list)
    partial_bundle: RuleBundle = Field(default_factory=RuleBundle)
    confidence: float = 0.0
    context_summary: str = ""


class AgentMessage(BaseModel):
    """A message from a specialist agent."""

    agent_name: str = ""
    message: str = ""
    phase: WorkflowPhase = WorkflowPhase.GREETING
    next_phase: WorkflowPhase | None = None
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    needs_user_input: bool = True


class RuleCreationRequest(BaseModel):
    """Input for rule creation endpoints."""

    customer_id: str
    org_id: str = ""
    message: str = ""
    workflow_id: str = ""


class RuleCreationResponse(BaseModel):
    """Output of rule creation endpoints."""

    status: bool = True
    workflow_id: str = ""
    agent_message: str = ""
    phase: str = ""
    is_complete: bool = False
    rule_bundle: RuleBundle | None = None
    error: str | None = None
