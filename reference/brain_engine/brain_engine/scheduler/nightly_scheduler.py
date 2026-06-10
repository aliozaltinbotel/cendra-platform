"""Background scheduler for nightly and monthly consolidation cycles.

Fix #5 — the continual-learning loop has always owned the *logic* for
nightly consolidation and monthly evaluation, but the trigger side was
missing: both coroutines had to be invoked manually via HTTP.  This
module fills the gap by wrapping :class:`AsyncIOScheduler` in a thin
Protocol-shaped façade that can be started and stopped as a FastAPI
lifespan singleton.

The scheduler keeps two jobs:

- ``nightly_consolidation`` — runs :meth:`NightlyConsolidator.run_nightly`
  every day at the UTC hour supplied at construction time.
- ``monthly_evaluation`` — runs :meth:`MonthlyEvaluator.evaluate` on the
  first day of every month at the same UTC hour.

Both jobs are `coalesce=True` with `max_instances=1`, so a missed run
(pod restart) does not pile up executions; the next window simply
catches up once.  Any exception raised by the job is logged and
swallowed — a broken cycle must never take the API down.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

__all__ = [
    "MonthlyRunner",
    "NightlyRunner",
    "NightlyScheduler",
]


logger = structlog.get_logger(__name__)


_NIGHTLY_JOB_ID = "brain.nightly_consolidation"
_MONTHLY_JOB_ID = "brain.monthly_evaluation"


@runtime_checkable
class NightlyRunner(Protocol):
    """Target of the daily job — typically a :class:`NightlyConsolidator`."""

    async def run_nightly(self) -> dict[str, Any]:
        ...


@runtime_checkable
class MonthlyRunner(Protocol):
    """Target of the monthly job — typically a :class:`MonthlyEvaluator`."""

    async def evaluate(self, days: int = 30) -> Any:
        ...


class NightlyScheduler:
    """Owns an :class:`AsyncIOScheduler` and two recurring brain jobs.

    The class does not *do* consolidation or evaluation — it only
    arranges for the supplied runners to be called on a schedule.  All
    domain logic lives in ``brain_engine.continual_learning``.
    """

    def __init__(
        self,
        *,
        nightly: NightlyRunner,
        monthly: MonthlyRunner | None = None,
        hour: int = 3,
        timezone: str = "UTC",
    ) -> None:
        if not 0 <= hour <= 23:
            raise ValueError(f"hour must be in [0, 23], got {hour}")
        self._nightly = nightly
        self._monthly = monthly
        self._hour = hour
        self._timezone = timezone
        self._scheduler = AsyncIOScheduler(timezone=timezone)
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler

    def start(self) -> None:
        """Register both jobs and start the underlying scheduler."""
        if self._started:
            return
        self._scheduler.add_job(
            self._run_nightly_safe,
            trigger=CronTrigger(hour=self._hour, minute=0, timezone=self._timezone),
            id=_NIGHTLY_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        if self._monthly is not None:
            self._scheduler.add_job(
                self._run_monthly_safe,
                trigger=CronTrigger(
                    day=1,
                    hour=self._hour,
                    minute=0,
                    timezone=self._timezone,
                ),
                id=_MONTHLY_JOB_ID,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        self._scheduler.start()
        self._started = True
        logger.info(
            "nightly_scheduler.started",
            hour=self._hour,
            timezone=self._timezone,
            monthly=self._monthly is not None,
        )

    def shutdown(self, *, wait: bool = False) -> None:
        """Stop the scheduler.  Safe to call multiple times."""
        if not self._started:
            return
        self._scheduler.shutdown(wait=wait)
        self._started = False
        logger.info("nightly_scheduler.stopped")

    # ------------------------------------------------------------------ #
    # Internal wrappers — swallow exceptions so a bad run does not kill  #
    # the scheduler thread or the API.                                   #
    # ------------------------------------------------------------------ #

    async def _run_nightly_safe(self) -> None:
        try:
            stats = await self._nightly.run_nightly()
        except Exception as exc:  # noqa: BLE001 - logged and contained
            logger.error(
                "nightly_scheduler.nightly_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        logger.info("nightly_scheduler.nightly_ok", stats=_summary(stats))

    async def _run_monthly_safe(self) -> None:
        if self._monthly is None:
            return
        try:
            report = await self._monthly.evaluate()
        except Exception as exc:  # noqa: BLE001 - logged and contained
            logger.error(
                "nightly_scheduler.monthly_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        logger.info("nightly_scheduler.monthly_ok", report=str(type(report).__name__))


def _summary(stats: Any) -> dict[str, Any]:
    """Return a short JSON-friendly preview of the nightly stats."""
    if isinstance(stats, dict):
        return {k: type(v).__name__ for k, v in stats.items()}
    return {"type": type(stats).__name__}
