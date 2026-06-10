"""Exception hierarchy for the narrative subsystem.

All narrative errors derive from :class:`NarrativeError` and plug into
the project-wide :class:`~brain_engine.exceptions.BrainEngineError`
base so they share the ``code`` / ``context`` convention.
"""

from __future__ import annotations

from typing import Any

from brain_engine.exceptions import BrainEngineError

__all__ = [
    "NarrativeCompositionError",
    "NarrativeError",
    "TimelineSourceError",
    "VoiceSynthesisUnavailable",
]


class NarrativeError(BrainEngineError):
    """Base exception for the narrative subsystem."""


class TimelineSourceError(NarrativeError):
    """Raised when a timeline source adapter fails to fetch events.

    Adapters catch their native exceptions and re-raise this wrapper
    with ``raise ... from e`` so the composer can decide whether to
    degrade gracefully or surface the failure.
    """

    def __init__(self, source: str, message: str, **context: Any) -> None:
        super().__init__(
            f"Timeline source '{source}' failed: {message}",
            code=502,
            source=source,
            **context,
        )
        self.source = source


class NarrativeCompositionError(NarrativeError):
    """Raised when the composer cannot assemble a coherent timeline."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message, code=500, **context)


class VoiceSynthesisUnavailable(NarrativeError):
    """Raised when a voice narrative is requested without an active TTS.

    This covers both the "ElevenLabs client was not configured at
    startup" case and runtime failures from the provider.
    """

    def __init__(self, message: str = "Voice synthesis unavailable") -> None:
        super().__init__(message, code=503)
