"""Tests for the Azure-backed :class:`ServiceBusQueueSender`.

These assert the glue between our send surface and the Azure SDK
without a live broker: the client is created lazily, ``send`` builds
a ``ServiceBusMessage`` carrying the dedup ``MessageId`` and routes
it to the configured queue, and ``aclose`` releases the client (but
is a no-op when the client was never opened).
"""

from __future__ import annotations

import pytest

pytest.importorskip("azure.servicebus")

from brain_engine.integrations.service_bus import (
    ServiceBusQueueSender,
)

_FROM_CONN = "azure.servicebus.aio.ServiceBusClient.from_connection_string"
_MESSAGE = "azure.servicebus.ServiceBusMessage"


class _FakeMessage:
    def __init__(self, body: str, *, message_id: str | None = None) -> None:
        self.body = body
        self.message_id = message_id


class _FakeQueueSender:
    def __init__(self) -> None:
        self.messages: list[_FakeMessage] = []

    async def __aenter__(self) -> _FakeQueueSender:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def send_messages(self, message: _FakeMessage) -> None:
        self.messages.append(message)


class _FakeClient:
    def __init__(self) -> None:
        self.queue_senders: dict[str, _FakeQueueSender] = {}
        self.closed = False

    def get_queue_sender(self, queue_name: str) -> _FakeQueueSender:
        return self.queue_senders.setdefault(queue_name, _FakeQueueSender())

    async def close(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(_FROM_CONN, lambda conn: client)
    monkeypatch.setattr(_MESSAGE, _FakeMessage)
    return client


async def test_send_publishes_message_with_dedup_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _install(monkeypatch)
    sender = ServiceBusQueueSender(
        connection_string="cs", queue_name="bootstrap-intents",
    )

    await sender.send(message_id="dedup-1", body='{"x":1}')

    queue_sender = client.queue_senders["bootstrap-intents"]
    assert len(queue_sender.messages) == 1
    message = queue_sender.messages[0]
    assert message.message_id == "dedup-1"
    assert message.body == '{"x":1}'


async def test_send_routes_to_configured_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _install(monkeypatch)
    sender = ServiceBusQueueSender(
        connection_string="cs", queue_name="my-queue",
    )

    await sender.send(message_id="m", body="b")

    assert "my-queue" in client.queue_senders


async def test_client_created_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _install(monkeypatch)
    sender = ServiceBusQueueSender(
        connection_string="cs", queue_name="q",
    )
    # No send yet → the client must not have been built.
    assert not client.queue_senders

    await sender.send(message_id="m", body="b")
    assert client.queue_senders


async def test_aclose_closes_opened_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _install(monkeypatch)
    sender = ServiceBusQueueSender(
        connection_string="cs", queue_name="q",
    )
    await sender.send(message_id="m", body="b")

    await sender.aclose()

    assert client.closed is True


async def test_aclose_is_noop_when_unused() -> None:
    sender = ServiceBusQueueSender(
        connection_string="cs", queue_name="q",
    )
    # Never sent → no client → must not raise.
    await sender.aclose()
