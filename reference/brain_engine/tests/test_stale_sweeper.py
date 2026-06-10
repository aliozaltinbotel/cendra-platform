"""Tests for the Stage 3 proactive stale-sweep (Track B).

Covers the store contract
(:meth:`PropertyStateStore.list_stale_candidates`), the
:class:`StaleSweeper` that drives it through the shared
:func:`request_bootstrap` enqueue path, and the CronJob entrypoint
kill-switch:

* only ``primed`` rows older than the TTL are surfaced, oldest first,
  capped by ``limit``; a NULL ``last_bootstrap_at`` ages out on
  ``updated_at``; fresh / non-primed rows are excluded;
* the sweep enqueues a ``stale_refresh`` for each candidate (flipping
  it ``primed → queued``) and tallies enqueued vs skipped;
* dry-run logs candidates without enqueuing or mutating state;
* a store read error is swallowed (the nightly backstop never
  crash-loops);
* ``FRESHNESS_SWEEP_ENABLED=false`` makes the entrypoint a no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from brain_engine.tenants import (
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_WARMING,
    BootstrapIntentMessage,
    BootstrapWorkload,
    InMemoryPropertyStateStore,
    PropertyState,
    StaleSweeper,
)

_NOW = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
_TTL = timedelta(days=14)


def _primed(
    cid: str,
    *,
    last_bootstrap_at: datetime | None,
    updated_at: datetime | None = None,
) -> PropertyState:
    return PropertyState(
        property_channel_id=cid,
        customer_id="cust-1",
        org_id="org-1",
        provider_type="HOSTAWAY",
        status=PROPERTY_STATUS_PRIMED,
        last_bootstrap_at=last_bootstrap_at,
        updated_at=updated_at or last_bootstrap_at or _NOW,
    )


class _RecordingDispatcher:
    """Captures dispatched intents without running the workload."""

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


# ── store contract: list_stale_candidates ──────────────────────────


async def test_list_stale_candidates_selects_only_old_primed() -> None:
    store = InMemoryPropertyStateStore()
    old = _NOW - timedelta(days=20)
    fresh = _NOW - timedelta(days=2)
    await store.create_if_absent(_primed("old", last_bootstrap_at=old))
    await store.create_if_absent(_primed("fresh", last_bootstrap_at=fresh))
    # Non-primed rows are never candidates, regardless of age.
    for status in (
        PROPERTY_STATUS_QUEUED,
        PROPERTY_STATUS_WARMING,
        PROPERTY_STATUS_FAILED,
    ):
        await store.create_if_absent(
            PropertyState(
                property_channel_id=f"x-{status}",
                customer_id="cust-1",
                provider_type="HOSTAWAY",
                status=status,
                last_bootstrap_at=old,
                updated_at=old,
            ),
        )

    cutoff = _NOW - _TTL
    candidates = await store.list_stale_candidates(cutoff=cutoff, limit=10)

    assert [c.property_channel_id for c in candidates] == ["old"]


async def test_list_stale_candidates_null_anchor_uses_updated_at() -> None:
    store = InMemoryPropertyStateStore()
    old = _NOW - timedelta(days=30)
    # last_bootstrap_at NULL but updated_at old → still a candidate
    # (must not be stranded by ``NULL < cutoff`` being false).
    await store.create_if_absent(
        _primed("null-anchor", last_bootstrap_at=None, updated_at=old),
    )

    candidates = await store.list_stale_candidates(
        cutoff=_NOW - _TTL, limit=10,
    )

    assert [c.property_channel_id for c in candidates] == ["null-anchor"]


async def test_list_stale_candidates_oldest_first_and_limit() -> None:
    store = InMemoryPropertyStateStore()
    for days, cid in ((40, "oldest"), (30, "middle"), (20, "newest")):
        await store.create_if_absent(
            _primed(cid, last_bootstrap_at=_NOW - timedelta(days=days)),
        )

    candidates = await store.list_stale_candidates(
        cutoff=_NOW - _TTL, limit=2,
    )

    # Oldest-warmed first, capped at the limit.
    assert [c.property_channel_id for c in candidates] == ["oldest", "middle"]


# ── sweeper: sweep_once ─────────────────────────────────────────────


async def test_sweep_once_enqueues_refresh_per_candidate() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(
        _primed("p1", last_bootstrap_at=_NOW - timedelta(days=20)),
    )
    await store.create_if_absent(
        _primed("p2", last_bootstrap_at=_NOW - timedelta(days=18)),
    )
    # Fresh primed → not selected, not enqueued.
    await store.create_if_absent(
        _primed("fresh", last_bootstrap_at=_NOW - timedelta(days=1)),
    )
    dispatcher = _RecordingDispatcher()
    sweeper = StaleSweeper(
        store,
        dispatcher,  # type: ignore[arg-type]
        ttl=_TTL,
        window_days=21,
        clock=lambda: _NOW,
    )

    result = await sweeper.sweep_once()

    assert result.candidates == 2
    assert result.enqueued == 2
    assert result.skipped == 0
    assert not result.dry_run
    # Each candidate flipped primed → queued and carries the refresh tag.
    enqueued_ids = sorted(i.property_channel_id for i in dispatcher.intents)
    assert enqueued_ids == ["p1", "p2"]
    assert all(i.reason == "stale_refresh" for i in dispatcher.intents)
    assert all(i.window_days == 21 for i in dispatcher.intents)
    assert all(i.org_id == "org-1" for i in dispatcher.intents)
    p1 = await store.get("p1")
    fresh = await store.get("fresh")
    assert p1 is not None and p1.status == PROPERTY_STATUS_QUEUED
    assert fresh is not None and fresh.status == PROPERTY_STATUS_PRIMED


async def test_sweep_once_dry_run_does_not_enqueue() -> None:
    store = InMemoryPropertyStateStore()
    await store.create_if_absent(
        _primed("p1", last_bootstrap_at=_NOW - timedelta(days=20)),
    )
    dispatcher = _RecordingDispatcher()
    sweeper = StaleSweeper(
        store,
        dispatcher,  # type: ignore[arg-type]
        ttl=_TTL,
        dry_run=True,
        clock=lambda: _NOW,
    )

    result = await sweeper.sweep_once()

    assert result.candidates == 1
    assert result.enqueued == 0
    assert result.skipped == 1
    assert result.dry_run
    assert dispatcher.intents == []
    # State untouched in a dry run.
    p1 = await store.get("p1")
    assert p1 is not None and p1.status == PROPERTY_STATUS_PRIMED


async def test_sweep_once_respects_limit() -> None:
    store = InMemoryPropertyStateStore()
    for days, cid in ((40, "a"), (30, "b"), (20, "c")):
        await store.create_if_absent(
            _primed(cid, last_bootstrap_at=_NOW - timedelta(days=days)),
        )
    dispatcher = _RecordingDispatcher()
    sweeper = StaleSweeper(
        store,
        dispatcher,  # type: ignore[arg-type]
        ttl=_TTL,
        limit=2,
        clock=lambda: _NOW,
    )

    result = await sweeper.sweep_once()

    assert result.candidates == 2
    assert result.enqueued == 2
    # Oldest two refreshed; the newest ages out next tick.
    assert sorted(i.property_channel_id for i in dispatcher.intents) == [
        "a",
        "b",
    ]


async def test_sweep_once_swallows_store_errors() -> None:
    class _BoomStore:
        async def list_stale_candidates(
            self, *, cutoff: datetime, limit: int,
        ) -> list[PropertyState]:
            raise RuntimeError("boom")

    sweeper = StaleSweeper(
        _BoomStore(),  # type: ignore[arg-type]
        _RecordingDispatcher(),  # type: ignore[arg-type]
        clock=lambda: _NOW,
    )

    result = await sweeper.sweep_once()

    assert result == type(result)(
        candidates=0, enqueued=0, skipped=0, dry_run=False,
    )


# ── entrypoint kill-switch ──────────────────────────────────────────


async def test_entrypoint_disabled_is_noop(monkeypatch) -> None:
    import workers.freshness_sweep as sweep

    monkeypatch.setenv("FRESHNESS_SWEEP_ENABLED", "false")

    def _boom() -> object:
        raise AssertionError("build_stale_sweeper must not be called")

    monkeypatch.setattr(sweep, "build_stale_sweeper", _boom)

    # Returns without touching deps — no Postgres / Service Bus needed.
    await sweep.main()
