"""Data models for the Adaptive Preferences Engine.

Defines PreferenceRule and supporting types for storing owner-specific rules
that adapt the Brain Engine behavior per owner and per property.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RuleScope(StrEnum):
    """Scope of a preference rule."""

    THIS_TIME = "this_time"
    THIS_PROPERTY = "this_property"
    ALL_PROPERTIES = "all_properties"
    ALWAYS = "always"
    CONDITIONAL = "conditional"


class PreferenceRule(BaseModel):
    """A learned preference rule for a specific owner/property/action combination.

    Attributes:
        rule_id: Unique rule identifier.
        owner_id: Property owner this rule belongs to.
        property_id: Specific property (empty = all properties).
        action_type: Action type this rule applies to.
        auto_approve: Whether to auto-approve this action.
        scope: How broadly this rule applies.
        conditions: Optional conditions under which this rule applies.
        priority: Higher priority rules override lower ones.
        created_at: When this rule was created.
        updated_at: Last update timestamp.
        created_from: Which approval request created this rule.
        usage_count: How many times this rule has been applied.
        active: Whether this rule is currently active.
    """

    rule_id: str = Field(default="", description="Unique rule identifier.")
    owner_id: str = Field(description="Owner who created this rule.")
    property_id: str = Field(
        default="",
        description="Property ID (empty = all properties for this owner).",
    )
    action_type: str = Field(description="Action type this rule covers.")
    auto_approve: bool = Field(
        default=True,
        description="Auto-approve (True) or auto-deny (False).",
    )
    scope: RuleScope = Field(default=RuleScope.THIS_PROPERTY)
    conditions: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Conditions for rule activation, e.g. "
            "{'guest_rating_min': 4.5, 'max_fee': 100}."
        ),
    )
    priority: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Rule priority (higher overrides lower).",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    created_from: str = Field(
        default="",
        description="Approval request ID that generated this rule.",
    )
    usage_count: int = Field(default=0)
    active: bool = Field(default=True)

    def __repr__(self) -> str:
        return (
            f"PreferenceRule(id={self.rule_id!r}, "
            f"owner={self.owner_id!r}, "
            f"action={self.action_type!r}, "
            f"approve={self.auto_approve}, "
            f"scope={self.scope.value!r})"
        )


class LearningQuestion(BaseModel):
    """A follow-up question asked to the owner after an approval decision.

    Attributes:
        question_id: Unique question identifier.
        request_id: The approval request this follows up on.
        question_text: The question to ask the owner.
        question_type: Type of question (scope, condition, frequency).
        options: Possible answers for the owner to choose from.
        answer: Owner's answer (filled after response).
    """

    question_id: str = Field(default="")
    request_id: str = Field(description="Related approval request.")
    question_text: str = Field(description="Question to ask the owner.")
    question_type: str = Field(
        default="scope",
        description="Type: scope, condition, frequency.",
    )
    options: list[str] = Field(default_factory=list)
    answer: str | None = Field(default=None)
