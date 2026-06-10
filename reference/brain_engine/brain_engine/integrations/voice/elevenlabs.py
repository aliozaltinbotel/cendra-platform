"""ElevenLabs voice synthesis and Conversational AI integration.

Provides text-to-speech, conversational AI outbound phone calls,
call status tracking, and transcript retrieval using the ElevenLabs API.

Docs:
  TTS: https://elevenlabs.io/docs/api-reference/text-to-speech
  ConvAI: https://elevenlabs.io/docs/conversational-ai/api-reference
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"


# ---------------------------------------------------------------------------
# TTS data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceSettings:
    """Voice generation settings."""
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True


@dataclass
class SpeechResult:
    """Result of a TTS call."""
    audio_bytes: bytes
    content_type: str = "audio/mpeg"
    character_count: int = 0


@dataclass
class VoiceInfo:
    """Voice metadata."""
    voice_id: str
    name: str
    category: str = ""
    labels: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Conversational AI data types (compatible with ActionExecutor interface)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallResult:
    """Result of initiating an outbound phone call via ConvAI."""
    call_id: str
    status: str
    phone_number: str
    agent_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class CallStatus:
    """Current status of a ConvAI call."""
    call_id: str
    status: str  # "in-progress", "completed", "failed", etc.
    duration_seconds: float | None = None
    ended_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Transcript:
    """Transcript of a completed ConvAI call."""
    call_id: str
    text: str
    turns: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class ElevenLabsError(Exception):
    """Raised when an ElevenLabs API request fails."""
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code




class ElevenLabsClient:
    """ElevenLabs API client for voice synthesis and Conversational AI calls.

    Supports both TTS and outbound phone calls via ConvAI agents.
    The ``make_call`` / ``get_call_status`` / ``get_transcript`` methods
    are compatible with the ``ActionExecutor`` voice provider interface.

    Usage::

        async with ElevenLabsClient(api_key="sk-...") as client:
            # TTS
            speech = await client.text_to_speech("Hello world")

            # Outbound call (ConvAI)
            result = await client.make_call("+15551234567", "Ask the guest...")
            status = await client.get_call_status(result.call_id)
            transcript = await client.get_transcript(result.call_id)
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model: str = "eleven_multilingual_v2",
        timeout: float = 30.0,
        agent_id: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.agent_id = agent_id
        self._client = httpx.AsyncClient(
            base_url=ELEVENLABS_BASE_URL,
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def text_to_speech(
        self,
        text: str,
        voice_id: str | None = None,
        voice_settings: VoiceSettings | None = None,
        output_format: str = "mp3_44100_128",
    ) -> SpeechResult:
        """Convert text to speech audio.

        Args:
            text: Text to synthesize.
            voice_id: Override default voice ID.
            voice_settings: Voice generation parameters.
            output_format: Audio format (mp3_44100_128, pcm_16000, etc.).

        Returns:
            SpeechResult with audio bytes.
        """
        vid = voice_id or self.voice_id
        settings = voice_settings or VoiceSettings()

        response = await self._client.post(
            f"/text-to-speech/{vid}",
            json={
                "text": text,
                "model_id": self.model,
                "voice_settings": {
                    "stability": settings.stability,
                    "similarity_boost": settings.similarity_boost,
                    "style": settings.style,
                    "use_speaker_boost": settings.use_speaker_boost,
                },
            },
            params={"output_format": output_format},
        )
        response.raise_for_status()

        logger.info("TTS generated for %d chars with voice %s", len(text), vid)

        return SpeechResult(
            audio_bytes=response.content,
            content_type=response.headers.get("content-type", "audio/mpeg"),
            character_count=len(text),
        )

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: str | None = None,
    ):
        """Stream TTS audio chunks for real-time playback.

        Yields audio bytes as they arrive from the API.
        """
        vid = voice_id or self.voice_id

        async with self._client.stream(
            "POST",
            f"/text-to-speech/{vid}/stream",
            json={
                "text": text,
                "model_id": self.model,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            },
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                yield chunk

    async def save_speech(
        self,
        text: str,
        output_path: str | Path,
        voice_id: str | None = None,
    ) -> Path:
        """Generate speech and save to file.

        Args:
            text: Text to synthesize.
            output_path: File path for the audio output.
            voice_id: Override default voice ID.

        Returns:
            Path to the saved audio file.
        """
        result = await self.text_to_speech(text, voice_id=voice_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.audio_bytes)
        logger.info("Saved speech to %s (%d bytes)", path, len(result.audio_bytes))
        return path

    async def list_voices(self) -> list[VoiceInfo]:
        """List all available voices."""
        response = await self._client.get("/voices")
        response.raise_for_status()
        data = response.json()

        return [
            VoiceInfo(
                voice_id=v["voice_id"],
                name=v["name"],
                category=v.get("category", ""),
                labels=v.get("labels", {}),
            )
            for v in data.get("voices", [])
        ]

    async def get_usage(self) -> dict:
        """Get current subscription usage info."""
        response = await self._client.get("/user/subscription")
        response.raise_for_status()
        data = response.json()
        return {
            "character_count": data.get("character_count", 0),
            "character_limit": data.get("character_limit", 0),
            "remaining": data.get("character_limit", 0) - data.get("character_count", 0),
        }

    # ------------------------------------------------------------------
    # Conversational AI — Outbound Phone Calls (Twilio)
    # ------------------------------------------------------------------

    async def make_call(
        self,
        phone_number: str,
        script: str | None = None,
        *,
        agent_id: str | None = None,
        agent_phone_number_id: str | None = None,
        first_message: str | None = None,
        ring_timeout: int = 60,
    ) -> CallResult:
        """Initiate an outbound phone call via ElevenLabs ConvAI + Twilio.

        Args:
            phone_number: Recipient phone number (E.164 format, e.g. "+15551234567").
            script: Optional system prompt override for the agent.
            agent_id: Override default agent ID.
            agent_phone_number_id: Twilio phone number ID registered in ElevenLabs.
            first_message: Optional first message the agent says when call connects.
            ring_timeout: Seconds to ring before timeout (default 60).

        Returns:
            CallResult with call_id (conversation_id) and status.
        """
        aid = agent_id or self.agent_id
        if not aid:
            raise ElevenLabsError("agent_id is required for outbound calls")

        payload: dict[str, Any] = {
            "agent_id": aid,
            "to_number": phone_number,
        }

        if agent_phone_number_id:
            payload["agent_phone_number_id"] = agent_phone_number_id

        if ring_timeout != 60:
            payload["telephony_call_config"] = {"ring_timeout": ring_timeout}

        # Pass dynamic script + first_message via conversation_initiation_client_data
        # Both overrides must be enabled in ElevenLabs Dashboard → Security → Overrides
        overrides: dict[str, Any] = {}
        if script:
            overrides["agent"] = {"prompt": {"prompt": script}}
        if first_message:
            overrides["agent"] = overrides.get("agent", {})
            overrides["agent"]["first_message"] = first_message

        if overrides:
            payload["conversation_initiation_client_data"] = {
                "conversation_config_override": overrides,
            }

        try:
            response = await self._client.post(
                "/convai/twilio/outbound-call",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            call_id = data.get("conversation_id", data.get("callSid", ""))
            logger.info(
                "Outbound call initiated to %s — conversation_id=%s",
                phone_number, call_id,
            )

            return CallResult(
                call_id=call_id,
                status="initiated",
                phone_number=phone_number,
                agent_id=aid,
                raw=data,
            )
        except httpx.HTTPStatusError as exc:
            logger.error("ElevenLabs outbound call failed: %s", exc.response.text)
            raise ElevenLabsError(
                f"Outbound call failed: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc

    async def get_call_status(self, call_id: str) -> CallStatus:
        """Get the current status of a ConvAI conversation/call.

        Args:
            call_id: The conversation_id returned from make_call().

        Returns:
            CallStatus with current state, duration, etc.
        """
        try:
            response = await self._client.get(
                f"/convai/conversations/{call_id}",
            )
            response.raise_for_status()
            data = response.json()

            metadata = data.get("metadata", {})
            status = data.get("status", "unknown")
            duration = metadata.get("call_duration_secs")

            logger.info("Call %s status: %s", call_id, status)

            return CallStatus(
                call_id=call_id,
                status=status,
                duration_seconds=float(duration) if duration is not None else None,
                ended_reason=metadata.get("termination_reason"),
                raw=data,
            )
        except httpx.HTTPStatusError as exc:
            logger.error("Failed to get call status for %s: %s", call_id, exc.response.text)
            raise ElevenLabsError(
                f"Get call status failed: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc

    async def get_transcript(self, call_id: str) -> Transcript:
        """Retrieve the transcript of a completed ConvAI call.

        Args:
            call_id: The conversation_id returned from make_call().

        Returns:
            Transcript with full text and individual turns.
        """
        try:
            response = await self._client.get(
                f"/convai/conversations/{call_id}",
            )
            response.raise_for_status()
            data = response.json()

            raw_transcript = data.get("transcript", [])
            turns = [
                {
                    "role": turn.get("role", "unknown"),
                    "message": turn.get("message", ""),
                    "time": turn.get("time_in_call_secs", 0),
                }
                for turn in raw_transcript
            ]

            full_text = "\n".join(
                f"{t['role'].capitalize()}: {t['message']}" for t in turns
            )

            logger.info(
                "Transcript for call %s: %d turns", call_id, len(turns),
            )

            return Transcript(
                call_id=call_id,
                text=full_text,
                turns=turns,
                raw=data,
            )
        except httpx.HTTPStatusError as exc:
            logger.error("Failed to get transcript for %s: %s", call_id, exc.response.text)
            raise ElevenLabsError(
                f"Get transcript failed: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
