"""Lifespan wiring for the background reasoning surface.

Two adjacent lifespan sections share the "background reasoning"
domain and are bundled here:

* :class:`CausalNavigationService` (Gap #3 — temporal causal
  links).  Reuses the :class:`NarrativeService` composer to fetch
  the property's events inside a window, then runs the
  :class:`CausalGraphBuilder`'s rule suite to produce a directed
  graph plus optional ancestor / descendant walks.  Read by the
  ``/api/memory/property/{property_id}/causal`` endpoint.  The
  service is wired only when a narrative service is live; a
  missing narrative service leaves the slot at ``None`` so the
  endpoint can respond 503 without crashing.
* :class:`NightlyScheduler` (Fix #5 — automate continual
  learning cycles).  Owns an :class:`AsyncIOScheduler` that
  registers two recurring brain jobs: the daily consolidator and
  the monthly evaluator.  The scheduler swallows and logs
  exceptions from each run so a bad job cannot take the API
  down.

Bundling these two concerns in one bootstrap follows the §17
guideline of grouping by domain coherence: both services run
*offline* over the events the engine has already absorbed, share
the FullSystem dependency for their job runners, and are read by
the same family of memory / causal endpoints.

The ``wire`` entry point is **synchronous** because none of the
collaborators perform I/O during construction —
:meth:`NightlyScheduler.start` registers jobs on the running
event loop but does not await network or disk operations.

The bootstrap returns a 2-tuple
``(causal_service, nightly_scheduler)``:

* ``causal_service`` — the assembled CausalNavigationService, or
  ``None`` when ``narrative_service`` is missing.  The caller
  mirrors this into the legacy ``_causal_service`` module global
  so existing readers stay untouched.
* ``nightly_scheduler`` — the started :class:`NightlyScheduler`.
  The caller mirrors this into ``_nightly_scheduler`` so the
  existing shutdown branch (``shutdown(wait=False)``) keeps
  working unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI

from brain_engine.causal import (
    CausalGraphBuilder,
    CausalNavigationService,
    ResolutionRule,
    SharedEntityRule,
    TemporalProximityRule,
)
from brain_engine.memory.factory import FullSystem
from brain_engine.narrative import NarrativeService
from brain_engine.patterns.case_archiver import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_RETENTION_DAYS,
    CaseArchiver,
)
from brain_engine.scheduler import (
    CompositeNightlyRunner,
    NightlyRunner,
    NightlyScheduler,
)

logger = logging.getLogger(__name__)


# Sprint-4 archival feature flag.  Default ``false`` so deploying
# this commit changes nothing for clusters that have not opted in;
# operators set ``BRAIN_CASE_ARCHIVER_ENABLED=true`` to add the
# nightly archival pass alongside the existing consolidator.
_ARCHIVER_ENV: str = "BRAIN_CASE_ARCHIVER_ENABLED"
_FALSY_ENV: frozenset[str] = frozenset({"false", "0", "no", "off", ""})


def _archiver_enabled() -> bool:
    """Return ``True`` when the operator opted into the archiver."""
    raw = os.environ.get(_ARCHIVER_ENV, "false").strip().lower()
    return raw not in _FALSY_ENV


def wire(
    application: FastAPI,
    *,
    narrative_service: NarrativeService | None,
    full_system: FullSystem,
    settings: Any,
) -> tuple[CausalNavigationService | None, NightlyScheduler]:
    """Build the causal navigation service and the nightly scheduler.

    On success ``application.state.{causal_service,
    nightly_scheduler}`` are populated so future readers migrated
    off the module globals can resolve them through the FastAPI
    request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed services.
        narrative_service: The R11 NarrativeService, or ``None``
            when the narrative bootstrap did not fire.  When
            ``None``, the causal service is left unwired.
        full_system: The Blueprint v5 FullSystem; only its
            ``nightly_consolidator`` and ``monthly_evaluator``
            attributes are consumed here.
        settings: The app settings; only its
            ``nightly_consolidation_hour`` attribute is consumed.

    Returns:
        A 2-tuple ``(causal_service, nightly_scheduler)``.  See
        the module docstring for the exact contract.
    """
    # ── Causal navigation service (Gap #3) ──────────────────────────
    # Builder ships with the three v1 inference rules.  The service
    # depends on the narrative service for event collection, so it
    # is wired only when the narrative service is live.
    causal_service: CausalNavigationService | None = None
    if narrative_service is not None:
        causal_builder = CausalGraphBuilder(
            (
                TemporalProximityRule(),
                ResolutionRule(),
                SharedEntityRule(),
            ),
        )
        causal_service = CausalNavigationService(causal_builder)
        logger.info("CausalNavigationService initialized (rules=3)")
    else:
        logger.info(
            "CausalNavigationService disabled — narrative missing",
        )

    # ── Nightly scheduler (Fix #5) ──────────────────────────────────
    # Boots AsyncIOScheduler on the running event loop and registers
    # the daily consolidator and the monthly evaluator.  The
    # scheduler catches and logs exceptions from each run so a bad
    # job cannot take the API down.
    #
    # Sprint-4 opt-in: when ``BRAIN_CASE_ARCHIVER_ENABLED=true`` AND
    # the lifespan has wired ``application.state.case_store``, wrap
    # the consolidator + a fresh CaseArchiver in a
    # CompositeNightlyRunner so both run on the same nightly tick.
    # If the env flag is on but the case_store is missing we LOG and
    # skip — never crash the boot path on a misconfiguration.
    # Without the env flag the scheduler stays exactly as it was on
    # dev-11267 so this commit ships dark by default.
    nightly_runner: NightlyRunner = full_system.nightly_consolidator
    if _archiver_enabled():
        case_store = getattr(application.state, "case_store", None)
        if case_store is None:
            logger.warning(
                "%s=true but application.state.case_store missing — "
                "case archiver skipped on this boot",
                _ARCHIVER_ENV,
            )
        else:
            case_archiver = CaseArchiver(
                case_store,
                retention_days=int(
                    os.environ.get(
                        "BRAIN_CASE_ARCHIVER_RETENTION_DAYS",
                        str(DEFAULT_RETENTION_DAYS),
                    ),
                ),
                batch_limit=int(
                    os.environ.get(
                        "BRAIN_CASE_ARCHIVER_BATCH_LIMIT",
                        str(DEFAULT_BATCH_LIMIT),
                    ),
                ),
            )
            nightly_runner = CompositeNightlyRunner(
                (full_system.nightly_consolidator, case_archiver),
            )
            logger.info(
                "Case archiver enabled (composite nightly runner) "
                "retention=%s batch_limit=%s",
                case_archiver._retention_days,
                case_archiver._batch_limit,
            )

    nightly_scheduler = NightlyScheduler(
        nightly=nightly_runner,
        monthly=full_system.monthly_evaluator,
        hour=settings.nightly_consolidation_hour,
    )
    nightly_scheduler.start()

    application.state.causal_service = causal_service
    application.state.nightly_scheduler = nightly_scheduler
    return causal_service, nightly_scheduler
