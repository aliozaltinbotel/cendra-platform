"""Tests for the Stage 3 reactive-freshness domain primitives.

``mark_stale`` drives only the ``primed → stale`` edge (recording the
OTA event time) and leaves every other status untouched, so a webhook
never disturbs an in-flight bootstrap or a not-yet-primed property.
``submit_refresh_intent`` enqueues a delta refresh *only* for a genuine
primed property and no-ops (``not_primed``) otherwise — proven here
with a recording dispatcher, no live pipeline.
"""

from __future__ import annotations

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
    TENANT_SOURCE_SYNC,
    InMemoryPropertyStateStore,
    PropertyState,
    TenantContext,
    mark_stale,
    submit_refresh_intent,
)
from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage
from brain_engine.tenants.bootstrap_runner import BootstrapWorkload

pytestmark = pytest.mark.asyncio

_EVENT_AT = datetime(2026, 5, 30, 8, 0, tzinfo=UTC)
_NOW = datetime(2026, 5, 30, 9, 0, tzinfo=UTC)


def _tenant() -> TenantContext:
    return TenantContext(
        customer_id="cust-1",
        org_id="org-1",
        provider_type="HOSTAWAY",
        property_channel_id="p1",
        source=TENANT_SOURCE_SYNC,
    )


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
    """Captures the intent without running the in-process workload."""

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


# ── mark_stale ────────────────────────────────────────────────────


async def test_mark_stale_transitions_primed_to_stale() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(PROPERTY_STATUS_PRIMED))

    result = await mark_stale(store, "p1", event_at=_EVENT_AT, now=_NOW)

    assert result is not None
    assert result.status == PROPERTY_STATUS_STALE
    assert result.last_data_event_at == _EVENT_AT
    assert result.updated_at == _NOW
    # Persisted, not just returned.
    stored = await store.get("p1")
    assert stored is not None
    assert stored.status == PROPERTY_STATUS_STALE


@pytest.mark.parametrize(
    "status",
    [
        PROPERTY_STATUS_QUEUED,
        PROPERTY_STATUS_WARMING,
        PROPERTY_STATUS_COLD,
        PROPERTY_STATUS_FAILED,
        PROPERTY_STATUS_STALE,
    ],
)
async def test_mark_stale_leaves_non_primed_untouched(status: str) -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(status))

    result = await mark_stale(store, "p1", event_at=_EVENT_AT, now=_NOW)

    assert result is not None
    assert result.status == status  # unchanged
    assert result.last_data_event_at is None  # not stamped


async def test_mark_stale_missing_row_returns_none() -> None:
    store = InMemoryPropertyStateStore()  # empty
    assert await mark_stale(store, "p1", event_at=_EVENT_AT) is None


# ── submit_refresh_intent ─────────────────────────────────────────


async def test_submit_refresh_enqueues_delta_for_primed() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(PROPERTY_STATUS_PRIMED))
    dispatcher = _RecordingDispatcher()

    result = await submit_refresh_intent(
        property_channel_id="p1",
        tenant=_tenant(),
        pipeline=Any,  # never invoked — dispatcher discards the workload
        state_store=store,
        dispatcher=dispatcher,  # type: ignore[arg-type]
        event_at=_EVENT_AT,
        now=_NOW,
    )

    assert result.enqueued is True
    assert result.status == PROPERTY_STATUS_QUEUED
    assert len(dispatcher.intents) == 1
    intent = dispatcher.intents[0]
    assert intent.reason == "webhook"
    assert intent.window_days == 7  # small delta, not the full window
    # The row was stale-then-queued by the standard dedup path.
    stored = await store.get("p1")
    assert stored is not None
    assert stored.status == PROPERTY_STATUS_QUEUED


@pytest.mark.parametrize(
    "status",
    [PROPERTY_STATUS_COLD, PROPERTY_STATUS_WARMING],
)
async def test_submit_refresh_noops_for_non_primed(status: str) -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_state(status))
    dispatcher = _RecordingDispatcher()

    result = await submit_refresh_intent(
        property_channel_id="p1",
        tenant=_tenant(),
        pipeline=Any,
        state_store=store,
        dispatcher=dispatcher,  # type: ignore[arg-type]
        event_at=_EVENT_AT,
        now=_NOW,
    )

    assert result.enqueued is False
    assert result.reason == "not_primed"
    assert dispatcher.intents == []  # nothing enqueued
    stored = await store.get("p1")
    assert stored is not None
    assert stored.status == status  # untouched


async def test_submit_refresh_noops_for_missing_row() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()

    result = await submit_refresh_intent(
        property_channel_id="p1",
        tenant=_tenant(),
        pipeline=Any,
        state_store=store,
        dispatcher=dispatcher,  # type: ignore[arg-type]
        event_at=_EVENT_AT,
    )

    assert result.enqueued is False
    assert result.reason == "not_primed"
    assert dispatcher.intents == []
