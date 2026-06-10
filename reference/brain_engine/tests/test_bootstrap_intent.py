"""Tests for :func:`request_bootstrap` and its dispatcher Protocol.

Three dedup layers under test plus the dispatcher contract:

1. Status layer — ``primed`` + fresh row short-circuits without
   touching the dispatcher.
2. In-flight layer — ``queued`` / ``warming`` short-circuits
   without touching the dispatcher.
3. Race-loser layer — a second caller that hits ``create_if_absent``
   after a concurrent winner observes the winner's status and
   bails out.

The dispatcher is exercised in three forms:

* :class:`AsyncioBootstrapDispatcher` schedules a real
  ``asyncio.Task`` and the test awaits it to confirm the
  workload ran.
* A recording fake dispatcher (test-local) captures one
  invocation per intent and asserts the workload is the
  callable the factory produced.
* A failing dispatcher confirms exceptions raised during
  ``dispatch`` propagate to the caller (intent semantics:
  dispatch failure means "no enqueue", not "silently lost").

Invalid input (blank channel id, blank customer id) is rejected
without persisting anything — guards the contract documented in
:class:`BootstrapIntentResult.reason`.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from brain_engine.tenants import (
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_STALE,
    PROPERTY_STATUS_WARMING,
    TENANT_SOURCE_REGISTRY,
    AsyncioBootstrapDispatcher,
    BootstrapDispatcher,
    BootstrapIntentMessage,
    BootstrapIntentResult,
    BootstrapWorkload,
    InMemoryPropertyStateStore,
    PropertyState,
    TenantContext,
    request_bootstrap,
)


def _tenant(
    *,
    customer_id: str = "cust",
    org_id: str | None = "org",
    provider_type: str = "HOSTAWAY",
    property_channel_id: str = "p1",
) -> TenantContext:
    return TenantContext(
        customer_id=customer_id,
        org_id=org_id,
        provider_type=provider_type,
        property_channel_id=property_channel_id,
        source=TENANT_SOURCE_REGISTRY,
    )


def _no_op_factory() -> object:
    """A workload_factory that returns a no-op coroutine.

    Cast at the call site to the proper type — tests that
    short-circuit before dispatch never invoke it, so its
    callable shape is the only thing that matters.
    """

    def factory(
        state: PropertyState, job_id: str,
    ) -> BootstrapWorkload:
        async def workload() -> None:
            return None

        return workload

    return factory


def _message(
    property_channel_id: str = "prop1",
    job_id: str = "job1",
) -> BootstrapIntentMessage:
    """Build a minimal valid intent message for dispatcher tests."""

    return BootstrapIntentMessage(
        property_channel_id=property_channel_id,
        customer_id="cust",
        provider_type="LODGIFY",
        window_days=365,
        reason="ui_select",
        job_id=job_id,
    )


class _RecordingDispatcher:
    """Captures one dispatch per intent for inspection."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, BootstrapWorkload]] = []

    async def dispatch(
        self,
        *,
        property_channel_id: str,
        job_id: str,
        workload: BootstrapWorkload,
        intent: BootstrapIntentMessage,
    ) -> None:
        self.calls.append((property_channel_id, job_id, workload))


class _FailingDispatcher:
    """Raises on dispatch — proves errors propagate to caller."""

    async def dispatch(
        self,
        *,
        property_channel_id: str,
        job_id: str,
        workload: BootstrapWorkload,
        intent: BootstrapIntentMessage,
    ) -> None:
        raise RuntimeError("dispatcher boom")


# ── Invalid input ───────────────────────────────────────────────


async def test_request_bootstrap_rejects_blank_property_id() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast(
            "object", _no_op_factory(),
        ),  # never invoked
    )
    assert result.enqueued is False
    assert result.reason == "invalid_input"
    assert result.state is None
    assert dispatcher.calls == []


async def test_request_bootstrap_rejects_blank_customer_id() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()
    tenant = _tenant(customer_id="")
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=tenant,
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    assert result.enqueued is False
    assert result.reason == "invalid_input"
    assert dispatcher.calls == []


# ── Cold path (no row exists) ───────────────────────────────────


