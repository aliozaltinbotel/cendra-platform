"""Lifespan wiring for the Voice transcriber and post-Interview glue.

This bootstrap closes out the V2 Q&A surface that R14 began.  R14
extracted the InterviewEngine and its persistence backend; this
section assembles the optional Whisper-backed
:class:`VoiceTranscriber` and binds the Interview / Profile router
dependencies that depend on both engine and transcriber.

The work is bundled here on purpose — three concerns travel as one
unit because they share state and ordering invariants:

* :class:`AzureWhisperTranscriber` is constructed when the
  tenant's AZURE_OPENAI_* triple plus an
  ``AZURE_OPENAI_WHISPER_DEPLOYMENT`` are configured; a missing
  deployment leaves the transcriber at :data:`None` so the
  ``/answer-voice`` endpoint responds 503 while the text endpoints
  stay reachable.  A constructor ``ValueError`` (the only
  documented failure mode of the SDK adapter) is logged and
  downgraded to the same "no transcriber" state.
* :func:`configure_interview_deps` is invoked here because it
  needs **both** the interview engine (R14 output) and the freshly
  built transcriber.  Calling it earlier would leak ``None`` into
  the router; calling it later would leave a window where the
  router is bound but unconfigured.
* :class:`SandboxReadinessService` is a stateless projection over
  the InterviewAnswerStore, and :func:`configure_profile_deps`
  needs the readiness service together with the property-profile
  store and the unanswered-thread store.  Both calls share the
  same dependency dict shape used elsewhere in the lifespan so
  later sections (Profile harvester, Sandbox backend selection)
  can merge into the same router state with no surprises.

The ``wire`` entry point is **synchronous** because none of the
collaborators perform I/O during construction —
:class:`AzureWhisperTranscriber` only stores its API key and
creates a lazy ``httpx.AsyncClient``.  The first network call
happens during the first transcription request, well after
lifespan startup is complete.

The bootstrap returns a 2-tuple ``(voice_transcriber,
voice_transcriber_close)``:

* ``voice_transcriber`` — the active transcriber, or ``None`` when
  Azure Whisper is not configured / the constructor raised
  ``ValueError``.  The caller mirrors this into the legacy
  ``_voice_transcriber`` module global so existing readers stay
  untouched.
* ``voice_transcriber_close`` — the async close handle when a
  transcriber was constructed, otherwise ``None``.  The caller
  threads this back into the lifespan-local
  ``_voice_transcriber_close`` so the existing shutdown branch
  invokes it unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Final

from fastapi import FastAPI

from brain_engine.api.interview_endpoints import (
    configure_interview_deps,
)
from brain_engine.api.profile_endpoints import (
    configure_profile_deps,
)
from brain_engine.interview import (
    AzureWhisperTranscriber,
    InterviewAnswerStore,
    InterviewEngine,
    VoiceTranscriber,
)
from brain_engine.models.azure_routing import load_azure_openai_config
from brain_engine.profiles import PropertyProfileStore
from brain_engine.sandbox import (
    ExampleReplyGenerator,
    SandboxReadinessService,
    UnansweredThreadStore,
)
from brain_engine.smart_engine.voice_message_processor import (
    VoiceMessageProcessor,
)

logger = logging.getLogger(__name__)


# WhatsApp / Telegram inbound voice messages from cleaners + vendors
# (separate concern from the InterviewEngine answer-voice flow above).
# Off by default — VoiceMessageProcessor does its own
# AzureWhisperConfig.is_complete() guard inside _transcribe, so the
# flag here is the outer ring that decides whether to expose the
# slot at all. Future PRs add the actual WhatsApp / Telegram message
# router that consumes it.
_VOICE_PROCESSOR_ENV: Final[str] = "BRAIN_VOICE_PROCESSOR_ENABLED"


def _voice_processor_enabled() -> bool:
    """Whether the inbound voice-message processor is mounted on app.state.

    Default off keeps the slot unset until a deploy explicitly opts
    in.  The processor itself is cheap to construct (no network I/O
    in its constructor), so this gate is cosmetic safety only — it
    prevents downstream code from picking up a slot that no inbound
    handler is yet calling.
    """
    raw = os.environ.get(_VOICE_PROCESSOR_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def wire(
    application: FastAPI,
    *,
    settings: Any,
    interview_engine: InterviewEngine,
    interview_store: InterviewAnswerStore,
    property_profile_store: PropertyProfileStore,
    unanswered_thread_store: UnansweredThreadStore,
    sandbox_generator: ExampleReplyGenerator,
) -> tuple[VoiceTranscriber | None, Callable[[], Awaitable[None]] | None]:
    """Build the Voice transcriber and bind Interview / Profile deps.

    On success ``application.state.voice_transcriber`` and
    ``application.state.sandbox_readiness_service`` are populated
    so future readers migrated off the module globals can resolve
    them through the FastAPI request lifecycle.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed services.
        settings: The app settings object.  Reserved for
            forward-compatibility — Azure-only Whisper wiring
            currently consumes only the AZURE_OPENAI_* environment
            triple plus AZURE_OPENAI_WHISPER_DEPLOYMENT.
        interview_engine: The R14-built InterviewEngine; passed
            verbatim into ``configure_interview_deps``.
        interview_store: The R14-built InterviewAnswerStore; the
            SandboxReadinessService projects over it.
        property_profile_store: The active property-profile store
            (currently the in-memory module default at this point
            in lifespan).
        unanswered_thread_store: The active unanswered-thread
            store (currently the in-memory module default at this
            point in lifespan; the postgres rebind happens later
            in the Sandbox backend selection section).
        sandbox_generator: The active example-reply generator
            (currently the template default at this point in
            lifespan; the LLM rebind happens later).  Passed in so
            the readiness/profile log line below stays a snapshot
            of the *current* state, exactly as the inline section
            behaved.

    Returns:
        A 2-tuple ``(voice_transcriber, voice_transcriber_close)``.
        See the module docstring for the exact contract.
    """
    voice_transcriber: VoiceTranscriber | None = None
    voice_transcriber_close: Callable[[], Awaitable[None]] | None = None

    # Azure OpenAI is the sole transcription backend.  When the
    # AZURE_OPENAI_* triple + AZURE_OPENAI_WHISPER_DEPLOYMENT are
    # set the transcriber is wired in; otherwise /answer-voice
    # responds 503 and the text endpoints remain reachable.
    azure_cfg = load_azure_openai_config()
    azure_whisper_deployment = os.environ.get(
        "AZURE_OPENAI_WHISPER_DEPLOYMENT", "",
    ).strip()
    use_azure_whisper = (
        azure_cfg.is_complete() and bool(azure_whisper_deployment)
    )
    if use_azure_whisper:
        try:
            whisper_client = AzureWhisperTranscriber(
                api_key=azure_cfg.api_key,
                azure_endpoint=azure_cfg.endpoint,
                azure_api_version=azure_cfg.api_version,
                azure_deployment=azure_whisper_deployment,
            )
            voice_transcriber = whisper_client
            voice_transcriber_close = whisper_client.close
            logger.info(
                "VoiceTranscriber backend=azure_whisper deployment=%s",
                azure_whisper_deployment,
            )
        except ValueError as exc:
            logger.warning(
                "VoiceTranscriber init failed: %s — /answer-voice "
                "disabled",
                exc,
            )
    else:
        logger.info(
            "VoiceTranscriber not configured (Azure Whisper "
            "deployment absent) — /answer-voice will respond 503",
        )

    # Bind the Interview router to engine + transcriber together.
    # ``configure_interview_deps`` merges into a shared dict so
    # ``voice_transcriber=None`` is a valid configured state — the
    # /answer-voice handler reads the slot at request time and
    # returns 503 when it is None.
    configure_interview_deps(
        {
            "interview_engine": interview_engine,
            "voice_transcriber": voice_transcriber,
        },
    )
    logger.info("InterviewEngine initialized and wired into router")

    # SandboxReadinessService is a stateless projection over the
    # InterviewAnswerStore — wiring it here is cheap and keeps the
    # router free of global lookups.
    sandbox_readiness_service = SandboxReadinessService(interview_store)
    configure_profile_deps(
        {
            "property_profile_store": property_profile_store,
            "interview_engine": interview_engine,
            "unanswered_thread_store": unanswered_thread_store,
            "sandbox_readiness_service": sandbox_readiness_service,
        },
    )
    logger.info(
        "Property profile + sandbox endpoints wired "
        "(profile_store=%s, sandbox_generator=%s)",
        type(property_profile_store).__name__,
        sandbox_generator.name,
    )

    application.state.voice_transcriber = voice_transcriber
    application.state.sandbox_readiness_service = sandbox_readiness_service

    # Inbound voice-message processor (separate from the answer-voice
    # path above).  When disabled, the slot stays unset so callers
    # that read it via getattr(..., None) fall through to the legacy
    # text-only handler.
    if _voice_processor_enabled():
        application.state.voice_message_processor = VoiceMessageProcessor()
        logger.info(
            "VoiceMessageProcessor wired on app.state.voice_message_processor "
            "(BRAIN_VOICE_PROCESSOR_ENABLED truthy).",
        )
    else:
        logger.info(
            "VoiceMessageProcessor disabled — set "
            "BRAIN_VOICE_PROCESSOR_ENABLED=1 to expose the slot.",
        )

    return voice_transcriber, voice_transcriber_close
