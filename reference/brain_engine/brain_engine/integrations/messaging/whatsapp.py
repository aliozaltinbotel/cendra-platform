"""
WhatsApp Business API integration for sending and receiving messages.

Uses the Meta Cloud API (graph.facebook.com).
Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


@dataclass(frozen=True)
class MessageResult:
    """Result of sending a WhatsApp message."""

    message_id: str
    status: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class IncomingMessage:
    """Parsed incoming message from a webhook payload."""

    sender_phone: str
    message_id: str
    text: str | None = None
    image_url: str | None = None
    timestamp: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class WhatsAppError(Exception):
    """Raised when a WhatsApp API request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WhatsAppClient:
    """
    Async client for the WhatsApp Business Cloud API.

    Usage::

        async with WhatsAppClient(token="EAA...", phone_number_id="12345") as wa:
            result = await wa.send_message("+15551234567", "Hello!")
            result = await wa.send_image("+15551234567", "https://...")
    """

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._phone_number_id = phone_number_id
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> WhatsAppClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_message(self, phone: str, text: str) -> MessageResult:
        """Send a text message to a WhatsApp number.

        Args:
            phone: Recipient phone number in E.164 format.
            text: Message body.

        Returns:
            :class:`MessageResult` with ``message_id``.
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": text},
        }
        data = await self._post(
            f"/{self._phone_number_id}/messages", payload
        )
        messages = data.get("messages", [{}])
        return MessageResult(
            message_id=messages[0].get("id", "") if messages else "",
            status="sent",
            raw=data,
        )

    async def send_image(
        self,
        phone: str,
        image_url: str,
        *,
        caption: str | None = None,
    ) -> MessageResult:
        """Send an image message to a WhatsApp number.

        Args:
            phone: Recipient phone number.
            image_url: Publicly accessible URL of the image.
            caption: Optional caption text.
        """
        image_obj: dict[str, str] = {"link": image_url}
        if caption:
            image_obj["caption"] = caption

        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "image",
            "image": image_obj,
        }
        data = await self._post(
            f"/{self._phone_number_id}/messages", payload
        )
        messages = data.get("messages", [{}])
        return MessageResult(
            message_id=messages[0].get("id", "") if messages else "",
            status="sent",
            raw=data,
        )

    async def receive_webhook(self, data: dict[str, Any]) -> list[IncomingMessage]:
        """Parse an incoming webhook payload from WhatsApp.

        This is a synchronous parse wrapped as async for interface
        consistency. It extracts all incoming messages from the payload.

        Args:
            data: The raw JSON body from the webhook POST.

        Returns:
            List of :class:`IncomingMessage` objects.
        """
        messages: list[IncomingMessage] = []

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    text = None
                    image_url = None

                    if msg.get("type") == "text":
                        text = msg.get("text", {}).get("body")
                    elif msg.get("type") == "image":
                        image_url = msg.get("image", {}).get("url")

                    messages.append(
                        IncomingMessage(
                            sender_phone=msg.get("from", ""),
                            message_id=msg.get("id", ""),
                            text=text,
                            image_url=image_url,
                            timestamp=msg.get("timestamp"),
                            raw=msg,
                        )
                    )

        return messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("WhatsApp POST %s failed: %s", path, exc.response.text)
            raise WhatsAppError(
                f"API error on POST {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("WhatsApp POST %s network error: %s", path, exc)
            raise WhatsAppError(f"Network error on POST {path}: {exc}") from exc
