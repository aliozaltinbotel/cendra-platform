"""Tests for the OTA change-event parser (Stage 3 freshness A2a).

Fixtures mirror the real ``botel-*-sync`` wire format captured off the
live ``booklyservicebus`` namespace: a double-JSON-encoded envelope
carrying the tenant tuple, with ``PropertyChannelId`` inside the inner
``DataJson`` and a mixed-case ``ProviderType``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from brain_engine.integrations.ota_event import (
    OtaRefreshEvent,
    parse_ota_event,
)

_ENQUEUED = datetime(2026, 5, 27, 12, 27, 56, tzinfo=UTC)


def _body(*, data: dict[str, object], **envelope: object) -> str:
    """Build a double-JSON-encoded message body like the backend sends."""
    full = {"DataJson": json.dumps(data), **envelope}
    return json.dumps(full)


def _reservation_body() -> str:
    return _body(
        data={"PropertyChannelId": "323133", "Status": "confirmed"},
        CustomerId="92bbd684-220b-4b73-a9c2-be9ff4a2e55d",
        OrgId="dc7822aa-b002-42db-84a3-e7781afc1d07",
        ProviderType="Hostaway",
        ChannelEntityId="57183477",
    )


def test_parses_reservation_envelope() -> None:
    event = parse_ota_event(_reservation_body(), enqueued_at=_ENQUEUED)
    assert event == OtaRefreshEvent(
        property_channel_id="323133",
        customer_id="92bbd684-220b-4b73-a9c2-be9ff4a2e55d",
        org_id="dc7822aa-b002-42db-84a3-e7781afc1d07",
        provider_type="HOSTAWAY",  # upper-cased from "Hostaway"
        entity_id="57183477",
        event_at=_ENQUEUED,
    )


def test_parses_conversation_envelope() -> None:
    body = _body(
        data={
            "PropertyChannelId": "309384",
            "ReservationChannelId": "9001",
            "MessageCount": 3,
        },
        CustomerId="92bbd684-220b-4b73-a9c2-be9ff4a2e55d",
        OrgId="dc7822aa-b002-42db-84a3-e7781afc1d07",
        ProviderType="Lodgify",
        ChannelEntityId="43539870",
    )
    event = parse_ota_event(body, enqueued_at=_ENQUEUED)
    assert event.property_channel_id == "309384"
    assert event.provider_type == "LODGIFY"
    assert event.entity_id == "43539870"


def test_org_id_optional_when_absent() -> None:
    body = _body(
        data={"PropertyChannelId": "323133"},
        CustomerId="cust-1",
        ProviderType="Hostaway",
    )
    event = parse_ota_event(body, enqueued_at=_ENQUEUED)
    assert event.org_id is None
    assert event.entity_id == ""  # ChannelEntityId absent → empty


@pytest.mark.parametrize(
    "body",
    [
        "{not json",  # envelope not JSON
        json.dumps(
            {"CustomerId": "c", "ProviderType": "Hostaway"}
        ),  # no DataJson
        json.dumps(
            {"DataJson": "[1,2,3]", "CustomerId": "c"}
        ),  # DataJson not object
    ],
)
def test_malformed_body_raises_value_error(body: str) -> None:
    with pytest.raises(ValueError):
        parse_ota_event(body, enqueued_at=_ENQUEUED)


def test_missing_property_channel_id_raises() -> None:
    body = _body(
        data={"Status": "confirmed"},  # no PropertyChannelId
        CustomerId="cust-1",
        ProviderType="Hostaway",
    )
    with pytest.raises(ValueError, match="PropertyChannelId"):
        parse_ota_event(body, enqueued_at=_ENQUEUED)


def test_missing_tenant_fields_raise() -> None:
    no_customer = _body(
        data={"PropertyChannelId": "323133"},
        ProviderType="Hostaway",
    )
    with pytest.raises(ValueError, match="CustomerId"):
        parse_ota_event(no_customer, enqueued_at=_ENQUEUED)

    no_provider = _body(
        data={"PropertyChannelId": "323133"},
        CustomerId="cust-1",
    )
    with pytest.raises(ValueError, match="ProviderType"):
        parse_ota_event(no_provider, enqueued_at=_ENQUEUED)
