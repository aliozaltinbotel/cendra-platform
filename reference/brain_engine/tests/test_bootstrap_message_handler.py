"""Tests for :class:`BootstrapMessageHandler` settlement decisions.

The handler is the worker's brain: given a raw queue body it decides
COMPLETE / ABANDON / DEAD_LETTER and drives the ``property_state`` FSM
through the reused :class:`BootstrapRunner`.  These tests cover every
settlement branch with a fake pipeline + fake store — no Azure, no
Postgres — which is exactly what keeps the worker's core fast to test.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

import pytest

from brain_engine.tenants import (
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_WARMING,
    InMemoryPropertyStateStore,
    PropertyState,
)
from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage
from workers.bootstrap_message_handler import (
    BootstrapMessageHandler,
    Settlement,
)

pytestmark = pytest.mark.asyncio


def _body(**overrides: Any) -> str:
    fields: dict[str, Any] = {
        "property_channel_id": "p1",
        "customer_id": "cust-1",
        "provider_type": "HOSTAWAY",
        "window_days": 30,
        "reason": "ui_select",
        "job_id": "job-1",
        "org_id": "org-1",
    }
    fields.update(overrides)
    return BootstrapIntentMessage(**fields).to_json()


def _queued_row() -> PropertyState:
    return PropertyState(
        property_channel_id="p1",
        customer_id="cust-1",
        provider_type="HOSTAWAY",
        org_id="org-1",
        status=PROPERTY_STATUS_QUEUED,
        current_job_id="job-1",
    )


@dataclass
class _FakeReport:
    conversations_loaded: int = 5
    cases_extracted: int = 3
    rules_emitted: int = 1
    profile_built: bool = True


class _RecordingPipeline:
    """Captures ``bootstrap_fast`` kwargs, returns a fixed report."""

    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def bootstrap_fast(self, **kwargs: Any) -> _FakeReport:
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return _FakeReport()


class _GetRaisesStore(InMemoryPropertyStateStore):
    """Store whose point read fails — simulates a transient DB fault."""

    async def get(self, property_channel_id: str) -> PropertyState | None:
        raise RuntimeError("connection reset")


class _UpdateRaisesStore(InMemoryPropertyStateStore):
    """Returns the queued row but fails the ``warming`` write."""

    async def update(self, state: PropertyState) -> PropertyState:
        raise RuntimeError("write timeout")


def _handler(store: Any, pipeline: Any) -> BootstrapMessageHandler:
    return BootstrapMessageHandler(
        pipeline=pipeline,
        state_store=store,
        timeout_seconds=None,
    )


async def test_poison_body_is_dead_lettered() -> None:
    handler = _handler(InMemoryPropertyStateStore(), _RecordingPipeline())
    assert await handler.handle("{not json") is Settlement.DEAD_LETTER


async def test_missing_required_field_is_dead_lettered() -> None:
    handler = _handler(InMemoryPropertyStateStore(), _RecordingPipeline())
    bad = '{"property_channel_id": "p1", "window_days": 30}'
    assert await handler.handle(bad) is Settlement.DEAD_LETTER


async def test_missing_row_is_dead_lettered() -> None:
    store = InMemoryPropertyStateStore()  # empty — no row for p1
    pipeline = _RecordingPipeline()
    assert await _handler(store, pipeline).handle(_body()) is (
        Settlement.DEAD_LETTER
    )
    assert pipeline.calls == []


async def test_state_read_fault_is_abandoned() -> None:
    handler = _handler(_GetRaisesStore(), _RecordingPipeline())
    assert await handler.handle(_body()) is Settlement.ABANDON


@pytest.mark.parametrize(
    "status",
    [PROPERTY_STATUS_WARMING, PROPERTY_STATUS_PRIMED],
)
async def test_non_queued_row_is_completed_without_running(
    status: str,
) -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(
        dataclasses.replace(_queued_row(), status=status),
    )
    pipeline = _RecordingPipeline()
    assert await _handler(store, pipeline).handle(_body()) is (
        Settlement.COMPLETE
    )
    assert pipeline.calls == []  # idempotency: no double bootstrap


async def test_queued_row_runs_and_completes_primed() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_row())
    pipeline = _RecordingPipeline()

    assert await _handler(store, pipeline).handle(_body()) is (
        Settlement.COMPLETE
    )

    final = await store.get("p1")
    assert final is not None
    assert final.status == PROPERTY_STATUS_PRIMED
    assert final.cases_extracted == 3
    assert len(pipeline.calls) == 1
    call = pipeline.calls[0]
    assert call["property_id"] == "p1"
    assert call["days"] == 30
    assert call["customer_id_override"] == "cust-1"
    assert call["org_id_override"] == "org-1"
    assert call["provider_type_override"] == "HOSTAWAY"
    assert call["job_id"] == "job-1"


async def test_pipeline_failure_is_recorded_and_completed() -> None:
    # The runner catches pipeline errors, marks the row ``failed`` and
    # returns normally → the message is COMPLETE (no Service Bus retry;
    # the orphan reaper / a fresh intent handle re-attempts).
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(_queued_row())
    pipeline = _RecordingPipeline(raise_exc=RuntimeError("graphql down"))

    assert await _handler(store, pipeline).handle(_body()) is (
        Settlement.COMPLETE
    )
    final = await store.get("p1")
    assert final is not None
    assert final.status != PROPERTY_STATUS_PRIMED
    assert final.last_error is not None


async def test_warming_write_fault_is_abandoned() -> None:
    store = _UpdateRaisesStore()
    await store.create_if_absent(_queued_row())
    handler = _handler(store, _RecordingPipeline())
    assert await handler.handle(_body()) is Settlement.ABANDON