async def test_cold_property_enqueues_and_persists_queued() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    assert result.enqueued is True
    assert result.status == PROPERTY_STATUS_QUEUED
    assert result.reason == "new"
    assert result.state is not None
    assert result.state.status == PROPERTY_STATUS_QUEUED
    assert result.state.current_job_id is not None
    persisted = await store.get("p1")
    assert persisted is not None
    assert persisted.status == PROPERTY_STATUS_QUEUED
    assert len(dispatcher.calls) == 1
    channel, job_id, workload = dispatcher.calls[0]
    assert channel == "p1"
    assert job_id == result.state.current_job_id
    assert callable(workload)


async def test_cold_property_uses_provided_job_id() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
        job_id="custom-job-id",
    )
    assert result.state is not None
    assert result.state.current_job_id == "custom-job-id"
    assert dispatcher.calls[0][1] == "custom-job-id"


# ── Primed + fresh → no-op ──────────────────────────────────────


async def test_primed_fresh_short_circuits() -> None:
    store = InMemoryPropertyStateStore()
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    primed = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_PRIMED,
        last_bootstrap_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(days=7),
        updated_at=now - timedelta(hours=2),
    )
    await store.create_if_absent(primed)
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
        now=now,
    )
    assert result.enqueued is False
    assert result.status == PROPERTY_STATUS_PRIMED
    assert result.reason == "primed_fresh"
    assert dispatcher.calls == []


# ── Primed + fresh BUT profile missing → self-heal ──────────────


async def _profile_present(_property_channel_id: str) -> bool:
    return True


async def _profile_absent(_property_channel_id: str) -> bool:
    return False


def _primed_fresh_state(now: datetime) -> PropertyState:
    """A ``primed`` row last bootstrapped two hours ago (fresh)."""
    return PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_PRIMED,
        last_bootstrap_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(days=7),
        updated_at=now - timedelta(hours=2),
    )


async def test_primed_fresh_with_existing_profile_still_short_circuits() -> None:
    """A profile probe confirming the profile exists keeps the
    primed+fresh no-op — no behaviour change for healthy rows."""
    store = InMemoryPropertyStateStore()
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    await store.create_if_absent(_primed_fresh_state(now))
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
        profile_exists_probe=_profile_present,
        now=now,
    )
    assert result.enqueued is False
    assert result.reason == "primed_fresh"
    assert dispatcher.calls == []


async def test_primed_fresh_without_profile_self_heals() -> None:
    """A primed+fresh row whose profile is MISSING must re-enqueue
    instead of short-circuiting — closes the state/profile desync that
    otherwise blocks re-harvest forever (agent stuck on 'no data')."""
    store = InMemoryPropertyStateStore()
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    await store.create_if_absent(_primed_fresh_state(now))
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
        profile_exists_probe=_profile_absent,
        now=now,
    )
    assert result.enqueued is True
    assert result.status == PROPERTY_STATUS_QUEUED
    assert result.reason == "new"
    assert len(dispatcher.calls) == 1
    persisted = await store.get("p1")
    assert persisted is not None
    assert persisted.status == PROPERTY_STATUS_QUEUED


async def test_primed_stale_re_enqueues() -> None:
    store = InMemoryPropertyStateStore()
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    primed = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_PRIMED,
        last_bootstrap_at=now - timedelta(days=10),
        first_seen_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=10),
    )
    await store.create_if_absent(primed)
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="stale_refresh",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
        fresh_window=timedelta(days=1),
        now=now,
    )
    assert result.enqueued is True
    assert result.status == PROPERTY_STATUS_QUEUED
    assert dispatcher.calls != []


async def test_primed_without_last_bootstrap_at_re_enqueues() -> None:
    # Programming bug guard: a primed row missing
    # last_bootstrap_at means stale, not "skip silently".
    store = InMemoryPropertyStateStore()
    primed = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_PRIMED,
        last_bootstrap_at=None,
    )
    await store.create_if_absent(primed)
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    assert result.enqueued is True
    assert result.reason == "new"


# ── In-flight (queued / warming) → no-op ────────────────────────


async def test_queued_row_short_circuits() -> None:
    store = InMemoryPropertyStateStore()
    queued = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_QUEUED,
        current_job_id="job-1",
    )
    await store.create_if_absent(queued)
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    assert result.enqueued is False
    assert result.status == PROPERTY_STATUS_QUEUED
    assert result.reason == "in_flight"
    assert dispatcher.calls == []


