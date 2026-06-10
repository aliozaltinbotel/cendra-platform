"""Action / Undo error hierarchy."""

from __future__ import annotations


class ActionError(Exception):
    """Base for action-pipeline errors."""


class ActionNotFound(ActionError):
    """Raised when an action_id is not present in the store."""


class UndoNotAllowed(ActionError):
    """Raised when reversibility tier forbids undo (RED)."""


class UndoWindowExpired(ActionError):
    """Raised when the GREEN/AMBER window has closed."""


class AlreadyUndone(ActionError):
    """Raised when the action was already reversed."""


class NotYetExecuted(ActionError):
    """Raised when Undo is attempted on a PENDING action."""


class CompensationFailed(ActionError):
    """Raised when an AMBER compensating call raises downstream."""
