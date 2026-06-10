"""Conversation archive loader Protocol.

An archive loader turns a property's historical reservations into a
stream of :class:`ArchivedConversation` objects.  The canonical
implementation is
:class:`brain_engine.onboarding.graphql_archive_loader.GraphQLConversationArchiveLoader`,
which reads from the onboarding-api unified GraphQL gateway.
Alternative backends (test fixtures, future channel adapters) plug in
by implementing the :class:`ConversationArchiveLoader` Protocol.

All adapters must:

- Filter by ``property_id`` upstream where possible.
- Respect ``since`` / ``until`` / ``limit`` arguments.
- Raise :class:`ConversationArchiveError` (wrapping the underlying
  exception) on any infrastructure failure.
- Never leak partially-constructed objects — a malformed record is
  skipped with a structured log entry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from brain_engine.onboarding.models import ArchivedConversation

__all__ = [
    "ConversationArchiveLoader",
]


@runtime_checkable
class ConversationArchiveLoader(Protocol):
    """Adapter that yields archived conversations for a property."""

    name: str

    def load(
        self,
        *,
        property_id: str,
        since: datetime,
        until: datetime,
        limit: int = 500,
    ) -> AsyncIterator[ArchivedConversation]:
        """Yield conversations for ``property_id`` inside the window."""
        ...
