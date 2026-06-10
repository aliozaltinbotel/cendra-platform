"""Forgetting-curve archival for :class:`DecisionCase` rows (Sprint 4).

Once a property has been bootstrapped over months, the
``decision_cases`` table grows roughly linearly with conversation
volume.  Most of those rows stop influencing pattern mining within
the first few weeks: either a :class:`PatternRule` already
captured the lesson (and re-mining the same case adds nothing) or
the surrounding context drifted enough that the case is no longer
representative of current PM behaviour.  Carrying that long tail
in the *hot* working set slows down HNSW / GIN scans, inflates
backup size, and makes audit dashboards noisy.

This module implements the **soft-archive** half of the timeline
research §3.4 forgetting curve: nightly job that flips
``archived_at`` on cases that are both *old enough* and *not
referenced by any active rule*.  The companion CLI script
``scripts/archive_stale_cases.py`` exposes the same logic for
one-shot operator-driven runs (mirrors
``cleanup_legacy_no_rationale.py``).

Why soft-archive instead of physical delete:

* Audit trail — :attr:`PatternRule.source_case_ids` retains
  references; the row stays queryable via
  ``include_archived=True`` so historical rule provenance keeps
  resolving.
* Recoverable — if the heuristic mis-fires (e.g. archives a case
  the operator wanted to keep) clearing ``archived_at`` brings
  the row back into the working set.
* No new tables — pure metadata flip on the existing schema; the
  Sprint-4 migration adds a single column + index.

Pure compute on the Python side; no LLM, no extra infra.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import structlog

if TYPE_CHECKING:
    from brain_engine.patterns.store import DecisionCaseStore


logger = structlog.get_logger(__name__)


# Default tunables — operators override via constructor kwargs
# when the working set has different freshness characteristics.
DEFAULT_RETENTION_DAYS: Final[int] = 90
DEFAULT_BATCH_LIMIT: Final[int] = 1000


@dataclass(frozen=True, slots=True)
class ArchivalReport:
    """Aggregate counters returned by :meth:`CaseArchiver.run_nightly`.

    Attributes:
        candidates: How many cases the cutoff selected.
        archived: How many transitioned from active to archived
            (idempotent re-runs see ``archived == 0``).
        cutoff: The ``created_at < cutoff`` boundary applied.
        retention_days: The configured retention window in days.
        batch_limit: The maximum batch size requested from the
            store; useful for diagnosing back-pressure when
            ``candidates == batch_limit``.
    """

    candidates: int
    archived: int
    cutoff: datetime
    retention_days: int
    batch_limit: int


class CaseArchiver:
    """Soft-archive stale :class:`DecisionCase` rows on a schedule.

    The archiver is a thin orchestrator: it computes the cutoff,
    asks the store for candidates, and flips ``archived_at`` per
    candidate.  All persistence + filtering logic lives behind
    :class:`DecisionCaseStore` so the archiver works against any
    compliant store (Postgres in production, in-memory in tests).

    Implements the :class:`brain_engine.scheduler.NightlyRunner`
    Protocol so the existing :class:`NightlyScheduler` can drive
    the archiver directly without composition glue.

    Attributes:
        _store: The :class:`DecisionCaseStore` to operate on.
        _retention_days: Cases older than this many days are
            eligible for archival.  Defaults to 90.
        _batch_limit: Maximum candidates pulled per nightly run.
            Caps work per pass so a single backlog spike cannot
            saturate the database for hours.
        _log: Bound structured logger.
    """

    def __init__(
        self,
        store: DecisionCaseStore,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be >= 1")
        if batch_limit < 1:
            raise ValueError("batch_limit must be >= 1")
        self._store = store
        self._retention_days = int(retention_days)
        self._batch_limit = int(batch_limit)
        self._log = logger.bind(component="case_archiver")

    async def archive_stale_cases(
        self,
        *,
        now: datetime | None = None,
    ) -> ArchivalReport:
        """Run one archival pass and return a structured report.

        Args:
            now: Optional reference timestamp — tests inject a
                frozen value; production omits the kwarg and
                uses :func:`datetime.now`.

        Returns:
            An :class:`ArchivalReport` with the cutoff, candidate
            count, and number of rows actually flipped.
        """
        reference = now or datetime.now(UTC)
        cutoff = reference - timedelta(days=self._retention_days)
        candidates = await self._store.select_archive_candidates(
            cutoff=cutoff, limit=self._batch_limit,
        )
        archived = 0
        for case_id in candidates:
            if await self._store.archive(case_id):
                archived += 1
        report = ArchivalReport(
            candidates=len(candidates),
            archived=archived,
            cutoff=cutoff,
            retention_days=self._retention_days,
            batch_limit=self._batch_limit,
        )
        self._log.info(
            "case_archiver.run",
            candidates=report.candidates,
            archived=report.archived,
            cutoff=report.cutoff.isoformat(),
            retention_days=report.retention_days,
        )
        return report

    async def run_nightly(self) -> dict[str, Any]:
        """Adapt :meth:`archive_stale_cases` to the NightlyRunner shape.

        Returns a JSON-friendly dict so the scheduler's structured
        log line ``nightly_scheduler.nightly_ok`` carries the
        archival counters alongside whatever other runners ran.
        """
        report = await self.archive_stale_cases()
        return {
            "candidates": report.candidates,
            "archived": report.archived,
            "cutoff": report.cutoff.isoformat(),
            "retention_days": report.retention_days,
            "batch_limit": report.batch_limit,
        }
