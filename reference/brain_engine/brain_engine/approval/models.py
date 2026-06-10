"""Модели данных Approval Gateway.

Определяет ApprovalRequest, ApprovalResponse и связанные перечисления
для отслеживания решений владельца в течение цикла разрешения инцидентов.

Дополнено (Phase 2): confidence_score и evidence_summary для
Confidence-Based Approval Routing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    """Types of actions that may require owner approval."""

    LATE_CHECKOUT = "late_checkout"
    CALL_CLEANER = "call_cleaner"
    DISPATCH_CLEANER = "dispatch_cleaner"
    SUBMIT_DAMAGE_CLAIM = "submit_damage_claim"
    SEND_ACCESS_CODE = "send_access_code"
    CONTACT_GUEST = "contact_guest"
    CHARGE_GUEST = "charge_guest"
    CALL_VENDOR = "call_vendor"
    ESCALATE_TO_MANAGER = "escalate_to_manager"
    OFFER_DISCOUNT = "offer_discount"
    SEND_WELCOME_MESSAGE = "send_welcome_message"


class ApprovalStatus(StrEnum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    AUTO_APPROVED = "auto_approved"


class ApprovalRequest(BaseModel):
    """A request for owner approval before executing an AI-proposed action.

    Attributes:
        request_id: Unique identifier for this approval request.
        action_type: Type of action requiring approval.
        owner_id: Property owner identifier.
        property_id: Property this action relates to.
        description: Human-readable description of the proposed action.
        proposed_action: Structured details of what AI wants to do.
        context: Additional context (guest info, incident details, etc.).
        urgency: Urgency level (1=low, 5=critical).
        timeout_seconds: How long to wait for owner response.
        fallback_action: What to do if owner doesn't respond in time.
        created_at: When the request was created.
        status: Current approval status.
        responded_at: When the owner responded.
        owner_response: Owner's response text (if any).
    """

    request_id: str = Field(default="", description="Unique request ID.")
    action_type: ActionType = Field(description="Type of action to approve.")
    owner_id: str = Field(default="", description="Owner identifier.")
    property_id: str = Field(default="", description="Property identifier.")
    description: str = Field(description="Human-readable action description.")
    proposed_action: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured action details.",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context for the decision.",
    )
    urgency: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Urgency level (1=low, 5=critical).",
    )
    timeout_seconds: int = Field(
        default=300,
        description="Seconds to wait before fallback.",
    )
    fallback_action: str = Field(
        default="notify_manager",
        description="What to do on timeout.",
    )
    confidence_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="AI confidence in the proposed action (0.0-1.0).",
    )
    confidence_tier: str | None = Field(
        default=None,
        description="Confidence tier: high, medium, low.",
    )
    evidence_summary: str | None = Field(
        default=None,
        description="Summary from EvidencePack for PM review.",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    responded_at: str | None = Field(default=None)
    owner_response: str | None = Field(default=None)

    def __repr__(self) -> str:
        return (
            f"ApprovalRequest(id={self.request_id!r}, "
            f"action={self.action_type.value!r}, "
            f"status={self.status.value!r})"
        )


class ApprovalResponse(BaseModel):
    """Owner's response to an approval request.

    Attributes:
        request_id: Which request this responds to.
        status: Approved or denied.
        owner_id: Who responded.
        message: Optional message from owner.
        apply_rule: Whether to create a preference rule from this decision.
        rule_scope: Scope for the preference rule.
    """

    request_id: str
    status: ApprovalStatus
    owner_id: str = ""
    message: str = ""
    apply_rule: bool = Field(
        default=False,
        description="Create a preference rule from this decision?",
    )
    rule_scope: str = Field(
        default="this_time",
        description="Rule scope: this_time, always, this_property, all_properties.",
    )


# Actions that never need approval (auto-approved).
# CRITICAL: SEND_ACCESS_CODE removed — access codes must be conditional
# on blockers (guest count confirmed, payment complete, ID verified).
# Moved to CONDITIONAL_APPROVE_ACTIONS below.
AUTO_APPROVE_ACTIONS: frozenset[ActionType] = frozenset({
    ActionType.SEND_WELCOME_MESSAGE,
})

# Actions that are auto-approved ONLY when no active blockers exist.
# BlockerEngine must check these before approval routing proceeds.
CONDITIONAL_APPROVE_ACTIONS: frozenset[ActionType] = frozenset({
    ActionType.SEND_ACCESS_CODE,
})

# Actions that always need approval.
ALWAYS_REQUIRE_APPROVAL: frozenset[ActionType] = frozenset({
    ActionType.SUBMIT_DAMAGE_CLAIM,
    ActionType.CHARGE_GUEST,
})

# Default timeout per urgency level (seconds).
URGENCY_TIMEOUTS: dict[int, int] = {
    1: 3600,    # 1 hour for low urgency
    2: 1800,    # 30 min
    3: 600,     # 10 min
    4: 300,     # 5 min
    5: 120,     # 2 min for critical
}
