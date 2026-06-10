"""VoiceMessageProcessor — transcribe and process WhatsApp voice messages.

When cleaners/vendors/guests send voice messages instead of text,
this module transcribes the audio using the tenant's Azure OpenAI
Whisper deployment, extracts facts, and feeds them into the
learning loop.  Public ``api.openai.com`` is never called.

Real scenario from Cendra:
    Aynur cleaner sends 1:06 voice message instead of typing.
    CEO has to listen manually.
    Brain Engine should: transcribe → extract facts → act.

Based on: Cendra real operations (March 2026 CEO screenshots).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """Result of voice message transcription.

    Attributes:
        text: Transcribed text.
        language: Detected language code.
        duration_seconds: Audio duration.
        confidence: Transcription confidence (0-1).
        source_type: Where the audio came from (whatsapp, telegram).
        sender_id: Who sent the voice message.
        sender_name: Sender's name.
    """

    text: str
    language: str = ""
    duration_seconds: float = 0.0
    confidence: float = 0.9
    source_type: str = "whatsapp"
    sender_id: str = ""
    sender_name: str = ""


class VoiceMessageProcessor:
    """Processes voice messages from WhatsApp/Telegram.

    Transcribes audio to text via the tenant's Azure OpenAI
    Whisper deployment, then feeds the text through the same
    pipeline as regular text messages.

    Args:
        call_learning_loop: For processing transcribed content.
    """

    def __init__(
        self,
        call_learning_loop: Any = None,
    ) -> None:
        self._learning = call_learning_loop
        self._transcription_count: int = 0

    async def process_voice_message(
        self,
        audio_url: str,
        sender_id: str,
        sender_name: str,
        sender_type: str = "cleaner",
        property_id: str = "",
        source: str = "whatsapp",
    ) -> dict[str, Any]:
        """Full pipeline: download → transcribe → extract → act.

        Args:
            audio_url: URL to the audio file.
            sender_id: Who sent the message.
            sender_name: Sender's name.
            sender_type: Role (cleaner, vendor, guest).
            property_id: Property context.
            source: Channel (whatsapp, telegram).

        Returns:
            Dict with transcription, facts, and actions.
        """
        audio_data = await self._download_audio(audio_url)
        if not audio_data:
            return {"error": "Failed to download audio"}

        transcription = await self._transcribe(
            audio_data, sender_id, sender_name, source,
        )

        actions = await self._process_transcription(
            transcription, sender_type, property_id,
        )

        self._transcription_count += 1
        logger.info(
            "Voice message processed: %s from %s — '%s'",
            source, sender_name, transcription.text[:100],
        )

        return {
            "transcription": transcription.text,
            "language": transcription.language,
            "duration": transcription.duration_seconds,
            "sender": sender_name,
            "facts": actions.get("extracted_facts", []),
            "actions": actions.get("actions", []),
            "patterns": actions.get("patterns", []),
        }

    async def _download_audio(self, url: str) -> bytes | None:
        """Download audio file from URL.

        Args:
            url: Audio file URL.

        Returns:
            Audio bytes or None on failure.
        """
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content
        except Exception as exc:
            logger.error("Audio download failed: %s", exc)
            return None

    async def _transcribe(
        self,
        audio_data: bytes,
        sender_id: str,
        sender_name: str,
        source: str,
    ) -> TranscriptionResult:
        """Transcribe audio using the tenant's Azure Whisper deployment.

        Args:
            audio_data: Raw audio bytes.
            sender_id: Sender identifier.
            sender_name: Sender name.
            source: Audio source channel.

        Returns:
            TranscriptionResult with text and metadata.
        """
        import os

        from brain_engine.models.azure_routing import (
            load_azure_openai_config,
        )

        azure_cfg = load_azure_openai_config()
        whisper_deployment = os.environ.get(
            "AZURE_OPENAI_WHISPER_DEPLOYMENT", "",
        ).strip()
        if not (azure_cfg.is_complete() and whisper_deployment):
            return TranscriptionResult(
                text="[Voice message — Azure Whisper not configured]",
                sender_id=sender_id,
                sender_name=sender_name,
                source_type=source,
            )

        try:
            text, language = await self._call_whisper(audio_data)
            return TranscriptionResult(
                text=text,
                language=language,
                duration_seconds=len(audio_data) / 16000,
                sender_id=sender_id,
                sender_name=sender_name,
                source_type=source,
            )
        except Exception as exc:
            logger.error("Whisper transcription failed: %s", exc)
            return TranscriptionResult(
                text="[Transcription failed]",
                sender_id=sender_id,
                sender_name=sender_name,
                source_type=source,
                confidence=0.0,
            )

    async def _call_whisper(
        self,
        audio_data: bytes,
    ) -> tuple[str, str]:
        """Call Azure OpenAI Whisper deployment for transcription.

        Callers must guard with ``AzureOpenAIConfig.is_complete()``
        and ``AZURE_OPENAI_WHISPER_DEPLOYMENT`` before invoking.

        Args:
            audio_data: Raw audio bytes.

        Returns:
            Tuple of (transcribed_text, detected_language).
        """
        import os

        import httpx

        from brain_engine.models.azure_routing import (
            load_azure_openai_config,
        )

        azure_cfg = load_azure_openai_config()
        whisper_deployment = os.environ["AZURE_OPENAI_WHISPER_DEPLOYMENT"]
        url = (
            f"{azure_cfg.endpoint}/openai/deployments/"
            f"{whisper_deployment}/audio/transcriptions"
            f"?api-version={azure_cfg.api_version}"
        )
        headers = {"api-key": azure_cfg.api_key}

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(temp_path, "rb") as audio_file:
                    response = await client.post(
                        url,
                        headers=headers,
                        files={"file": ("audio.ogg", audio_file, "audio/ogg")},
                        data={
                            "model": "whisper-1",
                            "response_format": "verbose_json",
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    return data.get("text", ""), data.get("language", "")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    async def _process_transcription(
        self,
        transcription: TranscriptionResult,
        sender_type: str,
        property_id: str,
    ) -> dict[str, Any]:
        """Feed transcribed text through the learning loop.

        Args:
            transcription: Transcription result.
            sender_type: Role of sender.
            property_id: Property context.

        Returns:
            Processing results (facts, actions, patterns).
        """
        if not self._learning:
            return {"extracted_facts": [], "actions": [], "patterns": []}

        fake_transcript = (
            f"Agent: [Voice message received]\n"
            f"User: {transcription.text}"
        )

        return await self._learning.process_call(
            transcript=fake_transcript,
            call_type=sender_type,
            contact_id=transcription.sender_id,
            contact_name=transcription.sender_name,
            call_outcome="",
        )

    @property
    def transcription_count(self) -> int:
        """Total voice messages processed."""
        return self._transcription_count
