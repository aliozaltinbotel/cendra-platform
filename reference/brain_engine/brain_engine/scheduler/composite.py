"""Composite NightlyRunner — fan out the nightly slot to several runners.

``brain_engine.scheduler.NightlyScheduler`` was designed around a
single :class:`NightlyRunner` (typically the
:class:`NightlyConsolidator`).  Sprint 4 introduces a second
runner — :class:`CaseArchiver` — that wants to run on the same
nightly cadence.  Rather than widen the scheduler's public API
(or duplicate the AsyncIOScheduler boilerplate per runner), the
composite below lets the bootstrap layer hand the scheduler a
single object that fans the nightly tick out to N independent
runners.

Each runner's exception is caught and logged so a failure in
one (e.g. the archiver hitting a transient DB blip) cannot
prevent the others (e.g. the consolidator that drives long-term
learning) from running on the same tick.  The aggregated return
payload merges every runner's stats under a per-runner key so
the scheduler's ``nightly_scheduler.nightly_ok`` log line still
carries useful diagnostics.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

import structlog

from brain_engine.scheduler.nightly_scheduler import NightlyRunner

__all__ = ["CompositeNightlyRunner"]


logger = structlog.get_logger(__name__)


_FAILED_KEY: Final[str] = "_failed"


class CompositeNightlyRunner:
    """Fan one nightly tick out to multiple :class:`NightlyRunner` s.

    Implements the :class:`NightlyRunner` Protocol itself — the
    scheduler treats it like any other single runner.  Each
    underlying runner is awaited sequentially; failures are
    isolated (logged + counted) so a broken runner never blocks
    its siblings.

    Order matters when runners share state: list the consolidator
    *before* the archiver if the consolidator might touch cases
    that should not be archived this tick.

    Attributes:
        _runners: Tuple of :class:`NightlyRunner` instances to
            invoke in order on every nightly tick.
        _names: Pre-computed display names per runner so the
            aggregated payload keys stay stable across instances.
        _log: Bound structured logger.
    """

    def __init__(self, runners: Sequence[NightlyRunner]) -> None:
        if not runners:
            raise ValueError("runners must contain at least one entry")
        self._runners: tuple[NightlyRunner, ...] = tuple(runners)
        self._names: tuple[str, ...] = tuple(
            type(r).__name__ for r in runners
        )
        self._log = logger.bind(component="composite_nightly_runner")

    async def run_nightly(self) -> dict[str, Any]:
        """Invoke every wrapped runner; merge their outputs.

        Returns a dict keyed by the runner's class name.  When a
        runner raises, its slot carries ``{"_failed": True,
        "error": str(exc), "error_type": type_name}`` so the
        scheduler's structured-log summary surfaces the failure
        without aborting the surrounding tick.
        """
        aggregated: dict[str, Any] = {}
        for runner, name in zip(self._runners, self._names, strict=True):
            try:
                stats = await runner.run_nightly()
            except Exception as exc:
                self._log.exception(
                    "composite_runner.failed",
                    runner=name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                aggregated[name] = {
                    _FAILED_KEY: True,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
                continue
            aggregated[name] = stats
        return aggregated
