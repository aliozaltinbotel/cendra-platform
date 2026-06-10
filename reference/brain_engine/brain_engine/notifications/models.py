"""Notification value objects + channel taxonomy.

Cendra V2 promises 24/7 coverage across four urgency tiers:

- ``DIGEST`` — low-priority FYI, batched into a daily summary.
- ``NORMAL`` — push notification, always allowed.
- ``URGENT`` — push + SMS.
- ``CRITICAL`` — push + SMS + phone call (phone requires opt-in).

Channel routing must never silently drop a message: if a PM has
opted out of phone calls and a CRITICAL fires, the dispatcher falls
back to SMS and flags the downgrade.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class NotificationUrgency(StrEnum):
    """Urgency tier — maps to the channel matrix."""

    DIGEST = "digest"
    NORMAL = "normal"
    URGENT = "urgent"
    CRITICAL = "critical"


class NotificationChannel(StrEnum):
    """Concrete delivery channel."""

    PUSH = "push"
    SMS = "sms"
    PHONE = "phone"
    EMAIL = "email"
    DIGEST_ENTRY = "digest_entry"


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Generate a unique notification identifier."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Recipient:
    """The PM (or team member) receiving the notification."""

    recipient_id: str
    name: str = ""
    push_token: str | None = None
    sms_number: str | None = None
    phone_number: str | None = None
    email: str | None = None
    phone_opt_in: bool = False
    quiet_hours_start: int = 22        # 22:00 local
    quiet_hours_end: int = 7           # 07:00 local


@dataclass(frozen=True, slots=True)
class Notification:
    """An envelope the dispatcher routes to one or more channels."""

    recipient_id: str
    title: str
    body: str
    urgency: NotificationUrgency = NotificationUrgency.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)
    notification_id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Outcome of dispatching a single :class:`Notification`.

    ``channels_attempted`` is the list the dispatcher *tried*;
    ``channels_delivered`` is the subset that succeeded; anything in
    ``fallbacks`` explains a downgrade (e.g. PHONE blocked by opt-in).
    """

    notification_id: str
    channels_attempted: tuple[NotificationChannel, ...]
    channels_delivered: tuple[NotificationChannel, ...]
    fallbacks: tuple[str, ...] = ()
    queued_for_digest: bool = False
    delivered_at: datetime = field(default_factory=_utc_now)

    @property
    def all_failed(self) -> bool:
        """True when nothing was delivered."""
        return (
            not self.channels_delivered and not self.queued_for_digest
        )
