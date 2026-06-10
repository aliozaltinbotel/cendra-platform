"""Proactive freshness — nightly stale-sweep for primed properties.

Stage 3 Track B.  The reactive consumer (Track A) refreshes a property
the moment a backend ``botel-*-sync`` webhook lands, but a property
that stops emitting webhooks — or whose webhook never reaches the
cascade subscription — would otherwise drift forever.  This sweep is
the backstop: once a night it finds every ``primed`` property whose
last warm predates the freshness TTL and routes a ``stale_refresh``
through the **same** Stage 2 ``bootstrap-intents`` queue the reactive
path and the UI already use.  It introduces no new execution path — a
refresh is just a small-window bootstrap the bootstrap-worker knows how
to run.

Like the reactive consumer, the sweep is a pure *producer*: it needs
only a :class:`PropertyStateStore` to read candidates and a
:class:`BootstrapDispatcher` to enqueue.  No bootstrap pipeline runs in
this process.

Why :func:`request_bootstrap` rather than an explicit ``primed → stale``
flip: :func:`request_bootstrap` already treats a ``primed`` row older
than its ``fresh_window`` as eligible and transitions it straight to
``queued`` (see :mod:`brain_engine.tenants.bootstrap_intent`).  Passing
the TTL as ``fresh_window`` makes that path do the staleness check in a
single transition, which is **crash-safe**: a sweep that dies mid-loop
leaves the unprocessed rows ``primed`` (untouched), so the next nightly
tick simply re-selects them — nothing is ever stranded in an
intermediate ``stale`` state.  The candidate read uses the same
``COALESCE(last_bootstrap_at, updated_at) < cutoff`` rule that
:func:`_is_fresh` enforces, so selection and enqueue never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

from brain_engine.tenants.bootstrap_intent import (
    BootstrapDispatcher,
    BootstrapIntentResult,
    BootstrapWorkload,
    request_bootstrap,
)
from brain_engine.tenants.models import TENANT_SOURCE_SYNC, TenantContext
from brain_engine.tenants.property_state import PropertyState
from brain_engine.tenants.property_state_store import PropertyStateStore

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["StaleSweeper", "SweepResult"]


logger = structlog.get_logger(__name__)


#: Default staleness threshold — a ``primed`` row not re-warmed within
#: this window is refreshed proactively.  Sits well above the reactive
#: 7-day refresh window so a property the webhook path keeps fresh is
#: never swept.  Tunable through ``FRESHNESS_STALE_TTL_DAYS``.
_DEFAULT_TTL: Final[timedelta] = timedelta(days=14)

#: Hard cap on refreshes enqueued per run, so an initial backlog cannot
#: flood the single bootstrap-worker in one night.  The remainder ages
#: out on the next tick.  Tunable through ``FRESHNESS_STALE_SWEEP_LIMIT``.
_DEFAULT_LIMIT: Final[int] = 100

#: Delta look-back for each refresh bootstrap.  Defaults to the TTL so
#: the pull covers the whole staleness gap; tunable through
#: ``FRESHNESS_STALE_REFRESH_WINDOW_DAYS``.
_DEFAULT_REFRESH_WINDOW_DAYS: Final[int] = 14

#: Observability tag for a TTL-driven proactive refresh.
_REFRESH_REASON: Final[str] = "stale_refresh"


@dataclass(frozen=True, slots=True)
class SweepResult:
    """Tally of one :meth:`StaleSweeper.sweep_once` run.

    Attributes:
        candidates: Primed rows the store returned as stale by TTL.
        enqueued: Refresh intents actually dispatched to the queue.
        skipped: Candidates not enqueued — a per-row dispatch error,
            or a dedup short-circuit (already in flight, re-primed by
            a concurrent reactive refresh), or every candidate in a
            dry run.
        dry_run: True when the run only logged candidates without
            enqueuing.
    """

    candidates: int
    enqueued: int
    skipped: int
    dry_run: bool


class StaleSweeper:
    """Enqueue proactive refreshes for primed-but-stale properties."""

    def __init__(
        self,
        state_store: PropertyStateStore,
        dispatcher: BootstrapDispatcher,
        *,
        ttl: timedelta = _DEFAULT_TTL,
        limit: int = _DEFAULT_LIMIT,
        window_days: int = _DEFAULT_REFRESH_WINDOW_DAYS,
        dry_run: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._state_store = state_store
        self._dispatcher = dispatcher
        self._ttl = ttl
        self._limit = max(1, limit)
        self._window_days = max(1, window_days)
        self._dry_run = dry_run
        self._clock = clock or _utcnow

    async def sweep_once(self) -> SweepResult:
        """Run a single sweep.  Never raises — logs and tallies.

        A failure reading candidates is swallowed (the nightly backstop
        must not crash-loop the CronJob); a per-row dispatch failure is
        counted as ``skipped`` and the sweep continues, since one bad
        tenant must not strand the rest of the batch.
        """

        now = self._clock()
        cutoff = now - self._ttl
        try:
            candidates = await self._state_store.list_stale_candidates(
                cutoff=cutoff,
                limit=self._limit,
            )
        except Exception as exc:
            logger.warning(
                "stale_sweeper.list_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return SweepResult(
                candidates=0, enqueued=0, skipped=0, dry_run=self._dry_run,
            )

        if self._dry_run:
            logger.info(
                "stale_sweeper.dry_run",
                candidates=len(candidates),
                cutoff=cutoff.isoformat(),
                property_channel_ids=[
                    row.property_channel_id for row in candidates[:20]
                ],
            )
            return SweepResult(
                candidates=len(candidates),
                enqueued=0,
                skipped=len(candidates),
                dry_run=True,
            )

        enqueued = 0
        skipped = 0
        for row in candidates:
            try:
                result = await self._refresh(row, now=now)
            except Exception as exc:
                skipped += 1
                logger.warning(
                    "stale_sweeper.refresh_failed",
                    property_channel_id=row.property_channel_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue
            if result.enqueued:
                enqueued += 1
            else:
                skipped += 1

        logger.info(
            "stale_sweeper.swept",
            candidates=len(candidates),
            enqueued=enqueued,
            skipped=skipped,
            cutoff=cutoff.isoformat(),
        )
        return SweepResult(
            candidates=len(candidates),
            enqueued=enqueued,
            skipped=skipped,
            dry_run=False,
        )

    async def _refresh(
        self,
        row: PropertyState,
        *,
        now: datetime,
    ) -> BootstrapIntentResult:
        """Route one candidate through the shared enqueue + dedup path.

        The tenant tuple is reconstructed from the row itself — the
        ``property_state`` SSoT already carries ``customer_id`` /
        ``org_id`` / ``provider_type`` from the original bootstrap, so
        the sweep needs no registry lookup.  ``fresh_window`` is the
        TTL, so :func:`request_bootstrap` re-confirms staleness against
        the live row before enqueuing (a candidate a concurrent
        reactive refresh re-primed in the meantime short-circuits as
        ``primed_fresh``).
        """

        tenant = TenantContext(
            customer_id=row.customer_id,
            org_id=row.org_id,
            provider_type=row.provider_type,
            property_channel_id=row.property_channel_id,
            source=TENANT_SOURCE_SYNC,
        )
        return await request_bootstrap(
            property_channel_id=row.property_channel_id,
            tenant=tenant,
            window_days=self._window_days,
            reason=_REFRESH_REASON,
            state_store=self._state_store,
            dispatcher=self._dispatcher,
            workload_factory=_noop_workload_factory(),
            fresh_window=self._ttl,
            now=now,
        )


def _noop_workload_factory() -> (
    Callable[[PropertyState, str], BootstrapWorkload]
):
    """A workload the Service Bus dispatcher discards (work runs remote).

    Mirrors the reactive consumer: the heavy ``bootstrap_fast`` runs in
    the out-of-process bootstrap-worker after it drains the queued
    intent, so the producer hands the dispatcher a coroutine that is
    never executed (the Service Bus dispatcher serialises the ``intent``
    and drops the ``workload``).
    """

    def factory(_state: PropertyState, _job_id: str) -> BootstrapWorkload:
        async def _noop() -> None:
            return None

        return _noop

    return factory


def _utcnow() -> datetime:
    return datetime.now(UTC)
