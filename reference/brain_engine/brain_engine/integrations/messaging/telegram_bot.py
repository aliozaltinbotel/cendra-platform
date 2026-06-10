"""
Telegram Bot API integration for sending messages, photos, and managing webhooks.

Docs: https://core.telegram.org/bots/api
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.telegram.org"


@dataclass(frozen=True)
class MessageResult:
    """Result of sending a Telegram message."""

    message_id: int
    chat_id: int | str
    status: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class TelegramError(Exception):
    """Raised when a Telegram Bot API request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TelegramBot:
    """
    Async client for the Telegram Bot API.

    Usage::

        async with TelegramBot(token="123:ABC...") as bot:
            await bot.send_message(chat_id=12345, text="Hello!")
            await bot.send_photo(chat_id=12345, photo="https://...")
            await bot.setup_webhook("https://example.com/webhook/telegram")
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._base_url = f"{BASE_URL}/bot{self._token}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> TelegramBot:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> MessageResult:
        """Send a text message to a Telegram chat.

        Args:
            chat_id: Telegram chat / user ID.
            text: Message text (supports HTML by default).
            parse_mode: ``HTML`` or ``MarkdownV2``.
            disable_notification: Send silently.
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        data = await self._post("/sendMessage", payload)
        result = data.get("result", {})
        return MessageResult(
            message_id=result.get("message_id", 0),
            chat_id=chat_id,
            status="sent",
            raw=data,
        )

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        *,
        caption: str | None = None,
        parse_mode: str = "HTML",
    ) -> MessageResult:
        """Send a photo to a Telegram chat.

        Args:
            chat_id: Telegram chat / user ID.
            photo: URL of the photo or a Telegram ``file_id``.
            caption: Optional caption.
            parse_mode: Parse mode for the caption.
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo,
            "parse_mode": parse_mode,
        }
        if caption:
            payload["caption"] = caption

        data = await self._post("/sendPhoto", payload)
        result = data.get("result", {})
        return MessageResult(
            message_id=result.get("message_id", 0),
            chat_id=chat_id,
            status="sent",
            raw=data,
        )

    async def setup_webhook(
        self,
        url: str,
        *,
        secret_token: str | None = None,
        allowed_updates: list[str] | None = None,
        max_connections: int = 40,
    ) -> bool:
        """Register a webhook URL with the Telegram Bot API.

        Args:
            url: HTTPS URL to receive updates.
            secret_token: Optional secret header for verification.
            allowed_updates: List of update types to receive.
            max_connections: Max simultaneous connections.

        Returns:
            ``True`` if the webhook was set successfully.
        """
        payload: dict[str, Any] = {
            "url": url,
            "max_connections": max_connections,
        }
        if secret_token:
            payload["secret_token"] = secret_token
        if allowed_updates:
            payload["allowed_updates"] = allowed_updates

        data = await self._post("/setWebhook", payload)
        success: bool = data.get("result", False)
        if success:
            logger.info("Telegram webhook set to %s", url)
        else:
            logger.warning("Telegram webhook setup failed: %s", data)
        return success

    async def delete_webhook(self) -> bool:
        """Remove the current webhook."""
        data = await self._post("/deleteWebhook", {})
        return data.get("result", False)  # type: ignore[no-any-return]

    async def get_file_url(self, file_id: str) -> str:
        """Get a download URL for a Telegram file.

        Args:
            file_id: Telegram file_id from a photo or document.

        Returns:
            Full URL to download the file.
        """
        data = await self._post("/getFile", {"file_id": file_id})
        file_path = data.get("result", {}).get("file_path", "")
        return f"{BASE_URL}/file/bot{self._token}/{file_path}"

    async def download_file(self, file_id: str) -> bytes:
        """Download a file from Telegram servers.

        Args:
            file_id: Telegram file_id.

        Returns:
            File contents as bytes.
        """
        url = await self.get_file_url(file_id)
        response = await self._client.get(url)
        response.raise_for_status()
        return response.content

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Long-poll for new updates (alternative to webhook).

        Args:
            offset: Identifier of the first update to return.
            timeout: Long polling timeout in seconds.

        Returns:
            List of update objects.
        """
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            response = await self._client.get(
                "/getUpdates",
                params=params,
                timeout=httpx.Timeout(timeout + 10),
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                return []
            return data.get("result", [])
        except Exception as exc:
            logger.error("getUpdates failed: %s", exc)
            return []

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            if not body.get("ok", False):
                description = body.get("description", "Unknown error")
                raise TelegramError(
                    f"Telegram API error: {description}",
                    status_code=body.get("error_code"),
                )
            return body
        except httpx.HTTPStatusError as exc:
            logger.error("Telegram POST %s failed: %s", path, exc.response.text)
            raise TelegramError(
                f"HTTP error on POST {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Telegram POST %s network error: %s", path, exc)
            raise TelegramError(f"Network error on POST {path}: {exc}") from exc
