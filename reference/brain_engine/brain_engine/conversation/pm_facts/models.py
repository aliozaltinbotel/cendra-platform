"""Value objects for PM-provided knowledge facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["PmFact"]


@dataclass(frozen=True, slots=True)
class PmFact:
    """One PM-confirmed knowledge fact, scoped per property.

    Stored when a manager replies through the PM Chat surface
    (POST /api/v1/regenerate-pm-knowledge or
    /api/v1/regenerate-escalation-resolution).  The next guest
    message for the same property reads the fact back from the
    store and folds it into the system prompt so the AI can answer
    directly instead of re-flagging the gap.

    Attributes:
        customer_id: Owning customer identifier (always present).
        org_id: Customer's organisation; empty string when unknown.
        property_channel_id: Property scope.  Empty string means
            "applies to every property under this customer" — used
            when the PM Chat does not pass a property selection.
        fact_text: Raw manager-supplied knowledge (free-form text).
        source_message_id: ``message_id`` from the original guest
            turn that triggered the gap.  Empty string when unknown.
        created_at: UTC timestamp when the fact was persisted.
    """

    customer_id: str
    org_id: str
    property_channel_id: str
    fact_text: str
    source_message_id: str
    created_at: datetime
