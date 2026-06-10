"""Interrupt data models — interrupt state, decisions, and payloads.

Defines the core data structures for the interrupt/resume lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class InterruptStatus(StrEnum):
    """Lifecycle states for an interrupt."""

    PENDING = "pending"
    AWAITING_HUMAN = "awaiting_human"
    RESUMED = "resumed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InterruptDecision(StrEnum):
    """Possible human decisions for an interrupt."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    RESPOND = "respond"
    IGNORE = "ignore"


class Interrupt(BaseModel):
    """A frozen interrupt point waiting for human input.

    Attributes:
        id: Unique interrupt identifier.
        value: Data sent to the client for review.
        tool_name: The tool that triggered the interrupt.
        tool_args: Arguments the tool was called with.
        session_id: Session this interrupt belongs to.
        status: Current lifecycle status.
        snapshot_name: BrainZFS snapshot taken at interrupt.
        created_at: When the interrupt was created.
        description: Human-readable description of what needs review.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    value: Any = None
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""
    status: InterruptStatus = InterruptStatus.PENDING
    snapshot_name: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    description: str = ""

    @property
    def is_terminal(self) -> bool:
        """Whether the interrupt has been resolved."""
        return self.status in {
            InterruptStatus.RESUMED,
            InterruptStatus.EXPIRED,
            InterruptStatus.CANCELLED,
        }

    def to_client_payload(self) -> dict[str, Any]:
        """Format for sending to the client/UI.

        Returns:
            Dict with interrupt details for client rendering.
        """
        return {
            "interrupt_id": self.id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "description": self.description,
            "value": self.value,
            "created_at": self.created_at.isoformat(),
        }


class ResumePayload(BaseModel):
    """Human's response to an interrupt.

    Attributes:
        interrupt_id: ID of the interrupt being resumed.
        decision: The human's decision.
        data: Optional data (edited args, text response, etc.).
        reason: Optional reason for the decision.
    """

    interrupt_id: str
    decision: InterruptDecision
    data: Any = None
    reason: str = ""
