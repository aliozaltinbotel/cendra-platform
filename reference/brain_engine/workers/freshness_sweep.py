"""Stage 3 nightly stale-sweep entrypoint — proactive freshness backstop.

``python -m workers.freshness_sweep`` is the one-shot process a
Kubernetes CronJob runs once a night.  It finds every ``primed``
property whose last warm predates the freshness TTL and enqueues a
``stale_refresh`` onto the Stage 2 ``bootstrap-intents`` queue, which
the always-on bootstrap-worker drains.  It is a pure producer — no
bootstrap pipeline runs here — so it shares the lightweight dependency
assembly of the reactive freshness consumer
(:func:`workers.freshness_deps.build_stale_sweeper`).

Unlike the worker and consumer (long-running Deployments), this is a
**run-to-completion** process: build deps, sweep once, log the tally,
tear down, exit.  The CronJob schedule is the loop.

Env knobs (all optional, safe defaults):

* ``FRESHNESS_SWEEP_ENABLED`` — ``false`` makes the run a logged no-op,
  a kill-switch that needs no CronJob delete.  Default ``true``.
* ``FRESHNESS_SWEEP_DRY_RUN`` — ``true`` logs the candidate count
  without enqueuing, for a safe first rollout.  Default ``true``.
* ``FRESHNESS_STALE_TTL_DAYS`` — staleness threshold.  Default 14.
* ``FRESHNESS_STALE_SWEEP_LIMIT`` — max refreshes per run.  Default 100.
* ``FRESHNESS_STALE_REFRESH_WINDOW_DAYS`` — delta look-back for each
  refresh bootstrap.  Default = the TTL.
"""

from __future__ import annotations

import asyncio
import os
from typing import Final

import structlog

from workers.freshness_deps import build_stale_sweeper

__all__ = ["main"]


logger = structlog.get_logger(__name__)

_ENABLED_ENV: Final[str] = "FRESHNESS_SWEEP_ENABLED"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _enabled() -> bool:
    """Read the kill-switch from env (default ``True``)."""
    raw = os.environ.get(_ENABLED_ENV, "true").strip().lower()
    return raw in _TRUTHY


async def main() -> None:
    """Build deps, run one sweep, log the tally, and tear down."""

    if not _enabled():
        logger.info("freshness_sweep.disabled")
        return

    logger.info("freshness_sweep.starting")
    handle = await build_stale_sweeper()
    try:
        result = await handle.sweeper.sweep_once()
        logger.info(
            "freshness_sweep.done",
            candidates=result.candidates,
            enqueued=result.enqueued,
            skipped=result.skipped,
            dry_run=result.dry_run,
        )
    finally:
        await handle.aclose()


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    asyncio.run(main())
