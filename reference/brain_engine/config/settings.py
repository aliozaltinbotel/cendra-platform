"""Application settings loaded from environment variables and .env files.

Uses pydantic-settings for validated, typed configuration with sensible
defaults for local development.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised settings for the Airbnb Brain Engine.

    All values can be overridden via environment variables or a ``.env``
    file in the project root.  Pydantic-settings handles parsing, validation,
    and type coercion automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM / AI ──────────────────────────────────────────────────────────
    llm_model: str = Field(
        default="gpt-4o",
        description="Primary LLM model identifier.",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Model used for generating text embeddings.",
    )

    # ── Azure OpenAI ──────────────────────────────────────────────────────
    # Azure OpenAI is the sole LLM backend.  Public ``api.openai.com``
    # is never called.  All chat / embedding / vision / Whisper traffic
    # routes through the Azure deployments below.
    azure_openai_api_key: str = Field(
        default="",
        description="Azure OpenAI resource key (botel-llm in prod).",
    )
    azure_openai_endpoint: str = Field(
        default="",
        description="Azure OpenAI endpoint, e.g. "
        "https://botel-llm.openai.azure.com/.",
    )
    azure_openai_api_version: str = Field(
        default="2024-08-01-preview",
        description="Azure OpenAI REST API version.",
    )
    azure_openai_gpt4o_deployment: str = Field(
        default="gpt-4o",
        description="Deployment name for the GPT-4o chat model.",
    )
    azure_openai_gpt4o_mini_deployment: str = Field(
        default="gpt-4o-mini",
        description="Deployment name for the GPT-4o-mini chat model.",
    )
    azure_openai_embedding_deployment: str = Field(
        default="text-embedding-3-large",
        description="Deployment name for the embedding model.",
    )
    azure_openai_whisper_deployment: str = Field(
        default="",
        description="Deployment name for the Whisper transcription "
        "model.  Empty = Azure Whisper not provisioned, voice "
        "endpoints respond 503.",
    )

    # ── Voice (ElevenLabs) ─────────────────────────────────────────────────
    elevenlabs_api_key: str = Field(
        default="",
        description="ElevenLabs API key for voice synthesis.",
    )
    elevenlabs_voice_id: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",
        description="ElevenLabs voice ID (default: Rachel).",
    )
    elevenlabs_model: str = Field(
        default="eleven_multilingual_v2",
        description="ElevenLabs TTS model.",
    )

    # ── External service keys ─────────────────────────────────────────────
    elevenlabs_agent_id: str = Field(
        default="",
        description="ElevenLabs Conversational AI agent ID for outbound calls.",
    )
    elevenlabs_phone_number_id: str = Field(
        default="",
        description="ElevenLabs Twilio phone number ID for outbound calls.",
    )
    nuki_api_key: str = Field(
        default="",
        description="Nuki Smart Lock API key.",
    )
    whatsapp_token: str = Field(
        default="",
        description="WhatsApp Business API token.",
    )
    telegram_bot_token: str = Field(
        default="",
        description="Telegram Bot API token for cleaner communication.",
    )
    telegram_webhook_secret: str = Field(
        default="cendra-tg-secret-2026",
        description="Secret token for Telegram webhook verification.",
    )
    # ── Vector database ───────────────────────────────────────────────────
    qdrant_url: str = Field(
        default="http://localhost:6333",
        description="Qdrant server URL.",
    )
    qdrant_api_key: str = Field(
        default="",
        description="Qdrant API key (empty for local instances).",
    )
    vector_db_collection: str = Field(
        default="airbnb_knowledge",
        description="Default Qdrant collection name.",
    )

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for episodic memory.",
    )

    # ── AWS ─────────────────────────────────────────────────────────────────
    aws_region: str = Field(
        default="us-east-1",
        description="AWS region.",
    )
    s3_bucket: str = Field(
        default="",
        description="S3 bucket for photos and data storage.",
    )

    # ── Server / operational ──────────────────────────────────────────────
    max_retries: int = Field(
        default=3,
        description="Maximum retry attempts for external API calls.",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    ui_api_key: str | None = Field(
        default=None,
        description="Optional API key for securing the AG-UI endpoint.",
    )
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        description="CORS allowed origins. Use ['*'] for development.",
    )
    rate_limit_max_requests: int = Field(
        default=60,
        description="Maximum requests per rate-limit window.",
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        description="Rate-limit sliding window in seconds.",
    )

    # ── Nuki Smart Lock ────────────────────────────────────────────────
    nuki_lock_id: str = Field(
        default="",
        description="Default Nuki smart lock ID for property entry detection.",
    )
    nuki_webhook_secret: str = Field(
        default="",
        description="Secret token for verifying Nuki webhook requests.",
    )
    nuki_polling_interval_seconds: int = Field(
        default=30,
        description="Interval in seconds for polling Nuki activity log.",
    )

    # ── Flight Tracking ────────────────────────────────────────────────
    aviationstack_api_key: str = Field(
        default="",
        description="AviationStack API key for flight tracking.",
    )

    # ── Google Maps ────────────────────────────────────────────────────
    google_maps_api_key: str = Field(
        default="",
        description="Google Maps API key for distance/traffic estimation.",
    )

    # ── Smart Home (Climate Control) ──────────────────────────────────
    sensibo_api_key: str = Field(
        default="",
        description="Sensibo API key for smart AC/climate control.",
    )
    sensibo_device_id: str = Field(
        default="",
        description="Default Sensibo device ID for the property.",
    )

    # ── Brain Engine API ───────────────────────────────────────────────
    brain_api_key: str = Field(
        default="",
        description="API key for Brain Engine endpoints (empty = no auth).",
    )
    default_cognitive_model: str = Field(
        default="gpt-4o-mini",
        description="Default LLM model for L1/L2 cognitive levels.",
    )
    nightly_consolidation_hour: int = Field(
        default=3,
        description="Hour (UTC) to run nightly consolidation (0-23).",
    )