async def test_warming_row_short_circuits() -> None:
    store = InMemoryPropertyStateStore()
    warming = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_WARMING,
        current_job_id="job-1",
    )
    await store.create_if_absent(warming)
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    assert result.enqueued is False
    assert result.status == PROPERTY_STATUS_WARMING
    assert result.reason == "in_flight"
    assert dispatcher.calls == []


# ── Failed / stale → eligible ───────────────────────────────────


async def test_failed_row_re_enqueues() -> None:
    store = InMemoryPropertyStateStore()
    failed = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_FAILED,
        last_error="boom",
        retry_count=2,
    )
    await store.create_if_absent(failed)
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    assert result.enqueued is True
    assert result.state is not None
    # Retry count is preserved across re-enqueue — the runner
    # will reset it to 0 on the next successful primed.
    assert result.state.retry_count == 2
    # last_error is cleared on re-enqueue to avoid stale message.
    assert result.state.last_error is None


async def test_stale_row_re_enqueues() -> None:
    store = InMemoryPropertyStateStore()
    stale = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
        org_id="org",
        status=PROPERTY_STATUS_STALE,
    )
    await store.create_if_absent(stale)
    dispatcher = _RecordingDispatcher()
    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="stale_refresh",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    assert result.enqueued is True
    assert result.state is not None
    assert result.state.status == PROPERTY_STATUS_QUEUED


# ── create_if_absent race (loser sees the winner) ───────────────


async def test_concurrent_callers_dedup_via_status() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()
    first = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    second = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    # First wins, second sees QUEUED and bails out.
    assert first.enqueued is True
    assert second.enqueued is False
    assert second.status == PROPERTY_STATUS_QUEUED
    assert second.reason == "in_flight"
    assert len(dispatcher.calls) == 1


# ── AsyncioBootstrapDispatcher (real execution) ─────────────────


async def test_asyncio_dispatcher_runs_workload() -> None:
    invoked: list[str] = []
    store = InMemoryPropertyStateStore()
    dispatcher = AsyncioBootstrapDispatcher()

    def factory(
        state: PropertyState, job_id: str,
    ) -> BootstrapWorkload:
        async def workload() -> None:
            invoked.append(state.property_channel_id)

        return workload

    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=dispatcher,
        workload_factory=factory,
    )
    assert result.enqueued is True
    # Yield so the scheduled task runs.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert invoked == ["p1"]


async def test_asyncio_dispatcher_holds_strong_reference() -> None:
    # The task must not be garbage-collected before it runs.
    # We schedule a slow workload, immediately drop the
    # dispatcher reference, and assert the task still completed.
    completed = asyncio.Event()
    store = InMemoryPropertyStateStore()
    dispatcher = AsyncioBootstrapDispatcher()

    def factory(
        state: PropertyState, job_id: str,
    ) -> BootstrapWorkload:
        async def workload() -> None:
            await asyncio.sleep(0)
            completed.set()

        return workload

    await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=dispatcher,
        workload_factory=factory,
    )
    del dispatcher  # task must survive
    await asyncio.wait_for(completed.wait(), timeout=1.0)


# ── Dispatcher failure propagates ───────────────────────────────


async def test_dispatcher_exception_propagates_to_caller() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _FailingDispatcher()
    with pytest.raises(RuntimeError, match="dispatcher boom"):
        await request_bootstrap(
            property_channel_id="p1",
            tenant=_tenant(),
            window_days=30,
            reason="ui_select",
            state_store=store,
            dispatcher=cast(BootstrapDispatcher, dispatcher),
            workload_factory=cast("object", _no_op_factory()),
        )
    # The row is left in QUEUED — Stage 2 dead-letter or
    # the caller's retry budget is responsible for the cleanup.
    persisted = await store.get("p1")
    assert persisted is not None
    assert persisted.status == PROPERTY_STATUS_QUEUED


# ── Result object shape ────────────────────────────────────────


def test_bootstrap_intent_result_is_frozen() -> None:
    result = BootstrapIntentResult(
        enqueued=False,
        status="cold",
        state=None,
        reason="invalid_input",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.enqueued = True  # type: ignore[misc]


# ── Provider-type / org_id preservation ────────────────────────


async def test_seed_row_inherits_tenant_provider_and_org() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()
    await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(provider_type="LODGIFY", org_id=None),
        window_days=30,
        reason="ui_select",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
    )
    persisted = await store.get("p1")
    assert persisted is not None
    assert persisted.provider_type == "LODGIFY"
    assert persisted.org_id is None
    assert persisted.customer_id == "cust"


