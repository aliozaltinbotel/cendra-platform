"""Exception hierarchy for the onboarding bootstrap flow.

Mirrors the ``brain_engine.narrative`` error shape: every domain
exception derives from :class:`~brain_engine.exceptions.BrainEngineError`
so callers can catch a single root without importing sub-packages.
"""

from __future__ import annotations

from brain_engine.exceptions import BrainEngineError

__all__ = [
    "ConversationArchiveError",
    "HistoricalExtractionError",
    "OnboardingError",
]


class OnboardingError(BrainEngineError):
    """Base class for onboarding-specific failures."""


class ConversationArchiveError(OnboardingError):
    """Raised when a :class:`ConversationArchiveLoader` cannot fulfil a request.

    Wraps the underlying backend exception via ``raise ... from exc``
    so the caller keeps the full chain for diagnostics.
    """

    def __init__(
        self,
        loader: str,
        reason: str,
        *,
        property_id: str = "",
    ) -> None:
        super().__init__(f"archive loader {loader!r}: {reason}")
        self.loader = loader
        self.property_id = property_id


class HistoricalExtractionError(OnboardingError):
    """Raised when a conversation cannot be turned into a DecisionCase."""

    def __init__(
        self,
        reason: str,
        *,
        conversation_id: str = "",
        property_id: str = "",
    ) -> None:
        super().__init__(f"historical extraction failed: {reason}")
        self.conversation_id = conversation_id
        self.property_id = property_id
