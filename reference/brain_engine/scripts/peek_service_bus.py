"""Read-only peek into the backend's Azure Service Bus topics.

The backend developer publishes change events to one topic per
resource family (``botel-property-sync``, ``botel-reservation-sync``,
…) in the ``booklyservicebus`` namespace.  This script answers two
questions an operator has *before* writing a real consumer:

  1. Are events actually landing in our subscription right now?
  2. What does each event payload look like (so we can map it to
     brain actions without waiting on a hand-written schema)?

**No Azure portal access is required.** The namespace connection
string is the credential — the SDK connects and reads with it
directly.  Reuse the value already configured on the pod
(``AZURE_SERVICEBUS_CONNECTION_STRING`` in
``deploy/brain-engine-dev.yaml``) by exporting it into the env, or
pass ``--connection-string`` explicitly.

**This is non-destructive.** ``peek_messages`` looks at messages
without locking or completing them — nothing is consumed, the
backend's events stay in the subscription for the eventual real
consumer.  A topic holds no messages itself; messages live in the
*subscription* under it, which is why every read targets
``(topic, subscription)``.

Operator workflow::

    export AZURE_SERVICEBUS_CONNECTION_STRING='Endpoint=sb://…'
    .venv/bin/python scripts/peek_service_bus.py --max 3

Reading the output:

* A topic with ``peeked=N (>0)`` → the backend's events are
  flowing and visible to us; the printed body is the real schema.
* ``peeked=0`` for every topic → either nothing has been published
  yet, the messages have aged out (subscription TTL), or the
  backend routes to a different subscription/filter.  That is the
  one fact to confirm with the backend — not the wire format.
* ``ENTITY NOT FOUND`` → that topic or the subscription does not
  exist under this namespace; check the name with the backend.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azure.servicebus import ServiceBusClient, ServiceBusReceivedMessage

logger = logging.getLogger("sb_peek")

_DEFAULT_TOPICS: tuple[str, ...] = (
    "botel-property-sync",
    "botel-reservation-sync",
    "botel-conversation-sync",
    "botel-guest-sync",
    "botel-review-sync",
    "botel-rateplan-sync",
)
_DEFAULT_SUBSCRIPTION = "brain-engine-cascade"
_BODY_PREVIEW_CHARS = 800


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only peek into Service Bus topics.",
    )
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("AZURE_SERVICEBUS_CONNECTION_STRING", ""),
        help="Namespace connection string (or set the env var).",
    )
    parser.add_argument(
        "--subscription",
        default=os.environ.get(
            "AZURE_SERVICEBUS_SUBSCRIPTION", _DEFAULT_SUBSCRIPTION,
        ),
        help=f"Subscription to peek (default: {_DEFAULT_SUBSCRIPTION}).",
    )
    parser.add_argument(
        "--topics",
        nargs="+",
        default=list(_DEFAULT_TOPICS),
        help="Topics to peek (default: the six botel-*-sync topics).",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=5,
        help="Messages to peek per topic (default: 5).",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print field names only (PII-safe), not raw bodies.",
    )
    return parser.parse_args(argv)


def _body_text(message: ServiceBusReceivedMessage) -> str:
    """Decode a received message body to text, defensively.

    The SDK returns the body as ``bytes`` or as a generator of
    ``bytes`` depending on how the message was sent, so handle both
    and never raise on an odd payload — this is a diagnostic.
    """
    raw = message.body
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    else:
        try:
            data = b"".join(bytes(chunk) for chunk in raw)
        except TypeError:
            return str(raw)
    return data.decode("utf-8", errors="replace")


def _print_schema(message: ServiceBusReceivedMessage) -> None:
    """Print the envelope + inner DataJson field names only (PII-safe)."""
    import json

    try:
        envelope = json.loads(_body_text(message))
    except json.JSONDecodeError:
        logger.info("    (body is not JSON)")
        return
    if not isinstance(envelope, dict):
        logger.info("    envelope: %s", type(envelope).__name__)
        return
    logger.info("    envelope keys: %s", sorted(envelope))
    # Routing/tenant ids (not guest PII) — confirm they are populated
    # so a consumer knows whether the tenant tuple rides on the event.
    routing = {
        key: envelope.get(key)
        for key in (
            "ChannelEntityId",
            "CustomerId",
            "OrgId",
            "ProviderType",
            "CustomerChannelId",
        )
    }
    logger.info("    routing: %s", routing)
    inner_raw = envelope.get("DataJson")
    if not isinstance(inner_raw, str):
        return
    try:
        inner = json.loads(inner_raw)
    except json.JSONDecodeError:
        logger.info("    DataJson: (not JSON)")
        return
    if isinstance(inner, dict):
        logger.info("    DataJson keys: %s", sorted(inner))
    elif isinstance(inner, list) and inner and isinstance(inner[0], dict):
        logger.info("    DataJson[0] keys: %s", sorted(inner[0]))
    else:
        logger.info("    DataJson: %s", type(inner).__name__)


def _peek_topic(
    client: ServiceBusClient,
    *,
    topic: str,
    subscription: str,
    max_count: int,
    schema: bool,
) -> int:
    """Peek one topic's subscription; return the message count seen."""
    from azure.servicebus.exceptions import (
        MessagingEntityNotFoundError,
        ServiceBusError,
    )

    try:
        receiver = client.get_subscription_receiver(
            topic_name=topic,
            subscription_name=subscription,
        )
        with receiver:
            messages = receiver.peek_messages(max_message_count=max_count)
    except MessagingEntityNotFoundError:
        logger.warning("  [%s] ENTITY NOT FOUND (topic/sub missing)", topic)
        return 0
    except ServiceBusError as exc:
        logger.error("  [%s] Service Bus error: %s", topic, exc)
        return 0

    logger.info("  [%s] peeked=%d", topic, len(messages))
    for message in messages:
        logger.info(
            "    seq=%s id=%s enqueued=%s",
            message.sequence_number,
            message.message_id,
            message.enqueued_time_utc,
        )
        if schema:
            _print_schema(message)
        else:
            logger.info("    body=%s", _body_text(message)[:_BODY_PREVIEW_CHARS])
    return len(messages)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # The Azure AMQP transport logs every connection/link state change
    # at INFO; silence it so only our peek output shows.
    for noisy in ("azure", "uamqp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args = _parse_args(argv)

    if not args.connection_string:
        logger.error(
            "No connection string. Set "
            "AZURE_SERVICEBUS_CONNECTION_STRING or pass "
            "--connection-string.",
        )
        return 2

    try:
        from azure.servicebus import ServiceBusClient
    except ImportError:
        logger.error("azure-servicebus is not installed in this env.")
        return 2

    logger.info(
        "Peeking subscription %r across %d topic(s)...",
        args.subscription,
        len(args.topics),
    )
    total = 0
    with ServiceBusClient.from_connection_string(
        args.connection_string,
    ) as client:
        for topic in args.topics:
            total += _peek_topic(
                client,
                topic=topic,
                subscription=args.subscription,
                max_count=args.max,
                schema=args.schema,
            )

    logger.info("Done. %d message(s) seen across all topics.", total)
    if total == 0:
        logger.info(
            "Nothing peeked — confirm with the backend that events "
            "are published to subscription %r (or that they have not "
            "aged out).",
            args.subscription,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
