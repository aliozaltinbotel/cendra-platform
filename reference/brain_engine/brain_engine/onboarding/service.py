"""Historical onboarding orchestrator.

Given a batch of property IDs, :class:`OnboardingService` replays
archived conversations through the learning pipeline so the engine
does not start with a cold DecisionCase / PatternRule cache.  The
service owns the traversal loop and the persistence decision; the
loader and the extractor stay pure.

Error strategy:

- A loader failure for one property aborts only that property (no
  data for it) and is surfaced in :attr:`PropertyReport.error`.
- An extractor failure for one conversation is logged and counted as
  ``skipped`` so a single bad row cannot kill the whole bootstrap.
- All per-property errors are mirrored into
  :attr:`OnboardingReport.errors` so a single top-level scan tells the
  operator whether the run was clean.

``dry_run=True`` still loads and extracts but skips persistence, so a
caller can preview the bootstrap volume before committing.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Protocol

import structlog

from brain_engine.onboarding.conversation_archive import (
    ConversationArchiveLoader,
)
from brain_engine.onboarding.errors import (
    ConversationArchiveError,
    HistoricalExtractionError,
)
from brain_engine.onboarding.historical_case_extractor import (
    HistoricalCaseExtractor,
)
from brain_engine.onboarding.models import (
    OnboardingReport,
    OnboardingRequest,
    PropertyReport,
)

__all__ = ["OnboardingService"]


logger = structlog.get_logger(__name__)


class _CaseStoreLike(Protocol):
    """Narrow slice of :class:`DecisionCaseStore` used by bootstrap."""

    async def store(self, case: object) -> str:
        ...


_MIN_DAYS: Final[int] = 1
_MAX_DAYS: Final[int] = 730
_DEFAULT_MAX_CONCURRENCY: Final[int] = 4


class OnboardingService:
    """Replay archived conversations into the DecisionCase store.

    Properties are bootstrapped concurrently with a bounded semaphore
    so a batch request does not wait for sequential per-property
    completion.  ``max_concurrency`` caps the in-flight fan-out;
    ``asyncio.gather`` preserves input order so the resulting
    ``property_reports`` line up with ``request.property_ids``.
    """

    def __init__(
        self,
        *,
        archive_loader: ConversationArchiveLoader,
        case_extractor: HistoricalCaseExtractor,
        case_store: _CaseStoreLike,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
        memory_fanout: Any = None,
    ) -> None:
        from brain_engine.memory.fanout import NullMemoryFanOut

        self._loader = archive_loader
        self._extractor = case_extractor
        self._case_store = case_store
        self._max_concurrency = max(1, int(max_concurrency))
        # Mümin 2026-05-13 (PR #F): V1 onboarding shares the
        # same fan-out as bootstrap / live so the timeline /
        # semantic / KG surfaces never miss a write path.
        self._memory_fanout = memory_fanout or NullMemoryFanOut()
        self._log = logger.bind(component="onboarding_service")

    async def bootstrap(
        self,
        request: OnboardingRequest,
    ) -> OnboardingReport:
        """Execute the bootstrap and return an aggregate report."""
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        since, until = self._window(days=request.days, now=now)

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _run(property_id: str) -> PropertyReport:
            async with semaphore:
                return await self._bootstrap_property(
                    property_id=property_id,
                    since=since,
                    until=until,
                    limit=request.limit_per_property,
                    dry_run=request.dry_run,
                )

        reports = await asyncio.gather(
            *(_run(pid) for pid in request.property_ids)
        )

        errors = tuple(
            f"{r.property_id}: {r.error}" for r in reports if r.error
        )
        return OnboardingReport(
            property_reports=tuple(reports),
            total_conversations=sum(r.conversations_loaded for r in reports),
            total_cases=sum(r.cases_extracted for r in reports),
            total_skipped=sum(r.skipped for r in reports),
            duration_seconds=round(time.monotonic() - started, 3),
            dry_run=request.dry_run,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Per-property loop
    # ------------------------------------------------------------------

    async def _bootstrap_property(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        limit: int,
        dry_run: bool,
    ) -> PropertyReport:
        conversations_loaded = 0
        cases_extracted = 0
        skipped = 0

        try:
            iterator = self._loader.load(
                property_id=property_id,
                since=since,
                until=until,
                limit=limit,
            )
            async for conversation in iterator:
                conversations_loaded += 1
                try:
                    case = await self._extractor.extract(conversation)
                except HistoricalExtractionError as exc:
                    skipped += 1
                    self._log.warning(
                        "onboarding.extract_failed",
                        property_id=property_id,
                        conversation_id=exc.conversation_id,
                        reason=str(exc),
                    )
                    continue
                if case is None:
                    skipped += 1
                    continue
                if not dry_run:
                    await self._case_store.store(case)
                    await self._memory_fanout.record_case(
                        case, source="onboarding_v1",
                    )
                cases_extracted += 1
        except ConversationArchiveError as exc:
            self._log.error(
                "onboarding.archive_failed",
                property_id=property_id,
                loader=exc.loader,
                reason=str(exc),
            )
            return PropertyReport(
                property_id=property_id,
                conversations_loaded=conversations_loaded,
                cases_extracted=cases_extracted,
                skipped=skipped,
                error=str(exc),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contained per property
            self._log.exception(
                "onboarding.unexpected_failure",
                property_id=property_id,
            )
            return PropertyReport(
                property_id=property_id,
                conversations_loaded=conversations_loaded,
                cases_extracted=cases_extracted,
                skipped=skipped,
                error=str(exc) or exc.__class__.__name__,
            )

        return PropertyReport(
            property_id=property_id,
            conversations_loaded=conversations_loaded,
            cases_extracted=cases_extracted,
            skipped=skipped,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _window(*, days: int, now: datetime) -> tuple[datetime, datetime]:
        clamped = max(_MIN_DAYS, min(_MAX_DAYS, days))
        return now - timedelta(days=clamped), now
