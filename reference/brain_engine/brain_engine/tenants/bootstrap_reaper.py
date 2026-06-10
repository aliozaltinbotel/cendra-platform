"""Background recovery for orphaned ``property_state`` bootstraps.

A row left in ``queued`` / ``warming`` by a pod that was killed (or a
worker whose event loop wedged) blocks every future intent for that
property through the in-flight dedup in :func:`request_bootstrap`.
The reaper sweeps such rows back to ``failed`` so they can be
re-attempted (the adopt-existing probe then re-primes them cheaply if
a profile already exists).

Stage 1 runs it in-process: a **startup sweep** — which recovers
restart orphans, the common case — plus a **periodic loop** that
catches in-pod stalls the per-run timeout missed.  Like every Stage 1
background task it shares the serving event loop, so a fully wedged
loop stops the reaper too; the durable guarantee arrives with the
out-of-process worker (Stage 2).  The startup sweep alone already
recovers the killed-pod case because a fresh pod's loop is healthy.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from brain_engine.tenants.property_state_store import PropertyStateStore

__all__ = ["BootstrapReaper"]


logger = structlog.get_logger(__name__)


#: A row not updated within this window while still ``queued`` /
#: ``warming`` is treated as orphaned.  Sits above the runner's 20 min
#: per-run timeout so a genuinely-slow-but-live run is never reaped
#: out from under itself.
_DEFAULT_ORPHAN_TIMEOUT: Final[timedelta] = timedelta(minutes=25)

#: Gap between periodic sweeps.  Coarse on purpose — the reaper is a
#: backstop, not a latency-sensitive path.
_DEFAULT_INTERVAL_SECONDS: Final[float] = 300.0


class BootstrapReaper:
    """Sweep orphaned bootstrap rows back to ``failed``."""

    def __init__(
        self,
        state_store: PropertyStateStore,
        *,
        orphan_timeout: timedelta = _DEFAULT_ORPHAN_TIMEOUT,
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._state_store = state_store
        self._orphan_timeout = orphan_timeout
        self._interval = max(1.0, interval_seconds)
        self._clock = clock or _utcnow

    async def reap_once(self) -> list[str]:
        """Run a single sweep.  Never raises — logs and returns ``[]``."""
        now = self._clock()
        cutoff = now - self._orphan_timeout
        try:
            reaped = await self._state_store.reap_orphaned(
                cutoff=cutoff, now=now,
            )
        except Exception as exc:
            logger.warning(
                "bootstrap_reaper.sweep_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        if reaped:
            logger.info(
                "bootstrap_reaper.reaped",
                count=len(reaped),
                property_channel_ids=reaped[:20],
            )
        return reaped

    async def run_forever(self) -> None:
        """Sweep once immediately, then on every ``interval`` tick.

        Cancellation (lifespan shutdown) propagates out of the sleep
        so the task ends promptly.
        """
        await self.reap_once()
        while True:
            await asyncio.sleep(self._interval)
            await self.reap_once()


def _utcnow() -> datetime:
    return datetime.now(UTC)
