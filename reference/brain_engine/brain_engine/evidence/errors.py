"""Typed error hierarchy for the evidence composer.

Each exception carries enough context for the HTTP layer to select the
correct status code without re-parsing messages.  Source-specific
failures wrap the upstream exception via ``raise ... from e`` so the
audit log preserves the full cause chain.
"""

from __future__ import annotations


class EvidenceError(Exception):
    """Base for every error raised by the evidence subsystem."""


class EvidenceNotFound(EvidenceError):
    """The referenced decision could not be located."""

    def __init__(self, decision_id: str) -> None:
        super().__init__(f"decision not found: {decision_id}")
        self.decision_id = decision_id


class EvidenceSourceError(EvidenceError):
    """A single source failed during composition.

    The composer catches this, records a summary in
    :attr:`EvidenceBundle.errors`, and continues so one broken source
    does not sink the entire bundle.
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.reason = message


class EvidenceCompositionError(EvidenceError):
    """Unrecoverable failure while assembling the bundle."""
