"""WorkGraph Consumer — consumes events from Azure Service Bus.

Subscribes to the work-events topic and dispatches events to
Brain Engine's learning and memory systems.

Config via environment variables:
    AZURE_SERVICEBUS_CONNECTION — Connection string
    WORKGRAPH_TOPIC            — Topic name (default: work-events)
    WORKGRAPH_SUBSCRIPTION     — Subscription (default: brain-engine)

Can run as:
- Background asyncio task within the FastAPI server
- Separate worker process
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from brain_engine.integrations.event_mapper import EventMapper, InternalAction
from brain_engine.integrations.event_models import WorkEventEnvelope

logger = logging.getLogger(__name__)


class WorkGraphConsumer:
    """Consumes WorkGraph events from Azure Service Bus.

    Args:
        connection_string: Service Bus connection string.
        topic_name: Topic name.
        subscription_name: Subscription name.
        event_mapper: EventMapper instance.
        on_action: Async callback for processed actions.
    """

    def __init__(
        self,
        connection_string: str = "",
        topic_name: str = "work-events",
        subscription_name: str = "brain-engine",
        event_mapper: EventMapper | None = None,
        on_action: Any | None = None,
    ) -> None:
        self._connection_string = (
            connection_string
            or os.environ.get("AZURE_SERVICEBUS_CONNECTION", "")
        )
        self._topic = (
            topic_name
            or os.environ.get("WORKGRAPH_TOPIC", "work-events")
        )
        self._subscription = (
            subscription_name
            or os.environ.get("WORKGRAPH_SUBSCRIPTION", "brain-engine")
        )
        self._mapper = event_mapper or EventMapper()
        self._on_action = on_action or self._default_handler
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._processed_count = 0
        self._error_count = 0

    @property
    def is_configured(self) -> bool:
        """Whether Service Bus credentials are available."""
        return bool(self._connection_string)

    @property
    def stats(self) -> dict[str, Any]:
        """Consumer statistics.

        Returns:
            Stats dict.
        """
        return {
            "running": self._running,
            "processed": self._processed_count,
            "errors": self._error_count,
            "topic": self._topic,
            "subscription": self._subscription,
        }

    async def start(self) -> None:
        """Start consuming messages in background."""
        if not self.is_configured:
            logger.warning(
                "WorkGraphConsumer: no connection string, skipping",
            )
            return
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info(
            "WorkGraphConsumer started: topic=%s, sub=%s",
            self._topic, self._subscription,
        )

    async def stop(self) -> None:
        """Stop consuming gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(
            "WorkGraphConsumer stopped: %d processed, %d errors",
            self._processed_count, self._error_count,
        )

    async def process_message(self, raw_body: str) -> InternalAction | None:
        """Process a single message (for testing or manual dispatch).

        Args:
            raw_body: JSON string of WorkEventEnvelope.

        Returns:
            InternalAction if mapped, None otherwise.
        """
        try:
            data = json.loads(raw_body)
            envelope = WorkEventEnvelope(**data)
        except (json.JSONDecodeError, Exception):
            logger.error("Failed to parse WorkEvent", exc_info=True)
            self._error_count += 1
            return None

        action = self._mapper.map(envelope)
        if action:
            await self._on_action(action)
            self._processed_count += 1
            logger.info(
                "WorkEvent processed: %s → %s",
                envelope.event_type, action.action_type,
            )
        return action

    async def _consume_loop(self) -> None:
        """Internal loop that reads from Azure Service Bus."""
        try:
            from azure.servicebus.aio import ServiceBusClient
        except ImportError:
            logger.error(
                "azure-servicebus not installed, consumer disabled",
            )
            self._running = False
            return

        async with ServiceBusClient.from_connection_string(
            self._connection_string,
        ) as client:
            receiver = client.get_subscription_receiver(
                topic_name=self._topic,
                subscription_name=self._subscription,
            )
            async with receiver:
                while self._running:
                    try:
                        messages = await receiver.receive_messages(
                            max_message_count=10,
                            max_wait_time=5,
                        )
                        for msg in messages:
                            body = str(msg)
                            await self.process_message(body)
                            await receiver.complete_message(msg)
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        logger.error(
                            "WorkGraph consume error", exc_info=True,
                        )
                        self._error_count += 1
                        await asyncio.sleep(5)

    @staticmethod
    async def _default_handler(action: InternalAction) -> None:
        """Default action handler — logs the action.

        Args:
            action: The internal action to handle.
        """
        logger.info(
            "WorkGraph action (no handler): %s → %s",
            action.action_type, action.target,
        )
