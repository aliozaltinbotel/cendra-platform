"""Stage 2 producer — enqueue bootstrap intents onto a queue.

:class:`ServiceBusBootstrapDispatcher` implements the
:class:`~brain_engine.tenants.bootstrap_intent.BootstrapDispatcher`
Protocol by serialising the :class:`BootstrapIntentMessage` onto a
broker queue and discarding the in-process workload.  The heavy
``bootstrap_fast`` run then happens out of process in the Stage 2
worker, so a burst of cold properties can never starve the serving
event loop — the failure mode that wedged a worker under the Stage 1
in-process dispatcher.

The dispatcher depends on a narrow :class:`QueueSender` Protocol
rather than the Azure SDK directly, so this domain module stays free
of infra imports and unit tests inject a fake sender.  The
Azure-backed sender lives in
:mod:`brain_engine.integrations.service_bus`; the composition root
(``api_server/bootstrap/multi_tenant.py``) wires the two together.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import structlog

from brain_engine.tenants.bootstrap_intent import BootstrapWorkload
from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage

__all__ = ["QueueSender", "ServiceBusBootstrapDispatcher"]


logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Return a tz-aware UTC ``datetime`` (the default clock)."""

    return datetime.now(UTC)


class QueueSender(Protocol):
    """Minimal send surface the dispatcher needs from a broker.

    Attributes:
        queue_name: Destination queue, exposed for observability
            logging only — the dispatcher never routes on it.
    """

    queue_name: str

    async def send(self, *, message_id: str, body: str) -> None:
        """Publish ``body`` with a broker-dedup ``message_id``."""
        ...


class ServiceBusBootstrapDispatcher:
    """Enqueue intents instead of running them in-process.

    Args:
        sender: Queue transport — the Azure-backed sender in
            production, a fake in tests.
        clock: Test seam returning the current UTC time, used only
            to derive the daily dedup bucket.
    """

    def __init__(
        self,
        sender: QueueSender,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._sender = sender
        self._clock = clock

    async def dispatch(
        self,
        *,
        property_channel_id: str,
        job_id: str,
        workload: BootstrapWorkload,
        intent: BootstrapIntentMessage,
    ) -> None:
        """Publish ``intent``; the in-process ``workload`` is unused.

        The workload is a coroutine bound to this pod's pipeline and
        cannot cross to the worker process — the worker rebuilds its
        own from the serialised message.  Accepting it here keeps a
        single ``BootstrapDispatcher`` Protocol for both execution
        models (asyncio in Stage 1, Service Bus in Stage 2).
        """

        del workload  # out-of-process execution; see docstring.
        message_id = self._dedup_key(intent)
        await self._sender.send(message_id=message_id, body=intent.to_json())
        logger.info(
            "bootstrap_intent.enqueued_service_bus",
            property_channel_id=property_channel_id,
            job_id=job_id,
            queue=self._sender.queue_name,
            dedup_key=message_id,
            window_days=intent.window_days,
            reason=intent.reason,
        )

    def _dedup_key(self, intent: BootstrapIntentMessage) -> str:
        """Stable per-day key → Service Bus ``MessageId`` dedup.

        Two intents for the same property + window on the same UTC
        day collapse to one queue message, so a double UI click or a
        middleware/endpoint race cannot enqueue twice even if both
        slip past the in-database status dedup.
        """

        day_bucket = self._clock().strftime("%Y-%m-%d")
        raw = (
            f"{intent.property_channel_id}:"
            f"{intent.window_days}:{day_bucket}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
