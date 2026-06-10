"""Tests for the Stage 2 Service Bus bootstrap dispatcher.

The dispatcher is the producer half of Stage 2: it serialises the
intent onto a queue and discards the in-process workload (which is
bound to this pod's pipeline and cannot reach the worker process).
The tests pin exactly that contract — the workload never runs, the
queue body is the serialised intent, and the dedup ``MessageId`` is
stable per ``(property, window, UTC-day)`` so duplicate enqueues
collapse at the broker but distinct requests do not.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from brain_engine.tenants import (
    BootstrapIntentMessage,
    ServiceBusBootstrapDispatcher,
)


class _FakeSender:
    """Records sends; structurally satisfies ``QueueSender``."""

    queue_name = "bootstrap-intents"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, *, message_id: str, body: str) -> None:
        self.sent.append((message_id, body))


def _message(
    *,
    property_channel_id: str = "598808",
    window_days: int = 730,
) -> BootstrapIntentMessage:
    return BootstrapIntentMessage(
        property_channel_id=property_channel_id,
        customer_id="cust",
        provider_type="HOSTAWAY",
        window_days=window_days,
        reason="ui_select",
        job_id="job-1",
    )


def _clock(value: datetime) -> Callable[[], datetime]:
    return lambda: value


async def _dispatch(
    dispatcher: ServiceBusBootstrapDispatcher,
    intent: BootstrapIntentMessage,
) -> None:
    async def _noop() -> None:
        return None

    await dispatcher.dispatch(
        property_channel_id=intent.property_channel_id,
        job_id=intent.job_id,
        workload=_noop,
        intent=intent,
    )


async def test_dispatch_sends_serialised_intent() -> None:
    sender = _FakeSender()
    dispatcher = ServiceBusBootstrapDispatcher(sender)
    intent = _message()

    await _dispatch(dispatcher, intent)

    assert len(sender.sent) == 1
    _, body = sender.sent[0]
    assert BootstrapIntentMessage.from_json(body) == intent


async def test_dispatch_does_not_run_workload() -> None:
    sender = _FakeSender()
    dispatcher = ServiceBusBootstrapDispatcher(sender)
    ran = False

    async def workload() -> None:
        nonlocal ran
        ran = True

    await dispatcher.dispatch(
        property_channel_id="598808",
        job_id="job-1",
        workload=workload,
        intent=_message(),
    )

    assert ran is False
    assert len(sender.sent) == 1


async def test_dedup_key_stable_within_a_day() -> None:
    sender = _FakeSender()
    morning = ServiceBusBootstrapDispatcher(
        sender, clock=_clock(datetime(2026, 5, 28, 9, 0, tzinfo=UTC)),
    )
    midnight = ServiceBusBootstrapDispatcher(
        sender, clock=_clock(datetime(2026, 5, 28, 23, 59, tzinfo=UTC)),
    )

    await _dispatch(morning, _message())
    await _dispatch(midnight, _message())

    assert sender.sent[0][0] == sender.sent[1][0]


async def test_dedup_key_differs_across_days() -> None:
    sender = _FakeSender()
    day1 = ServiceBusBootstrapDispatcher(
        sender, clock=_clock(datetime(2026, 5, 28, tzinfo=UTC)),
    )
    day2 = ServiceBusBootstrapDispatcher(
        sender, clock=_clock(datetime(2026, 5, 29, tzinfo=UTC)),
    )

    await _dispatch(day1, _message())
    await _dispatch(day2, _message())

    assert sender.sent[0][0] != sender.sent[1][0]


async def test_dedup_key_differs_across_window() -> None:
    sender = _FakeSender()
    dispatcher = ServiceBusBootstrapDispatcher(
        sender, clock=_clock(datetime(2026, 5, 28, tzinfo=UTC)),
    )

    await _dispatch(dispatcher, _message(window_days=730))
    await _dispatch(dispatcher, _message(window_days=30))

    assert sender.sent[0][0] != sender.sent[1][0]


async def test_dedup_key_differs_across_property() -> None:
    sender = _FakeSender()
    dispatcher = ServiceBusBootstrapDispatcher(
        sender, clock=_clock(datetime(2026, 5, 28, tzinfo=UTC)),
    )

    await _dispatch(dispatcher, _message(property_channel_id="111"))
    await _dispatch(dispatcher, _message(property_channel_id="222"))

    assert sender.sent[0][0] != sender.sent[1][0]
