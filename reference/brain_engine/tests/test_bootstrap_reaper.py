"""Tests for the orphan-recovery reaper.

Covers the store contract (:meth:`PropertyStateStore.reap_orphaned`)
and the :class:`BootstrapReaper` that drives it:

* only stale ``queued`` / ``warming`` rows are flipped to ``failed``
  (with ``retry_count`` bumped, ``current_job_id`` cleared);
* fresh in-flight rows and terminal rows are left untouched;
* the reaper does an initial sweep on ``run_forever`` and is
  cancellable;
* a store error during a sweep is swallowed (the backstop must never
  crash the serving loop).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from brain_engine.tenants import (
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_WARMING,
    BootstrapReaper,
    InMemoryPropertyStateStore,
    PropertyState,
)

_NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _row(
    cid: str,
    status: str,
    updated_at: datetime,
    *,
    retry_count: int = 0,
) -> PropertyState:
    return PropertyState(
        property_channel_id=cid,
        customer_id="cust",
        provider_type="HOSTAWAY",
        status=status,
        current_job_id="job-x",
        updated_at=updated_at,
        retry_count=retry_count,
    )


async def test_reap_orphaned_flips_only_stale_inflight() -> None:
    store = InMemoryPropertyStateStore()
    old = _NOW - timedelta(minutes=30)
    fresh = _NOW - timedelta(minutes=1)
    await store.create_if_absent(_row("stale-warm", PROPERTY_STATUS_WARMING, old))
    await store.create_if_absent(_row("fresh-warm", PROPERTY_STATUS_WARMING, fresh))
    await store.create_if_absent(
        _row("stale-queued", PROPERTY_STATUS_QUEUED, old),
    )
    await store.create_if_absent(_row("primed", PROPERTY_STATUS_PRIMED, old))

    cutoff = _NOW - timedelta(minutes=25)
    reaped = await store.reap_orphaned(cutoff=cutoff, now=_NOW)

    assert sorted(reaped) == ["stale-queued", "stale-warm"]
    sw = await store.get("stale-warm")
    assert sw is not None
    assert sw.status == PROPERTY_STATUS_FAILED
    assert sw.retry_count == 1
    assert sw.current_job_id is None
    assert "reaped" in (sw.last_error or "")
    assert sw.updated_at == _NOW
    # Fresh in-flight + terminal rows untouched.
    fresh_row = await store.get("fresh-warm")
    primed_row = await store.get("primed")
    assert fresh_row is not None and fresh_row.status == PROPERTY_STATUS_WARMING
    assert primed_row is not None and primed_row.status == PROPERTY_STATUS_PRIMED


async def test_reaper_reap_once_uses_timeout_and_clock() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(
        _row("orphan", PROPERTY_STATUS_WARMING, _NOW - timedelta(minutes=40)),
    )
    reaper = BootstrapReaper(
        store,
        orphan_timeout=timedelta(minutes=25),
        clock=lambda: _NOW,
    )

    reaped = await reaper.reap_once()

    assert reaped == ["orphan"]
    row = await store.get("orphan")
    assert row is not None and row.status == PROPERTY_STATUS_FAILED


async def test_reaper_reap_once_swallows_store_errors() -> None:
    class _BoomStore:
        async def reap_orphaned(
            self, *, cutoff: datetime, now: datetime | None = None,
        ) -> list[str]:
            raise RuntimeError("boom")

    reaper = BootstrapReaper(_BoomStore())  # type: ignore[arg-type]
    assert await reaper.reap_once() == []


async def test_run_forever_does_initial_sweep_then_cancellable() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(
        _row("orphan", PROPERTY_STATUS_WARMING, _NOW - timedelta(minutes=40)),
    )
    reaper = BootstrapReaper(
        store,
        orphan_timeout=timedelta(minutes=25),
        interval_seconds=999.0,
        clock=lambda: _NOW,
    )

    task = asyncio.create_task(reaper.run_forever())
    for _ in range(5):
        await asyncio.sleep(0)

    # Initial sweep already recovered the orphan.
    row = await store.get("orphan")
    assert row is not None and row.status == PROPERTY_STATUS_FAILED

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
