"""Lifespan wiring for the NarrativeService (Gap #2).

The narrative service composes a per-property / per-customer
timeline from every adapter whose backing store is live.  In v1
the LLM renderer stays optional so the endpoint works without an
Azure OpenAI deployment; the voice renderer is wired only when
ElevenLabs is live; the ownership resolver is wired only when the
DecisionCase store is up.  The result is a single
:class:`NarrativeService` object exposed at
``application.state.narrative_service`` and returned for
assignment to the module global in lifespan.

The wire entry point is synchronous because none of the renderers
or composers perform network I/O at construction.
``init_chat_model`` only constructs an SDK-side chat-model
wrapper; the real Azure OpenAI call happens later, on render.

A subtlety preserved verbatim: the original inline section
**re-clears** ``_unified_data_client`` to ``None`` when the
``UnifiedReservationsTimelineSource`` fails to construct, so that
downstream consumers (conversation archive loader, profile
harvester) also disable their unified-gateway path.  The
bootstrap signals this back to the caller through the second
element of the return tuple — the caller assigns it back to the
module global to preserve identical downstream behaviour.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from brain_engine.integrations.unified_data import UnifiedDataGraphQLClient
from brain_engine.integrations.voice.elevenlabs import ElevenLabsClient
from brain_engine.memory.factory import MemorySystem
from brain_engine.models.azure_routing import load_azure_openai_config
from brain_engine.narrative import (
    LLMNarrativeRenderer,
    NarrativeService,
    PropertyOwnershipResolver,
    TextRenderer,
    TimelineComposer,
    UnifiedReservationsTimelineSource,
    VoiceRenderer,
)
from brain_engine.narrative.sources import (
    DecisionCaseTimelineSource,
    GuestHistoryTimelineSource,
)
from brain_engine.patterns.store import DecisionCaseStore
from config.settings import Settings

logger = logging.getLogger(__name__)


def wire(
    application: FastAPI,
    *,
    case_store: DecisionCaseStore | None,
    memory: MemorySystem | None,
    elevenlabs_client: ElevenLabsClient | None,
    unified_data_client: UnifiedDataGraphQLClient | None,
    unified_customer_id: str,
    unified_org_id: str | None,
    unified_provider_type: str | None,
    settings: Settings,
) -> tuple[NarrativeService, UnifiedDataGraphQLClient | None]:
    """Build the NarrativeService and resolve adapter activation.

    On success ``application.state.narrative_service`` is
    populated so that future readers migrated off the module
    global can resolve it through the FastAPI request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed service.
        case_store: DecisionCase store from R2.  When ``None``
            the DecisionCaseTimelineSource and the ownership
            resolver are skipped.
        memory: Cognitive memory system from R8.  When ``None``
            the GuestHistoryTimelineSource is skipped.
        elevenlabs_client: Voice client from R6.  When ``None``
            the voice renderer is skipped.
        unified_data_client: Unified GraphQL client from R10, or
            ``None`` when the gateway is disabled.
        unified_customer_id: Cendra customer id from R10.  Used
            both to gate the unified source and to scope it.
        unified_org_id: Optional org-level scope from R10.
        unified_provider_type: Optional provider-type filter from
            R10.
        settings: The loaded :class:`Settings` providing
            ``llm_model`` for the LLM rewriter; LLM credentials
            come from the AZURE_OPENAI_* env triple.

    Returns:
        A 2-tuple ``(narrative_service, unified_data_client)``:

        * ``narrative_service`` — the assembled service, always
          non-None.  Failure to wire individual adapters degrades
          the service silently rather than aborting startup.
        * ``unified_data_client`` — the (possibly cleared) input
          unified client.  When the
          ``UnifiedReservationsTimelineSource`` construction
          raises, the value comes back as ``None`` so the caller
          can re-assign the module global, disabling downstream
          consumers (archive loader, profile harvester) — exact
          parity with the original inline section.
    """
    sources: list[Any] = []
    if case_store is not None:
        sources.append(DecisionCaseTimelineSource(case_store))
    if memory is not None:
        sources.append(
            GuestHistoryTimelineSource(memory.guest_history)
        )

    # Unified GraphQL reservations adapter — appended only when
    # the client was wired by R10.  Source-construction error is
    # swallowed; the client reference is cleared so downstream
    # consumers (archive loader, profile harvester) also disable.
    if unified_data_client is not None and unified_customer_id:
        try:
            sources.append(
                UnifiedReservationsTimelineSource(
                    unified_data_client,
                    cendra_customer_id=unified_customer_id,
                    cendra_org_id=unified_org_id,
                    provider_type=unified_provider_type,
                )
            )
            logger.info(
                "UnifiedReservationsTimelineSource wired "
                "(customer=%s, org=%s, provider=%s)",
                unified_customer_id,
                unified_org_id or "—",
                unified_provider_type or "—",
            )
        except Exception as exc:  # noqa: BLE001 — optional adapter
            logger.warning(
                "UnifiedReservationsTimelineSource init skipped: "
                "%s (%s)",
                exc,
                type(exc).__name__,
            )
            # Preserve original behaviour: any failure here
            # disables downstream consumers by clearing the
            # reference.  The caller assigns the returned value
            # back to the module global.
            unified_data_client = None

    voice_renderer: VoiceRenderer | None = (
        VoiceRenderer(elevenlabs_client)
        if elevenlabs_client is not None
        else None
    )

    # LLM rewriter is opt-in per-environment.  Routes exclusively
    # through the tenant's Azure OpenAI deployment when the
    # AZURE_OPENAI_* env triple is complete.  Construction errors
    # (missing SDK, bad model string, no key) are swallowed so the
    # endpoint still responds with a deterministic skeleton.
    llm_renderer: LLMNarrativeRenderer | None = None
    azure_cfg = load_azure_openai_config()
    if azure_cfg.is_complete():
        try:
            from brain_engine.models.factory import init_chat_model

            # Pick the deployment matching the configured model
            # family — `gpt-4o-mini` is the engine-wide default.
            deployment = (
                azure_cfg.chat_mini_deployment
                if "mini" in settings.llm_model.lower()
                else azure_cfg.chat_deployment
            )
            chat_model = init_chat_model(
                f"azure_openai:{deployment}",
                api_key=azure_cfg.api_key,
                azure_endpoint=azure_cfg.endpoint,
                api_version=azure_cfg.api_version,
                temperature=0.3,
            )
            llm_renderer = LLMNarrativeRenderer(chat_model)
        except Exception as exc:  # noqa: BLE001 — optional path
            logger.warning(
                "LLMNarrativeRenderer (Azure) init skipped: %s (%s)",
                exc,
                type(exc).__name__,
            )

    # Ownership resolver lets the timeline endpoint fall back to
    # the last recorded owner when the caller omits customer_id.
    ownership_resolver = (
        PropertyOwnershipResolver(case_store)
        if case_store is not None
        else None
    )

    service = NarrativeService(
        composer=TimelineComposer(sources),
        text_renderer=TextRenderer(),
        voice_renderer=voice_renderer,
        llm_renderer=llm_renderer,
        ownership_resolver=ownership_resolver,
    )
    application.state.narrative_service = service
    logger.info(
        "NarrativeService initialized (sources=%d, voice=%s, "
        "llm=%s, owner=%s)",
        len(sources),
        "yes" if voice_renderer is not None else "no",
        "yes" if llm_renderer is not None else "no",
        "yes" if ownership_resolver is not None else "no",
    )
    return service, unified_data_client
