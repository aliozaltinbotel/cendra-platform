"""Voice rendering over an ElevenLabs client.

Thin wrapper that turns a narrative string into TTS audio.  The
underlying :class:`ElevenLabsClient` already handles the HTTP details;
this layer only provides a domain-specific error boundary and reserves
a streaming entry point for a later commit.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Final

import structlog

from brain_engine.narrative.errors import VoiceSynthesisUnavailable

__all__ = ["VoiceRenderer"]


logger = structlog.get_logger(__name__)


_DEFAULT_OUTPUT_FORMAT: Final[str] = "mp3_44100_128"
_DEFAULT_CONTENT_TYPE: Final[str] = "audio/mpeg"


class VoiceRenderer:
    """Synthesise a narrative string to audio bytes via ElevenLabs."""

    def __init__(
        self,
        client: Any,
        *,
        voice_id: str | None = None,
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
    ) -> None:
        self._client = client
        self._voice_id = voice_id
        self._output_format = output_format

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return ``(audio_bytes, content_type)`` for ``text``.

        Raises :class:`VoiceSynthesisUnavailable` if the provider fails
        or returns an empty body.
        """
        if not text.strip():
            raise VoiceSynthesisUnavailable("Empty text, nothing to synthesise")
        try:
            result = await self._client.text_to_speech(
                text,
                voice_id=self._voice_id,
                output_format=self._output_format,
            )
        except Exception as exc:  # noqa: BLE001 - wrapped below
            logger.warning(
                "narrative.voice_synthesis_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise VoiceSynthesisUnavailable(
                f"TTS provider error: {exc}"
            ) from exc

        audio = getattr(result, "audio_bytes", b"") or b""
        if not audio:
            raise VoiceSynthesisUnavailable("TTS provider returned empty audio")
        content_type = getattr(result, "content_type", _DEFAULT_CONTENT_TYPE)
        return audio, content_type

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield TTS audio chunks as they arrive from the provider.

        Raises :class:`VoiceSynthesisUnavailable` if the client lacks
        streaming support, the input is empty, or the provider call
        fails.  A failure part-way through a stream re-raises as the
        same typed error so the caller can short the HTTP response.
        """
        if not text.strip():
            raise VoiceSynthesisUnavailable("Empty text, nothing to synthesise")
        stream_fn = getattr(self._client, "text_to_speech_stream", None)
        if stream_fn is None:
            raise VoiceSynthesisUnavailable(
                "TTS client does not support streaming"
            )
        try:
            async for chunk in stream_fn(text, voice_id=self._voice_id):
                if chunk:
                    yield chunk
        except Exception as exc:  # noqa: BLE001 - wrapped below
            logger.warning(
                "narrative.voice_stream_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise VoiceSynthesisUnavailable(
                f"TTS streaming error: {exc}"
            ) from exc

    @property
    def stream_content_type(self) -> str:
        """Content-type for streamed audio chunks."""
        return _DEFAULT_CONTENT_TYPE
