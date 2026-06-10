"""WorkGraph event models — matches Cendra's WorkEventEnvelope contract.

Based on Botel.Contracts/WorkGraph/WorkEventEnvelope.cs (section 16.5).
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


class WorkEventCorrelation(BaseModel):
    """Correlation context for a WorkGraph event.

    Attributes:
        reservation_id: Related reservation.
        customer_id: Cendra tenant.
        org_id: Workspace GUID (not Auth0 org).
        work_item_id: Related work item.
        parent_work_item_id: Parent work item (if fork).
        origin: Event origin (kind + channel).
    """

    reservation_id: str = ""
    customer_id: str = ""
    org_id: str = ""
    work_item_id: str = ""
    parent_work_item_id: str = ""
    origin: dict[str, str] = Field(default_factory=dict)


class WorkEventEnvelope(BaseModel):
    """Azure Service Bus message from Cendra's WorkGraph.

    Attributes:
        event_id: Unique event identifier.
        event_type: Event type (ReplySent, TaskCreated, etc.).
        correlation: Correlation context.
        producer: Service that produced this event.
        trace_id: Distributed tracing ID.
        occurred_at: ISO 8601 timestamp.
        payload: Event-specific data.
    """

    event_id: str = ""
    event_type: str = ""
    correlation: WorkEventCorrelation = Field(
        default_factory=WorkEventCorrelation,
    )
    producer: str = ""
    trace_id: str = ""
    occurred_at: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
