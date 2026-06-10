"""TelegramApprovalNotifier — Adapts TelegramBot for approval notifications.

Wraps the existing TelegramBot to satisfy the Notifier protocol,
adding approval-specific formatting and owner→chat_id mapping.

The owner receives a formatted message with inline /approve and /deny
commands. Their reply is processed by the Telegram polling loop in
server.py and forwarded to the ApprovalGateway.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TelegramApprovalNotifier:
    """Sends approval requests to owners via Telegram.

    Wraps TelegramBot.send_message with approval-specific formatting
    and maintains an owner_id→chat_id registry.

    Args:
        telegram_bot: The existing TelegramBot instance.
        owner_chat_ids: Mapping of owner_id → Telegram chat_id.
    """

    def __init__(
        self,
        telegram_bot: Any,
        owner_chat_ids: dict[str, int | str] | None = None,
    ) -> None:
        self._bot = telegram_bot
        self._owner_chats: dict[str, int | str] = owner_chat_ids or {}

    def register_owner(self, owner_id: str, chat_id: int | str) -> None:
        """Register or update an owner's Telegram chat_id.

        Args:
            owner_id: Owner identifier in the Brain Engine.
            chat_id: Telegram chat_id for this owner.
        """
        self._owner_chats[owner_id] = chat_id
        logger.info("Registered owner %s → chat_id %s", owner_id, chat_id)

    def get_chat_id(self, owner_id: str) -> int | str | None:
        """Resolve owner_id to Telegram chat_id."""
        return self._owner_chats.get(owner_id)

    async def send_approval_request(
        self,
        *,
        owner_id: str,
        message: str,
        request_id: str,
    ) -> Any:
        """Send a formatted approval request to the owner's Telegram.

        Args:
            owner_id: Owner to notify.
            message: Pre-formatted approval message.
            request_id: Approval request ID (for reference).

        Returns:
            MessageResult from TelegramBot, or None if chat_id unknown.
        """
        chat_id = self._owner_chats.get(owner_id)
        if not chat_id:
            logger.warning(
                "No Telegram chat_id for owner %s — cannot send approval request %s",
                owner_id, request_id,
            )
            return None

        formatted = (
            f"<b>🔔 Approval Required</b>\n"
            f"<code>{request_id}</code>\n\n"
            f"{message}\n\n"
            f"<b>Reply:</b>\n"
            f"  /approve {request_id}\n"
            f"  /deny {request_id}\n"
            f"  /approve {request_id} always — approve + make rule"
        )

        try:
            result = await self._bot.send_message(
                chat_id=int(chat_id) if isinstance(chat_id, str) else chat_id,
                text=formatted,
            )
            logger.info(
                "Approval request %s sent to owner %s (chat_id=%s)",
                request_id, owner_id, chat_id,
            )
            return result
        except Exception:
            logger.exception(
                "Failed to send approval request %s to owner %s",
                request_id, owner_id,
            )
            return None

    async def send_message(
        self,
        *,
        target: str = "",
        chat_id: int = 0,
        text: str = "",
        owner_id: str = "",
    ) -> Any:
        """Send a plain text message. Satisfies Notifier protocol.

        Args:
            target: Phone number or chat_id as string.
            chat_id: Direct Telegram chat_id.
            text: Message text.
            owner_id: Owner ID to resolve to chat_id.
        """
        resolved_chat_id = chat_id or self._owner_chats.get(owner_id) or target
        if not resolved_chat_id:
            logger.warning("No chat_id resolved for message (owner=%s)", owner_id)
            return None

        try:
            return await self._bot.send_message(
                chat_id=int(resolved_chat_id),
                text=text,
            )
        except Exception:
            logger.exception("Failed to send message to %s", resolved_chat_id)
            return None

    async def send_learning_questions(
        self,
        *,
        owner_id: str,
        questions: list[dict[str, Any]],
    ) -> None:
        """Send follow-up learning questions after an approval decision.

        Args:
            owner_id: Owner to send questions to.
            questions: List of question dicts with text and options.
        """
        chat_id = self._owner_chats.get(owner_id)
        if not chat_id:
            return

        for q in questions:
            options_text = "\n".join(
                f"  {i + 1}. {opt}"
                for i, opt in enumerate(q.get("options", []))
            )
            text = (
                f"<b>Quick question:</b>\n\n"
                f"{q.get('text', '')}\n\n"
                f"{options_text}\n\n"
                f"Reply with the number or /skip"
            )
            try:
                await self._bot.send_message(chat_id=int(chat_id), text=text)
            except Exception:
                logger.exception("Failed to send learning question to %s", owner_id)
