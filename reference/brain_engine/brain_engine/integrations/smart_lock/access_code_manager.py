"""
Access Code Manager - orchestrates temporary code generation and delivery.

Generates time-limited access codes via a smart lock provider and delivers
them to guests through a messaging integration.
"""

from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols so the manager is provider-agnostic
# ---------------------------------------------------------------------------


@runtime_checkable
class SmartLockProvider(Protocol):
    """Any smart lock backend that can create access codes."""

    async def create_access_code(
        self,
        lock_id: str,
        name: str,
        start: datetime,
        end: datetime,
        *,
        code: str | None = None,
    ) -> Any: ...

    async def get_status(self, lock_id: str) -> Any: ...


@runtime_checkable
class MessagingProvider(Protocol):
    """Any messaging backend that can send text messages."""

    async def send_message(self, recipient: str | int, text: str) -> Any: ...


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class DeliveryChannel(str, Enum):
    """Supported delivery channels for access codes."""

    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    SMS = "sms"


@dataclass
class AccessCodeRecord:
    """Persisted record of an issued access code."""

    code_id: str
    lock_id: str
    code: str
    guest_name: str
    start: datetime
    end: datetime
    delivered: bool = False
    delivery_channel: DeliveryChannel | None = None
    recipient: str | int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class AccessCodeManager:
    """
    High-level orchestrator for creating and delivering temporary access codes.

    Usage::

        manager = AccessCodeManager(
            lock_provider=nuki_client,
            messaging_provider=whatsapp_client,
        )
        record = await manager.create_and_deliver(
            lock_id="12345",
            guest_name="John",
            guest_contact="+15551234567",
            start=checkin_time,
            end=checkout_time,
            channel=DeliveryChannel.WHATSAPP,
        )
    """

    def __init__(
        self,
        lock_provider: SmartLockProvider,
        messaging_provider: MessagingProvider | None = None,
        *,
        code_length: int = 6,
    ) -> None:
        self._lock = lock_provider
        self._messaging = messaging_provider
        self._code_length = code_length
        self._records: dict[str, AccessCodeRecord] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_and_deliver(
        self,
        lock_id: str,
        guest_name: str,
        guest_contact: str | int,
        start: datetime,
        end: datetime,
        *,
        channel: DeliveryChannel = DeliveryChannel.WHATSAPP,
        custom_code: str | None = None,
        message_template: str | None = None,
    ) -> AccessCodeRecord:
        """Create a temporary access code and send it to the guest.

        Args:
            lock_id: Identifier for the smart lock.
            guest_name: Human-readable guest label.
            guest_contact: Phone number or chat ID.
            start: Code validity start.
            end: Code validity end.
            channel: How to deliver the code.
            custom_code: Use a specific code instead of auto-generating.
            message_template: Custom message; use ``{code}`` placeholder.

        Returns:
            :class:`AccessCodeRecord` with delivery status.
        """
        pin = custom_code or self._generate_code()

        # Create the code on the lock
        result = await self._lock.create_access_code(
            lock_id, guest_name, start, end, code=pin
        )
        code_id = getattr(result, "code_id", "") or str(id(result))
        actual_code = getattr(result, "code", pin) or pin

        record = AccessCodeRecord(
            code_id=code_id,
            lock_id=lock_id,
            code=actual_code,
            guest_name=guest_name,
            start=start,
            end=end,
            delivery_channel=channel,
            recipient=guest_contact,
        )

        # Deliver via messaging
        if self._messaging is not None:
            template = message_template or (
                "Hi {name}! Your access code for your stay is: {code}\n"
                "Valid from {start} to {end}.\n"
                "Please keep this code confidential."
            )
            message = template.format(
                name=guest_name,
                code=actual_code,
                start=start.strftime("%b %d, %I:%M %p"),
                end=end.strftime("%b %d, %I:%M %p"),
            )
            try:
                await self._messaging.send_message(guest_contact, message)
                record.delivered = True
                logger.info(
                    "Access code delivered to %s via %s",
                    guest_contact,
                    channel.value,
                )
            except Exception:
                logger.exception(
                    "Failed to deliver access code to %s", guest_contact
                )
        else:
            logger.warning(
                "No messaging provider configured; code created but not delivered"
            )

        self._records[code_id] = record
        return record

    async def revoke(self, code_id: str) -> None:
        """Revoke a previously issued code (remove from local registry).

        Actual revocation on the lock provider depends on provider
        capabilities and should be handled externally if needed.
        """
        record = self._records.pop(code_id, None)
        if record:
            logger.info("Revoked access code %s for lock %s", code_id, record.lock_id)
        else:
            logger.warning("Access code %s not found in registry", code_id)

    def get_record(self, code_id: str) -> AccessCodeRecord | None:
        """Look up an access code record by ID."""
        return self._records.get(code_id)

    def list_active_codes(self, lock_id: str | None = None) -> list[AccessCodeRecord]:
        """List all active (non-expired) access codes, optionally filtered by lock."""
        now = datetime.now()
        return [
            r
            for r in self._records.values()
            if r.end > now and (lock_id is None or r.lock_id == lock_id)
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _generate_code(self) -> str:
        """Generate a random numeric PIN."""
        return "".join(
            secrets.choice(string.digits) for _ in range(self._code_length)
        )
