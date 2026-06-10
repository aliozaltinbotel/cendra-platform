"""Channel dispatcher — routes :class:`Notification` to the right channels.

The dispatcher's job is *only* to decide which channels to try and in
what order, then delegate the actual send to a Protocol transport.
It never silently drops a message — every decision lands in
:class:`DeliveryResult`.

Routing table:

========  =======================================  ===========================
Urgency   Channels                                 Quiet-hours behavior
========  =======================================  ===========================
DIGEST    DIGEST_ENTRY (batched in daily summary)  Always allowed
NORMAL    PUSH                                     Allowed; digest as fallback
URGENT    PUSH + SMS                               Allowed; bypasses quiet
CRITICAL  PUSH + SMS + PHONE (opt-in)              Always bypasses quiet
========  =======================================  ===========================
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import structlog

from brain_engine.notifications.models import (
    DeliveryResult,
    Notification,
    NotificationChannel,
    NotificationUrgency,
    Recipient,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Transport Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationTransport(Protocol):
    """One outbound channel (push / sms / phone / email / digest)."""

    channel: NotificationChannel

    async def send(
        self,
        *,
        recipient: Recipient,
        notification: Notification,
    ) -> bool:
        """Deliver ``notification`` or return False on failure."""
        ...


# ---------------------------------------------------------------------------
# Channel matrix
# ---------------------------------------------------------------------------


_CHANNEL_MATRIX: dict[
    NotificationUrgency, tuple[NotificationChannel, ...]
] = {
    NotificationUrgency.DIGEST: (NotificationChannel.DIGEST_ENTRY,),
    NotificationUrgency.NORMAL: (NotificationChannel.PUSH,),
    NotificationUrgency.URGENT: (
        NotificationChannel.PUSH,
        NotificationChannel.SMS,
    ),
    NotificationUrgency.CRITICAL: (
        NotificationChannel.PUSH,
        NotificationChannel.SMS,
        NotificationChannel.PHONE,
    ),
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class ChannelDispatcher:
    """Routes :class:`Notification` objects to the configured transports.

    Construction accepts a mapping of channel → transport.  Any
    channel missing from the mapping is treated as "not available"
    and recorded as a fallback rather than silently failing.
    """

    def __init__(
        self,
        transports: dict[NotificationChannel, NotificationTransport],
        *,
        clock: callable | None = None,  # type: ignore[valid-type]
    ) -> None:
        self._transports = transports
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = logger.bind(component="channel_dispatcher")

    async def dispatch(
        self,
        *,
        recipient: Recipient,
        notification: Notification,
    ) -> DeliveryResult:
        """Dispatch a notification and return the delivery record."""
        planned = _CHANNEL_MATRIX[notification.urgency]
        quiet = self._is_quiet_hours(recipient)

        fallbacks: list[str] = []
        delivered: list[NotificationChannel] = []
        attempted: list[NotificationChannel] = []

        for channel in planned:
            attempted.append(channel)
            decision = self._should_attempt(
                channel=channel,
                recipient=recipient,
                urgency=notification.urgency,
                quiet=quiet,
            )
            if decision.skip:
                fallbacks.append(decision.reason)
                continue
            transport = self._transports.get(channel)
            if transport is None:
                fallbacks.append(f"{channel.value}: no transport")
                continue
            ok = await transport.send(
                recipient=recipient, notification=notification,
            )
            if ok:
                delivered.append(channel)
            else:
                fallbacks.append(f"{channel.value}: transport returned false")

        queued = False
        if not delivered and notification.urgency is NotificationUrgency.NORMAL:
            queued = await self._digest_fallback(recipient, notification)
            if queued:
                attempted.append(NotificationChannel.DIGEST_ENTRY)

        result = DeliveryResult(
            notification_id=notification.notification_id,
            channels_attempted=tuple(attempted),
            channels_delivered=tuple(delivered),
            fallbacks=tuple(fallbacks),
            queued_for_digest=queued,
        )
        if result.all_failed:
            self._log.error(
                "notification.dispatch_failed",
                recipient=recipient.recipient_id,
                urgency=notification.urgency.value,
                fallbacks=list(fallbacks),
            )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_quiet_hours(self, recipient: Recipient) -> bool:
        now = self._clock()
        hour = now.hour
        start = recipient.quiet_hours_start
        end = recipient.quiet_hours_end
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    @staticmethod
    def _should_attempt(
        *,
        channel: NotificationChannel,
        recipient: Recipient,
        urgency: NotificationUrgency,
        quiet: bool,
    ) -> "_Decision":
        if channel is NotificationChannel.PUSH and not recipient.push_token:
            return _Decision(True, "push: no token")
        if channel is NotificationChannel.SMS and not recipient.sms_number:
            return _Decision(True, "sms: no number")
        if channel is NotificationChannel.PHONE:
            if not recipient.phone_opt_in:
                return _Decision(True, "phone: opt-in missing")
            if not recipient.phone_number:
                return _Decision(True, "phone: no number")
        if quiet and urgency is NotificationUrgency.NORMAL:
            if channel is not NotificationChannel.DIGEST_ENTRY:
                return _Decision(True, f"{channel.value}: quiet hours")
        return _Decision(False, "")

    async def _digest_fallback(
        self,
        recipient: Recipient,
        notification: Notification,
    ) -> bool:
        transport = self._transports.get(NotificationChannel.DIGEST_ENTRY)
        if transport is None:
            return False
        return await transport.send(
            recipient=recipient, notification=notification,
        )


class _Decision:
    """Internal helper: whether to skip a channel and why."""

    __slots__ = ("skip", "reason")

    def __init__(self, skip: bool, reason: str) -> None:
        self.skip = skip
        self.reason = reason
