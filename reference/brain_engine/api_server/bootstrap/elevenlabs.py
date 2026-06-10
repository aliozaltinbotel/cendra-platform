"""Lifespan wiring for the ElevenLabs voice client.

The client backs every outbound voice path: the sandbox/voice
renderer, the Telegram approval voicer, and the ops endpoints that
emit a spoken brief.  When the API key is missing those readers see
``None`` and the corresponding feature degrades silently — call
endpoints simply return "voice unavailable" rather than the whole
process refusing to start.

The wire entry point is synchronous because
:class:`ElevenLabsClient` only allocates an
:class:`httpx.AsyncClient` at construction (no network I/O until
the first request).  The shutdown contract still lives in
``server.lifespan``: ``await client.close()`` releases the
underlying connection pool there.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from brain_engine.integrations.voice.elevenlabs import ElevenLabsClient
from config.settings import Settings

logger = logging.getLogger(__name__)


def wire(
    application: FastAPI,
    *,
    settings: Settings,
) -> ElevenLabsClient | None:
    """Construct the ElevenLabs client and attach it to app state.

    On success ``application.state.elevenlabs_client`` is populated
    so that future readers migrated off the module global can
    resolve it through the FastAPI request lifecycle.

    When ``settings.elevenlabs_api_key`` is empty the section logs a
    warning and returns ``None`` — voice-backed endpoints handle the
    ``None`` client and surface "voice unavailable" instead of 500.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed client.
        settings: The loaded :class:`Settings` instance providing
            the API key and the voice / model / agent identifiers.

    Returns:
        The :class:`ElevenLabsClient` instance, or ``None`` when the
        API key is missing.  ``client.close()`` must be awaited on
        shutdown to release the underlying httpx pool — that
        teardown stays in ``server.lifespan`` for now.
    """
    if not settings.elevenlabs_api_key:
        logger.warning(
            "ElevenLabs API key not set — call endpoints will be "
            "unavailable.",
        )
        return None

    client = ElevenLabsClient(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice_id,
        model=settings.elevenlabs_model,
        agent_id=settings.elevenlabs_agent_id or None,
    )
    application.state.elevenlabs_client = client
    logger.info(
        "ElevenLabs client initialized (agent_id=%s)",
        settings.elevenlabs_agent_id,
    )
    return client
