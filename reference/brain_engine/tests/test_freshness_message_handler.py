"""Tests for :class:`FreshnessMessageHandler` settlement decisions.

The handler is the freshness consumer's brain: given a raw
``botel-*-sync`` event body it decides COMPLETE / ABANDON / DEAD_LETTER
and, for a genuinely primed property, marks it stale and enqueues a
refresh through the reused :func:`request_bootstrap` path.  Every branch
is covered with a real :class:`InMemoryPropertyStateStore` + a recording
dispatcher — no Azure, no Postgres, no live pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from brain_engine.tenants import (
    PROPERTY_STATUS_COLD,
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_STALE,
    PROPERTY_STATUS_WARMING,
    InMemoryPropertyStateStore,
    PropertyState,
)
from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage
from brain_engine.tenants.bootstrap_runner import BootstrapWorkload
from workers.bootstrap_message_handler import Settlement
from workers.freshness_message_handler import FreshnessMessageHandler

pytestmark = pytest.mark.asyncio

_ENQUEUED_AT = datetime(2026, 5, 30, 8, 0, tzinfo=UTC)


def _body(
    *,
    property_id: str = "p1",
    customer_id: str = "cust-1",
    org_id: str | None = "org-1",
    provider: str = "Hostaway",
    entity_id: str = "r-1",
) -> str:
    """Build a double-JSON-encoded ``botel-*-sync`` envelope."""
    data = json.dumps({"PropertyChannelId": property_id, "Status": "confirmed"})
    envelope: dict[str, Any] = {
        "CustomerId": customer_id,
        "OrgId": org_id,
        "ProviderType": provider,
        "ChannelEntityId": entity_id,
        "DataJson": data,
    }
    return json.dumps(envelope)


def _state(status: str) -> PropertyState:
    return PropertyState(
        property_channel_id="p1",
        customer_id="cust-1",
        provider_type="HOSTAWAY",
        org_id="org-1",
        status=status,
        last_bootstrap_at=datetime(2026, 5, 20, tzinfo=UTC),
    )


class _RecordingDispatcher:
    """Captures the enqueued intent without running the workload."""

    def __init__(self) -> None:
        self.intents: list[BootstrapIntentMessage] = []

    async def dispatch(
        self,
        *,
        property_channel_id: str,
        job_id: str,
        workload: BootstrapWorkload,
        intent: BootstrapIntentMessage,
    ) -> None:
        del property_channel_id, job_id, workload
        self.intents.append(intent)


class _GetRaisesStore(InMemoryPropertyStateStore):
    """Store whose point read fails — simulates a transient DB fault."""

    async def get(self, property_channel_id: str) -> PropertyState | None:
        raise RuntimeError("connection reset")


def _handler(store: Any, dispatcher: Any) -> FreshnessMessageHandler:
    return FreshnessMessageHandler(
        state_store=store,
        dispatcher=dispatcher,
        window_days=7,
    )


async def test_poison_body_is_dead_lettered() -> None:
    dispatcher = _RecordingDispatcher()
    handler = _handler(InMemoryPropertyStateStore(), dispatcher)
    assert await handler.handle("{not json", enqueued_at=_ENQUEUED_AT) is (
        Settlement.DEAD_LETTER
    )
    assert dispatcher.intents == []


async def test_missing_property_id_is_dead_lettered() -> None:
    dispatcher = _RecordingDispatcher()
    handler = _handler(InMemoryPropertyStateStore(), dispatcher)
    # Valid envelope, but DataJson lacks PropertyChannelId.
    envelope = json.dumps(
        {
            "CustomerId": "cust-1",
            "ProviderType": "Hostaway",
            "DataJson": json.dumps({"Status": "confirmed"}),
        }
    )
    assert await handler.handle(envelope, enqueued_at=_ENQUEUED_AT) is (
        Settlement.DEAD_LETTER
    )
    assert dispatcher.intents == []


async def test_absent_row_completes_without_refresh() -> None:
    store = InMemoryPropertyStateStore()  # empty — no row for p1
    dispatcher = _RecordingDispatcher()
    assert await _handler(store, dispatcher).handle(
        _body(), enqueued_at=_ENQUEUED_AT
    ) is Settlement.COMPLETE
    assert dispatcher.intents == []


@pytest.mark.parametrize(
    "status",
    [
        PROPERTY_STATUS_COLD,
        PROPERTY_STATUS_QUEUED,
        PROPERTY_STATUS_WARMING,
        PROPERTY_STATUS_FAILED,
    ],
)
async def test_non_primed_row_completes_without_refresh(status: str) -> None:
    # Cold / in-flight / failed are not refresh candidates: mark_stale
    # leaves them untouched, so the event drops (COMPLETE) and nothing
    # is enqueued — the full first-touch path owns those.
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(status))
    dispatcher = _RecordingDispatcher()
    assert await _handler(store, dispatcher).handle(
        _body(), enqueued_at=_ENQUEUED_AT
    ) is Settlement.COMPLETE
    assert dispatcher.intents == []


async def test_primed_row_marks_stale_and_enqueues_refresh() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(PROPERTY_STATUS_PRIMED))
    dispatcher = _RecordingDispatcher()

    assert await _handler(store, dispatcher).handle(
        _body(), enqueued_at=_ENQUEUED_AT
    ) is Settlement.COMPLETE

    # Row transitioned primed → stale → queued, OTA time recorded.
    final = await store.get("p1")
    assert final is not None
    assert final.status == PROPERTY_STATUS_QUEUED
    assert final.last_data_event_at == _ENQUEUED_AT

    # Exactly one refresh intent, tagged webhook with the delta window.
    assert len(dispatcher.intents) == 1
    intent = dispatcher.intents[0]
    assert intent.property_channel_id == "p1"
    assert intent.reason == "webhook"
    assert intent.window_days == 7
    assert intent.customer_id == "cust-1"
    assert intent.org_id == "org-1"
    assert intent.provider_type == "HOSTAWAY"  # upper-cased from envelope


async def test_already_stale_row_is_refreshed() -> None:
    # A nightly-sweep ``stale`` row that then receives an OTA event is a
    # legitimate refresh candidate: it stays stale and is enqueued.
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(PROPERTY_STATUS_STALE))
    dispatcher = _RecordingDispatcher()

    assert await _handler(store, dispatcher).handle(
        _body(), enqueued_at=_ENQUEUED_AT
    ) is Settlement.COMPLETE
    assert len(dispatcher.intents) == 1


async def test_state_read_fault_is_abandoned() -> None:
    dispatcher = _RecordingDispatcher()
    handler = _handler(_GetRaisesStore(), dispatcher)
    assert await handler.handle(_body(), enqueued_at=_ENQUEUED_AT) is (
        Settlement.ABANDON
    )
    assert dispatcher.intents == []
