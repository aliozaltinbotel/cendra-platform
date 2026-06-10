"""Lifespan wiring for the V1 onboarding bootstrap surface.

Three adjacent lifespan sections share the V1 onboarding domain
and are bundled here:

* Archive loader selection — picks PMS / GraphQL / Composite
  based on the ``ONBOARDING_ARCHIVE_SOURCE`` env var.  The loader
  is ``None`` when the chosen source is unavailable; downstream
  subsystems then skip wiring with a structured warning instead
  of silently falling back to the wrong backend.
* :class:`OnboardingService` — historical DecisionCase replay.
  Wired only when both an archive loader and the DecisionCase
  store are present; without a loader there is nothing to replay,
  without the store there is nowhere to persist.  A missing
  dependency leaves the slot at ``None`` and the bootstrap
  endpoint responds 503 instead of silently no-oping.
* :class:`PropertyProfileHarvester` — onboarding step 5
  ("what Brain knows" snapshot).  Wired only when the
  onboarding-api GraphQL client is live and the caller has
  configured a customer id.  Without those the bootstrap
  pipeline still runs, just without the knowledge snapshot
  (the knowledge endpoint then serves 404 for every property).

Bundling these three in one bootstrap follows the §17 guideline
of grouping by domain coherence: all three feed the V1
onboarding flow and share the same UnifiedData / PMS
dependencies.

The V2 :class:`OnboardingBootstrapPipeline` (episodes + pattern
mining) is **not** bundled here — it depends on the sandbox
backend swap that happens later in the lifespan.  It stays in
``server.py`` for now and is wired against the locals returned
from this bootstrap.

The ``wire`` entry point is **synchronous** because none of the
three collaborators perform I/O during construction; they all
wrap an already-initialised client.

The bootstrap returns a 3-tuple
``(archive_loader, onboarding_service, profile_harvester)``:

* ``archive_loader`` — ``None`` when GraphQL is unavailable.
  The caller mirrors this into the lifespan-local
  ``_archive_loader`` so the downstream V2 pipeline keeps
  working unchanged.
* ``onboarding_service`` — ``None`` when ``archive_loader`` or
  ``case_store`` is missing.  The caller mirrors this into the
  ``_onboarding_service`` module global so existing readers stay
  untouched.
* ``profile_harvester`` — ``None`` when the unified-data client
  or customer id is missing or construction crashed.  The caller
  mirrors this into ``_profile_harvester`` so the V2 pipeline
  keeps working unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI

from brain_engine.api.profile_endpoints import configure_profile_deps
from brain_engine.integrations.unified_data.readers import (
    UnifiedPropertyReader,
    UnifiedRatePlanReader,
    UnifiedReviewReader,
)
from brain_engine.onboarding import (
    ConversationArchiveLoader,
    GraphQLConversationArchiveLoader,
    HistoricalCaseExtractor,
    OnboardingService,
)
from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.classifier import DecisionClassifier
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.profiles import (
    PropertyProfileHarvester,
    PropertyProfileStore,
)

logger = logging.getLogger(__name__)


def wire(
    application: FastAPI,
    *,
    unified_data_client: Any,
    unified_customer_id: str | None,
    unified_org_id: str | None,
    unified_provider_type: str | None,
    case_store: Any,
    property_profile_store: PropertyProfileStore,
    card_store: Any,
) -> tuple[
    ConversationArchiveLoader | None,
    OnboardingService | None,
    PropertyProfileHarvester | None,
]:
    """Build the archive loader, onboarding service, and harvester.

    On success ``application.state.{archive_loader,
    onboarding_service, profile_harvester}`` are populated so
    future readers migrated off the module globals can resolve
    them through the FastAPI request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed services.
        unified_data_client: The onboarding-api GraphQL client
            constructed by
            ``api_server/bootstrap/unified_data.py``, or ``None``.
        unified_customer_id: The Cendra customer id configured
            on the unified data bootstrap, or ``None``.
        unified_org_id: The Cendra org id, or ``None``.
        unified_provider_type: The provider taxonomy string
            (e.g. ``"BOTEL"``), or ``None``.
        case_store: The DecisionCase store, or ``None``.
        property_profile_store: The property profile store
            (always non-None — defaults to in-memory).
        card_store: The decision-card store wired by
            ``api_server/bootstrap/collab.py``.

    Returns:
        A 3-tuple ``(archive_loader, onboarding_service,
        profile_harvester)``.  See the module docstring for the
        exact contract.
    """
    # ── Archive loader selection — GraphQL-only ─────────────────────
    # Cendra moved the data plane to ES + onboarding-api GraphQL
    # (2026-04-28).  Brain Engine reads historical conversations
    # exclusively from the unified GraphQL gateway; the
    # ``ONBOARDING_ARCHIVE_SOURCE`` env var is honoured for
    # observability but every value collapses to GraphQL — the
    # PMS branch is dead.
    archive_source = (
        os.getenv("ONBOARDING_ARCHIVE_SOURCE", "graphql").strip().lower()
    )
    if archive_source not in {"graphql", "pms", "both"}:
        logger.warning(
            "Unknown ONBOARDING_ARCHIVE_SOURCE=%r — using GraphQL",
            archive_source,
        )
    elif archive_source != "graphql":
        logger.info(
            "ONBOARDING_ARCHIVE_SOURCE=%r is legacy — collapsing to "
            "GraphQL (PMS path retired)",
            archive_source,
        )
    graphql_loader: ConversationArchiveLoader | None = None
    if unified_data_client is not None and unified_customer_id:
        try:
            graphql_loader = GraphQLConversationArchiveLoader(
                unified_data_client,
                cendra_customer_id=unified_customer_id,
                cendra_org_id=unified_org_id,
                provider_type=unified_provider_type,
            )
        except Exception as exc:  # noqa: BLE001 - optional adapter
            logger.warning(
                "GraphQLConversationArchiveLoader init skipped: "
                "%s (%s)",
                exc,
                type(exc).__name__,
            )
            graphql_loader = None
    archive_loader: ConversationArchiveLoader | None = graphql_loader
    logger.info(
        "Archive loader selected (source=graphql, chosen=%s)",
        getattr(archive_loader, "name", None),
    )

    # ── Onboarding service (historical DecisionCase bootstrap) ──────
    onboarding_service: OnboardingService | None = None
    if archive_loader is not None and case_store is not None:
        onboarding_service = OnboardingService(
            archive_loader=archive_loader,
            case_extractor=HistoricalCaseExtractor(
                case_builder=CaseBuilder(FeatureBuilder()),
                classifier=DecisionClassifier(),
            ),
            case_store=case_store,
        )
        logger.info(
            "OnboardingService initialized (loader=%s)",
            getattr(archive_loader, "name", None),
        )
    else:
        logger.warning(
            "OnboardingService disabled "
            "(archive_loader=%s, case_store=%s)",
            getattr(archive_loader, "name", None),
            case_store is not None,
        )

    # ── Property profile harvester (onboarding step 5) ──────────────
    profile_harvester: PropertyProfileHarvester | None = None
    property_reader: UnifiedPropertyReader | None = None
    rate_plan_reader: UnifiedRatePlanReader | None = None
    if unified_data_client is not None and unified_customer_id:
        try:
            property_reader = UnifiedPropertyReader(
                unified_data_client,
                cendra_customer_id=unified_customer_id,
                cendra_org_id=unified_org_id,
                provider_type=unified_provider_type,
            )
            rate_plan_reader = UnifiedRatePlanReader(
                unified_data_client,
                cendra_customer_id=unified_customer_id,
                cendra_org_id=unified_org_id,
                provider_type=unified_provider_type,
            )
            profile_harvester = PropertyProfileHarvester(
                property_reader=property_reader,
                rate_plan_reader=rate_plan_reader,
                review_reader=UnifiedReviewReader(
                    unified_data_client,
                    cendra_customer_id=unified_customer_id,
                    cendra_org_id=unified_org_id,
                    provider_type=unified_provider_type,
                ),
                profile_store=property_profile_store,
                # Optional direct-Elasticsearch overlay, wired earlier by
                # ``bootstrap.elasticsearch.wire``.  ``None`` (flag off /
                # not wired) keeps the GraphQL-only harvest unchanged.
                es_reader=getattr(
                    application.state, "es_property_reader", None,
                ),
            )
            logger.info(
                "PropertyProfileHarvester wired "
                "(customer=%s, org=%s, provider=%s, es=%s)",
                unified_customer_id,
                unified_org_id or "—",
                unified_provider_type or "—",
                getattr(application.state, "es_property_reader", None)
                is not None,
            )
        except Exception as exc:  # noqa: BLE001 - optional adapter
            logger.warning(
                "PropertyProfileHarvester init skipped: %s (%s)",
                exc,
                type(exc).__name__,
            )
            profile_harvester = None
            property_reader = None
            rate_plan_reader = None

    # Late-bind the UnifiedData readers once they are known;
    # ``configure_profile_deps`` merges into the shared dict, so
    # the earlier call from ``api_server/bootstrap/voice.py`` is
    # additive.  When a reader is still ``None`` the corresponding
    # endpoint responds with 503, matching the other optional
    # deps in this router.
    configure_profile_deps(
        {
            "property_reader": property_reader,
            "rate_plan_reader": rate_plan_reader,
            "card_store": card_store,
        }
    )

    application.state.archive_loader = archive_loader
    application.state.onboarding_service = onboarding_service
    application.state.profile_harvester = profile_harvester
    return (
        archive_loader,
        onboarding_service,
        profile_harvester,
    )
