"""24/7 channel dispatch — push / SMS / phone / digest routing.

Public surface:

- :class:`Notification` / :class:`NotificationUrgency` /
  :class:`NotificationChannel` — taxonomy + envelope value objects.
- :class:`Recipient` — PM delivery profile.
- :class:`DeliveryResult` — routing audit record.
- :class:`ChannelDispatcher` — urgency → channel routing engine.
- :class:`NotificationTransport` — per-channel Protocol.
"""

from __future__ import annotations

from brain_engine.notifications.dispatcher import (
    ChannelDispatcher,
    NotificationTransport,
)
from brain_engine.notifications.models import (
    DeliveryResult,
    Notification,
    NotificationChannel,
    NotificationUrgency,
    Recipient,
)

__all__ = [
    "ChannelDispatcher",
    "DeliveryResult",
    "Notification",
    "NotificationChannel",
    "NotificationTransport",
    "NotificationUrgency",
    "Recipient",
]
