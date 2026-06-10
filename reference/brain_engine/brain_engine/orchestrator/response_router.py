"""Response Router — delivers Telegram messages to active orchestrators.

Maintains a global mapping of chat_id -> orchestrator so that when
a cleaner/vendor/PMS user replies in Telegram, the message is
routed to the correct BookingOrchestrator instance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain_engine.orchestrator.booking_orchestrator import BookingOrchestrator

logger = logging.getLogger(__name__)


class ResponseRouter:
    """Routes incoming Telegram messages to active orchestrators.

    Thread-safe singleton that maps chat_id to the orchestrator
    currently waiting for a response from that chat.
    """

    def __init__(self) -> None:
        self._chat_to_orchestrator: dict[str, BookingOrchestrator] = {}
        self._process_orchestrators: dict[str, BookingOrchestrator] = {}

    def register(
        self,
        chat_id: str,
        orchestrator: BookingOrchestrator,
    ) -> None:
        """Register a chat_id as being watched by an orchestrator.

        Args:
            chat_id: Telegram chat ID.
            orchestrator: The orchestrator waiting for this chat.
        """
        self._chat_to_orchestrator[chat_id] = orchestrator
        self._process_orchestrators[orchestrator.process_id] = orchestrator
        logger.debug("Router: chat_id=%s -> process=%s", chat_id, orchestrator.process_id)

    def unregister(self, chat_id: str) -> None:
        """Remove a chat_id from routing.

        Args:
            chat_id: Telegram chat ID to remove.
        """
        self._chat_to_orchestrator.pop(chat_id, None)

    def unregister_process(self, process_id: str) -> None:
        """Remove all chat_ids for a process.

        Args:
            process_id: Process to clean up.
        """
        orch = self._process_orchestrators.pop(process_id, None)
        if not orch:
            return
        to_remove = [
            cid for cid, o in self._chat_to_orchestrator.items()
            if o is orch
        ]
        for cid in to_remove:
            del self._chat_to_orchestrator[cid]
        logger.debug("Router: cleaned up process=%s (%d chats)", process_id, len(to_remove))

    def get_orchestrator(self, chat_id: str) -> BookingOrchestrator | None:
        """Find the orchestrator waiting for this chat_id.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            BookingOrchestrator or None.
        """
        return self._chat_to_orchestrator.get(chat_id)

    def has_active_process(self, chat_id: str) -> bool:
        """Check if a chat_id has an active orchestrator.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if an orchestrator is waiting.
        """
        return chat_id in self._chat_to_orchestrator

    @property
    def active_count(self) -> int:
        """Number of active orchestrator processes."""
        return len(self._process_orchestrators)


# Global singleton
response_router = ResponseRouter()
