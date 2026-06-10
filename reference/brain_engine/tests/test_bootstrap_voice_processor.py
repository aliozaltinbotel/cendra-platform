"""Tests for the VoiceMessageProcessor wiring in api_server/bootstrap/voice.py.

The bootstrap is part of FastAPI lifespan; tests build only what
:func:`wire` reads — no real Azure / Whisper / Redis is touched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from api_server.bootstrap.voice import (
    _voice_processor_enabled,
    wire,
)
from brain_engine.smart_engine.voice_message_processor import (
    VoiceMessageProcessor,
)


def _wire_kwargs() -> dict[str, Any]:
    """Minimal collaborator stubs accepted by ``wire``.

    The body of ``wire`` only consults attributes / call-args from
    these objects; we do not run any business logic against them so
    bare MagicMocks suffice.
    """
    return {
        "settings": MagicMock(name="settings"),
        "interview_engine": MagicMock(name="interview_engine"),
        "interview_store": MagicMock(name="interview_store"),
        "property_profile_store": MagicMock(name="property_profile_store"),
        "unanswered_thread_store": MagicMock(name="unanswered_thread_store"),
        "sandbox_generator": MagicMock(
            name="sandbox_generator", spec=["name"], name_attr="default",
        ),
    }


@pytest.fixture
def app() -> FastAPI:
    return FastAPI()


@pytest.fixture(autouse=True)
def _silence_azure_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the Azure Whisper branch to take its "not configured" path.

    Without this the test would try to construct a real
    ``AzureWhisperTranscriber`` on whatever credentials happen to be
    in the developer's shell.  The voice-message-processor wiring is
    independent of the answer-voice transcriber, so we just keep the
    latter dormant for the duration of the test.
    """
    monkeypatch.delenv("AZURE_OPENAI_WHISPER_DEPLOYMENT", raising=False)


# ── Env-flag helper ────────────────────────────────────────────────


def test_voice_processor_flag_default_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRAIN_VOICE_PROCESSOR_ENABLED", raising=False)
    assert _voice_processor_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_voice_processor_flag_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("BRAIN_VOICE_PROCESSOR_ENABLED", value)
    assert _voice_processor_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "  "])
def test_voice_processor_flag_falsy(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("BRAIN_VOICE_PROCESSOR_ENABLED", value)
    assert _voice_processor_enabled() is False


# ── Bootstrap wiring ───────────────────────────────────────────────


def test_wire_skips_processor_when_flag_off(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRAIN_VOICE_PROCESSOR_ENABLED", raising=False)

    wire(app, **_wire_kwargs())

    assert not hasattr(app.state, "voice_message_processor")


def test_wire_mounts_processor_when_flag_on(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_VOICE_PROCESSOR_ENABLED", "1")

    wire(app, **_wire_kwargs())

    processor = getattr(app.state, "voice_message_processor", None)
    assert isinstance(processor, VoiceMessageProcessor)
