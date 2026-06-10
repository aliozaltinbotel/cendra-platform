"""Parse a backend OTA change event into a refresh trigger.

The backend publishes one Service Bus message per changed entity to the
``botel-*-sync`` topics (subscription ``brain-engine-cascade``).  Each
message body is a **double-JSON-encoded** envelope; the Stage 3
freshness consumer turns it into an :class:`OtaRefreshEvent` — the
minimal tuple needed to mark the affected property stale and enqueue a
delta refresh.

Wire format (captured off the live ``booklyservicebus`` 2026-05): the
envelope carries the tenant tuple directly, so no resolver is needed::

    {
      "CustomerId": "<uuid>", "OrgId": "<uuid>",
      "ProviderType": "Hostaway", "ChannelEntityId": "<entity-id>",
      "DataJson": "{\\"PropertyChannelId\\": \\"323133\\", ...}"  # encoded
    }

* ``ProviderType`` arrives mixed-case (``"Hostaway"``) and is
  upper-cased to match the brain's ``TenantContext.provider_type``.
* ``PropertyChannelId`` lives inside the inner ``DataJson`` for both
  the reservation and conversation families.
* ``event_at`` is the broker enqueue time (when the backend emitted
  the change) — a uniform freshness signal across families, recorded on
  ``property_state.last_data_event_at``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["OtaRefreshEvent", "parse_ota_event"]


@dataclass(frozen=True, slots=True)
class OtaRefreshEvent:
    """The minimal change signal a refresh needs from one OTA event.

    Attributes:
        property_channel_id: Short Cendra channel id of the affected
            property (the ``property_state`` primary key).
        customer_id: Cendra customer UUID (envelope tenant tuple).
        org_id: Cendra workspace UUID, or ``None`` when absent.
        provider_type: Upper-cased PMS identifier (``"HOSTAWAY"`` …).
        entity_id: The changed entity's channel id
            (``ChannelEntityId`` — reservation / conversation id),
            used for per-``(topic, entity)`` dedup.
        event_at: Broker enqueue time of the change.
    """

    property_channel_id: str
    customer_id: str
    org_id: str | None
    provider_type: str
    entity_id: str
    event_at: datetime


def parse_ota_event(
    body: str | bytes,
    *,
    enqueued_at: datetime,
) -> OtaRefreshEvent:
    """Parse a ``botel-*-sync`` message body into a refresh event.

    Args:
        body: The raw message body — a double-JSON-encoded envelope.
        enqueued_at: The broker enqueue time of the message, used as
            the freshness ``event_at``.

    Raises:
        ValueError: when the body is not a JSON object, ``DataJson`` is
            missing / not a JSON object, or a required field
            (``PropertyChannelId`` / ``CustomerId`` / ``ProviderType``)
            is missing or blank.  The consumer dead-letters on this so a
            malformed change is never silently dropped.
    """

    envelope = _loads_object(body, "envelope")
    data = _loads_object(envelope.get("DataJson"), "DataJson")
    return OtaRefreshEvent(
        property_channel_id=_required(data, "PropertyChannelId"),
        customer_id=_required(envelope, "CustomerId"),
        org_id=_optional(envelope, "OrgId"),
        provider_type=_required(envelope, "ProviderType").upper(),
        entity_id=str(envelope.get("ChannelEntityId") or ""),
        event_at=enqueued_at,
    )


def _loads_object(raw: Any, label: str) -> dict[str, Any]:
    """Decode ``raw`` (str / bytes) to a JSON object or raise."""

    if not isinstance(raw, (str, bytes, bytearray)):
        raise ValueError(f"{label} is missing or not a JSON string")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _required(data: dict[str, Any], key: str) -> str:
    """Return a non-blank string field or raise ``ValueError``."""

    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or blank field {key!r}")
    return value


def _optional(data: dict[str, Any], key: str) -> str | None:
    """Return a non-blank string field, or ``None`` when absent."""

    value = data.get(key)
    return value if isinstance(value, str) and value.strip() else None
