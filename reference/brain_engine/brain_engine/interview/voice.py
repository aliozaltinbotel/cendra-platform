"""Voice-to-text transcription for the interview pipeline.

The PM often prefers to answer a question by recording a voice memo
instead of typing — especially when the answer is long-form policy
text.  This module defines a provider-neutral
:class:`VoiceTranscriber` Protocol plus a reference
:class:`AzureWhisperTranscriber` implementation that calls the
tenant's Azure OpenAI ``audio.transcriptions`` deployment via
``httpx`` (keeping the dependency footprint minimal — no extra
client library required).  Public ``api.openai.com`` is never
called.

The Protocol returns :class:`VoiceTranscript` rather than a bare
string so callers can surface language + duration in the audit log
without re-invoking the provider.

The transcriber is optional — when not configured, the
``/answer-voice`` endpoint responds 503, and the text endpoint
remains the only way to record an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

import httpx
import structlog


__all__ = [
    "AzureWhisperTranscriber",
    "VoiceTranscriber",
    "VoiceTranscript",
    "VoiceTranscriptionError",
]


logger = structlog.get_logger(__name__)


_DEFAULT_TIMEOUT_SECONDS: Final[float] = 60.0


class VoiceTranscriptionError(RuntimeError):
    """Raised when a transcription provider rejects the request."""


@dataclass(frozen=True, slots=True)
class VoiceTranscript:
    """Output of a transcription call.

    Attributes:
        text: Plain-text transcription suitable for
            :meth:`InterviewEngine.record_answer`.
        language: Detected language code (ISO-639-1) when the
            provider reports it; empty string otherwise.
        duration_seconds: Audio duration reported by the provider;
            ``0.0`` when not reported.
    """

    text: str
    language: str = ""
    duration_seconds: float = 0.0


@runtime_checkable
class VoiceTranscriber(Protocol):
    """Abstract STT contract used by the interview endpoints."""

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> VoiceTranscript:
        """Transcribe ``audio_bytes`` and return the text + metadata."""
        ...


class AzureWhisperTranscriber:
    """Whisper-backed :class:`VoiceTranscriber` implementation.

    Routes exclusively through the tenant's Azure OpenAI Whisper
    deployment at ``{endpoint}/openai/deployments/{deployment}/
    audio/transcriptions?api-version=...`` with the ``api-key``
    header.  Public ``api.openai.com`` is never called.

    The client is stateful only in that it owns an ``httpx.AsyncClient``
    instance; call :meth:`close` during shutdown to release it.
    """

    def __init__(
        self,
        *,
        azure_endpoint: str,
        azure_api_version: str,
        azure_deployment: str,
        api_key: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be non-empty")
        if not (azure_endpoint and azure_api_version and azure_deployment):
            raise ValueError(
                "azure_endpoint, azure_api_version and azure_deployment "
                "are required",
            )
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._azure_endpoint = azure_endpoint.rstrip("/")
        self._azure_api_version = azure_api_version
        self._azure_deployment = azure_deployment
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout_seconds,
        )
        self._log = logger.bind(component="whisper_transcriber")

    async def close(self) -> None:
        """Release the underlying ``httpx`` client when owned."""
        if self._owns_client:
            await self._client.aclose()
            self._log.info("http_client_closed")

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> VoiceTranscript:
        """POST ``audio_bytes`` to the Whisper endpoint.

        Raises:
            VoiceTranscriptionError: on non-2xx responses or when the
                provider returns a body we cannot parse.
        """
        if not audio_bytes:
            raise ValueError("audio_bytes must be non-empty")
        url = (
            f"{self._azure_endpoint}/openai/deployments/"
            f"{self._azure_deployment}/audio/transcriptions"
            f"?api-version={self._azure_api_version}"
        )
        headers = {"api-key": self._api_key}
        files = {"file": (filename, audio_bytes, content_type)}
        data = {"response_format": "verbose_json"}
        try:
            response = await self._client.post(
                url,
                headers=headers,
                files=files,
                data=data,
            )
        except httpx.HTTPError as exc:
            raise VoiceTranscriptionError(
                f"transport error: {exc}",
            ) from exc
        if response.status_code >= 400:
            raise VoiceTranscriptionError(
                f"provider error {response.status_code}: "
                f"{response.text[:200]}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise VoiceTranscriptionError(
                "provider returned non-JSON response",
            ) from exc
        text = str(payload.get("text", "")).strip()
        if not text:
            raise VoiceTranscriptionError(
                "provider returned an empty transcript",
            )
        language = str(payload.get("language", "") or "")
        duration_raw = payload.get("duration", 0) or 0
        try:
            duration = float(duration_raw)
        except (TypeError, ValueError):
            duration = 0.0
        self._log.info(
            "transcribed",
            language=language,
            duration_seconds=duration,
            text_length=len(text),
        )
        return VoiceTranscript(
            text=text,
            language=language,
            duration_seconds=duration,
        )
