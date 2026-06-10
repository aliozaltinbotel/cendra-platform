"""Action envelope + Undo executor — three-tier reversibility.

Public surface:

- :class:`ActionEnvelope` / :class:`ActionStatus` — immutable action
  record + lifecycle states.
- :class:`UndoExecutor` — validates and applies Undo.
- :class:`ActionStore` / :class:`InMemoryActionStore` — persistence
  Protocol + in-memory impl.
- :class:`CompensatingTransport` — AMBER downstream caller Protocol.
- :mod:`brain_engine.actions.errors` — typed error hierarchy.
"""

from __future__ import annotations

from brain_engine.actions.errors import (
    ActionError,
    ActionNotFound,
    AlreadyUndone,
    CompensationFailed,
    NotYetExecuted,
    UndoNotAllowed,
    UndoWindowExpired,
)
from brain_engine.actions.executor import (
    ActionStore,
    CompensatingTransport,
    InMemoryActionStore,
    UndoExecutor,
)
from brain_engine.actions.models import ActionEnvelope, ActionStatus

__all__ = [
    "ActionEnvelope",
    "ActionError",
    "ActionNotFound",
    "ActionStatus",
    "ActionStore",
    "AlreadyUndone",
    "CompensatingTransport",
    "CompensationFailed",
    "InMemoryActionStore",
    "NotYetExecuted",
    "UndoExecutor",
    "UndoNotAllowed",
    "UndoWindowExpired",
]
