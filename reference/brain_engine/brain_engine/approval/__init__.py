"""Approval Gateway — Human-in-the-Loop с Confidence-Based Routing.

ApprovalGateway перехватывает AI-решения перед выполнением,
маршрутизирует через ConfidenceRouter (HIGH/MEDIUM/LOW),
отправляет уведомления владельцу через WhatsApp/Telegram.
"""

from brain_engine.approval.confidence_router import (
    ConfidenceRouter,
    ConfidenceTier,
    EvidencePack,
    RoutingDecision,
)
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.approval.models import (
    ActionType,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalStatus,
)

__all__ = [
    "ActionType",
    "ApprovalGateway",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalStatus",
    "ConfidenceRouter",
    "ConfidenceTier",
    "EvidencePack",
    "RoutingDecision",
]
