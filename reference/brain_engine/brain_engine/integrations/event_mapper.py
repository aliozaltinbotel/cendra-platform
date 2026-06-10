"""Event Mapper — maps WorkGraph events to Brain Engine internal actions.

Translates Cendra's WorkEventEnvelope into actions that update
Brain Engine's memory, learning, and autonomy systems.

Event type mapping:
    ReplySent              → Episodic memory + skill reinforcement
    ApprovalRequested      → Log pending approval
    ApprovalDecided        → Update adaptive autonomy stats
    TaskCreated            → Create active process
    TaskCompleted          → Complete active process
    InboundMessageReceived → Guest history update
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.integrations.event_models import WorkEventEnvelope

logger = logging.getLogger(__name__)


@dataclass
class InternalAction:
    """An action derived from a WorkGraph event.

    Attributes:
        action_type: Internal action identifier.
        target: Target system (episodic, skill, autonomy, process, guest).
        workspace_id: Tenant workspace.
        data: Action-specific payload.
    """

    action_type: str
    target: str
    workspace_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class EventMapper:
    """Maps WorkGraph event types to internal Brain Engine actions."""

    _MAPPING: dict[str, tuple[str, str]] = {
        "ReplySent": ("record_episode_and_reinforce", "episodic"),
        "ApprovalRequested": ("log_pending_approval", "autonomy"),
        "ApprovalDecided": ("update_autonomy_stats", "autonomy"),
        "TaskCreated": ("create_active_process", "process"),
        "TaskCompleted": ("complete_active_process", "process"),
        "InboundMessageReceived": ("update_guest_history", "guest"),
        "DraftCreated": ("record_episode", "episodic"),
        "WorkItemCreated": ("record_episode", "episodic"),
    }

    def map(self, envelope: WorkEventEnvelope) -> InternalAction | None:
        """Map a WorkGraph event to an internal action.

        Args:
            envelope: The incoming WorkGraph event.

        Returns:
            InternalAction or None if event type is unknown.
        """
        mapping = self._MAPPING.get(envelope.event_type)
        if not mapping:
            logger.debug(
                "Unknown WorkGraph event type: %s", envelope.event_type,
            )
            return None

        action_type, target = mapping
        return InternalAction(
            action_type=action_type,
            target=target,
            workspace_id=envelope.correlation.org_id,
            data={
                "event_id": envelope.event_id,
                "event_type": envelope.event_type,
                "reservation_id": envelope.correlation.reservation_id,
                "customer_id": envelope.correlation.customer_id,
                "work_item_id": envelope.correlation.work_item_id,
                "occurred_at": envelope.occurred_at,
                "payload": envelope.payload,
                "trace_id": envelope.trace_id,
            },
        )
