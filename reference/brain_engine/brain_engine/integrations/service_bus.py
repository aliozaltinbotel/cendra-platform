"""Azure Service Bus queue sender — thin async transport.

:class:`ServiceBusQueueSender` is the production implementation of
the ``QueueSender`` Protocol the bootstrap dispatcher depends on
(see :mod:`brain_engine.tenants.service_bus_dispatcher`).  It owns
one :class:`azure.servicebus.aio.ServiceBusClient` for the pod's
lifetime and publishes single messages with a caller-supplied
``MessageId``, which Azure's duplicate-detection window uses to drop
re-enqueues.

The client is created lazily on first send so importing this module
never requires a live connection string — a pod with the queue
disabled, or a unit test, can construct the sender without reaching
Azure.  The Azure SDK is imported lazily inside the methods for the
same reason and to match :mod:`workgraph_consumer`'s convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from azure.servicebus.aio import ServiceBusClient

__all__ = ["BOOTSTRAP_QUEUE", "ServiceBusQueueSender"]


logger = structlog.get_logger(__name__)


#: Queue every bootstrap intent is published to; the Stage 2 worker
#: consumes it.  Duplicate detection must be enabled on this queue
#: for the dispatcher's ``MessageId`` dedup to take effect.
BOOTSTRAP_QUEUE: str = "bootstrap-intents"


class ServiceBusQueueSender:
    """Send-only Service Bus client bound to one queue.

    Args:
        connection_string: Azure Service Bus namespace connection
            string (``Endpoint=sb://…;SharedAccessKey=…``).
        queue_name: Destination queue.
    """

    def __init__(
        self,
        *,
        connection_string: str,
        queue_name: str,
    ) -> None:
        self._connection_string = connection_string
        self.queue_name = queue_name
        self._client: ServiceBusClient | None = None

    def _ensure_client(self) -> ServiceBusClient:
        """Open the namespace client on first use (lazy)."""

        if self._client is None:
            from azure.servicebus.aio import ServiceBusClient

            self._client = ServiceBusClient.from_connection_string(
                self._connection_string,
            )
        return self._client

    async def send(self, *, message_id: str, body: str) -> None:
        """Publish one message with broker-side dedup on ``message_id``."""

        from azure.servicebus import ServiceBusMessage

        client = self._ensure_client()
        sender = client.get_queue_sender(queue_name=self.queue_name)
        async with sender:
            await sender.send_messages(
                ServiceBusMessage(body, message_id=message_id),
            )
        logger.debug(
            "service_bus.sent",
            queue=self.queue_name,
            message_id=message_id,
        )

    async def aclose(self) -> None:
        """Close the underlying client; a no-op if never opened."""

        if self._client is not None:
            await self._client.close()
            self._client = None