async def test_in_memory_store_implements_protocol_methods() -> None:
    # PropertyStateStore is a *type-hint* Protocol (not
    # runtime_checkable, intentionally — Protocol is for static
    # typing, not isinstance gates).  Verify duck-typing instead:
    # every method the Protocol declares must exist on the
    # implementation with an awaitable signature.
    store = InMemoryPropertyStateStore()
    assert callable(store.get)
    assert callable(store.create_if_absent)
    assert callable(store.update)
    # And the methods actually do their job — round-trip a row.
    seed = PropertyState(
        property_channel_id="p1",
        customer_id="cust",
        provider_type="HOSTAWAY",
    )
    await store.create_if_absent(seed)
    fetched = await store.get("p1")
    assert fetched == seed


# ── Adopt-existing (already-primed probe) ───────────────────────


async def test_cold_property_with_existing_profile_is_adopted() -> None:
    # A property already primed by a pre-SSoT bootstrap (its profile
    # exists) must be adopted as primed, NOT re-bootstrapped — this
    # is what stops the re-bootstrap storm when property_state starts
    # empty.
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()

    async def _always_primed(_cid: str) -> bool:
        return True

    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="first_touch",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
        already_primed_probe=_always_primed,
    )

    assert result.enqueued is False
    assert result.status == PROPERTY_STATUS_PRIMED
    assert result.reason == "adopted_existing"
    assert dispatcher.calls == []
    persisted = await store.get("p1")
    assert persisted is not None
    assert persisted.status == PROPERTY_STATUS_PRIMED
    assert persisted.last_bootstrap_at is not None


async def test_cold_property_without_profile_still_enqueues() -> None:
    store = InMemoryPropertyStateStore()
    dispatcher = _RecordingDispatcher()

    async def _never_primed(_cid: str) -> bool:
        return False

    result = await request_bootstrap(
        property_channel_id="p1",
        tenant=_tenant(),
        window_days=30,
        reason="first_touch",
        state_store=store,
        dispatcher=cast(BootstrapDispatcher, dispatcher),
        workload_factory=cast("object", _no_op_factory()),
        already_primed_probe=_never_primed,
    )

    assert result.enqueued is True
    assert result.status == PROPERTY_STATUS_QUEUED
    assert result.reason == "new"
    assert len(dispatcher.calls) == 1


# ── Dispatcher concurrency cap ──────────────────────────────────


async def test_dispatcher_concurrency_cap_serialises() -> None:
    # max_concurrency=1 ⇒ the second workload must wait for the
    # first to release the semaphore before it starts.
    dispatcher = AsyncioBootstrapDispatcher(max_concurrency=1)
    started: list[str] = []
    release = asyncio.Event()

    def _make(name: str) -> BootstrapWorkload:
        async def workload() -> None:
            started.append(name)
            await release.wait()

        return workload

    await dispatcher.dispatch(
        property_channel_id="a", job_id="1", workload=_make("a"),
        intent=_message("a", "1"),
    )
    await dispatcher.dispatch(
        property_channel_id="b", job_id="2", workload=_make("b"),
        intent=_message("b", "2"),
    )
    for _ in range(5):
        await asyncio.sleep(0)
    # Only the first holds the single permit.
    assert started == ["a"]

    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    # First released the permit → second ran.
    assert started == ["a", "b"]


async def test_dispatcher_unbounded_by_default() -> None:
    # No cap ⇒ both workloads start concurrently (neither blocks).
    dispatcher = AsyncioBootstrapDispatcher()
    started: list[str] = []
    release = asyncio.Event()

    def _make(name: str) -> BootstrapWorkload:
        async def workload() -> None:
            started.append(name)
            await release.wait()

        return workload

    await dispatcher.dispatch(
        property_channel_id="a", job_id="1", workload=_make("a"),
        intent=_message("a", "1"),
    )
    await dispatcher.dispatch(
        property_channel_id="b", job_id="2", workload=_make("b"),
        intent=_message("b", "2"),
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert sorted(started) == ["a", "b"]
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
