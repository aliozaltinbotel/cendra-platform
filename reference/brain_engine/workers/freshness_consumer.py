"""Stage 3 freshness consumer — turn ``botel-*-sync`` events into refreshes.

``python -m workers.freshness_consumer`` is the out-of-process consumer
for Stage 3 Track A (reactive freshness).  It subscribes to the backend
OTA change topics (``botel-reservation-sync`` / ``botel-conversation-sync``
on subscription ``brain-engine-cascade``) and, for each event, marks the
affected property stale and enqueues a small-window refresh onto the
Stage 2 ``bootstrap-intents`` queue — which the bootstrap worker drains.

This adds **no new execution path**: a refresh is just a new *trigger*
for the existing queue + worker.  Per-event work is light (one Postgres
update + one queue send), so — unlike the bootstrap worker — there is no
pipeline, no memory tiers, and no lock-renewer; the resources are small.

Runtime shape mirrors the bootstrap worker: an always-on Deployment in
namespace ``dev`` that long-polls each topic continuously until SIGTERM
flips a stop event for a graceful drain.  The pure loop (:func:`_drain`
/ :func:`_drain_loop`) is transport-shaped but SDK-free, so it is
unit-tested against a fake receiver; the Azure client is wired only in
:func:`run_consumer`.

Settlement (see :mod:`workers.freshness_message_handler`):

* **COMPLETE** — refresh enqueued, or the property is not a refresh
  candidate (cold / in-flight / unknown).
* **ABANDON** — transient infra fault; the broker redelivers, then
  dead-letters after the subscription's max delivery count.
* **DEAD_LETTER** — poison event (unparseable envelope).
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any, Final

import structlog

from workers.bootstrap_message_handler import Settlement
from workers.freshness_deps import FreshnessHandle, build_freshness_handler
from workers.freshness_message_handler import FreshnessMessageHandler

__all__ = ["main", "run_consumer"]


logger = structlog.get_logger(__name__)


_CONN_ENV: Final[str] = "AZURE_SERVICEBUS_CONNECTION_STRING"
_TOPICS_ENV: Final[str] = "FRESHNESS_TOPICS"
_SUBSCRIPTION_ENV: Final[str] = "FRESHNESS_SUBSCRIPTION"
_CONTINUOUS_ENV: Final[str] = "FRESHNESS_CONSUMER_CONTINUOUS"
_BATCH_ENV: Final[str] = "FRESHNESS_RECEIVE_BATCH"
_WAIT_ENV: Final[str] = "FRESHNESS_MAX_WAIT_SECONDS"
_IDLE_ENV: Final[str] = "FRESHNESS_MAX_IDLE_RECEIVES"
_MAX_MESSAGES_ENV: Final[str] = "FRESHNESS_MAX_MESSAGES"

_DEFAULT_TOPICS: Final[str] = "botel-reservation-sync,botel-conversation-sync"
_DEFAULT_SUBSCRIPTION: Final[str] = "brain-engine-cascade"
_DEFAULT_BATCH: Final[int] = 10
_DEFAULT_WAIT_SECONDS: Final[int] = 5
_DEFAULT_IDLE_RECEIVES: Final[int] = 1
_DEFAULT_MAX_MESSAGES: Final[int] = 200
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


async def _drain(
    receiver: Any,
    handler: FreshnessMessageHandler,
    *,
    max_messages: int,
    receive_batch: int,
    max_wait_seconds: int,
    max_idle_receives: int,
    stop: asyncio.Event | None = None,
) -> dict[str, int]:
    """Receive, process, and settle until the batch drains or caps out.

    Works against any object exposing the Service Bus receiver surface
    (``receive_messages`` / ``complete_message`` / ``abandon_message`` /
    ``dead_letter_message``), so tests inject a fake.  ``stop``, when
    set, ends the pass between messages for a responsive shutdown.
    Returns a settlement tally for observability.
    """

    tally = {s.value: 0 for s in Settlement}
    processed = 0
    idle = 0
    while processed < max_messages and not _stopping(stop):
        messages = await receiver.receive_messages(
            max_message_count=receive_batch,
            max_wait_time=max_wait_seconds,
        )
        if not messages:
            idle += 1
            if idle >= max_idle_receives:
                break
            continue
        idle = 0
        for message in messages:
            settlement = await handler.handle(
                str(message),
                enqueued_at=message.enqueued_time_utc,
            )
            await _settle(receiver, message, settlement)
            tally[settlement.value] += 1
            processed += 1
            if processed >= max_messages or _stopping(stop):
                break
    logger.info("freshness_consumer.drained", processed=processed, **tally)
    return tally


async def _drain_loop(
    receiver: Any,
    handler: FreshnessMessageHandler,
    *,
    continuous: bool,
    stop: asyncio.Event,
    max_messages: int,
    receive_batch: int,
    max_wait_seconds: int,
    max_idle_receives: int,
) -> dict[str, int]:
    """Run drain passes until ``stop`` is set, or just one pass.

    Continuous mode is the always-on Deployment behaviour: each empty
    ``receive_messages`` blocks up to ``max_wait_seconds`` so the loop
    long-polls rather than busy-spins, and it keeps going until SIGTERM
    sets ``stop``.  A single pass (``continuous=False``) is the
    drain-then-exit behaviour used by tests.
    """

    total = {s.value: 0 for s in Settlement}
    while not stop.is_set():
        pass_tally = await _drain(
            receiver,
            handler,
            max_messages=max_messages,
            receive_batch=receive_batch,
            max_wait_seconds=max_wait_seconds,
            max_idle_receives=max_idle_receives,
            stop=stop,
        )
        for key, value in pass_tally.items():
            total[key] += value
        if not continuous:
            break
    return total


async def _settle(receiver: Any, message: Any, settlement: Settlement) -> None:
    """Apply one settlement decision to the broker."""

    if settlement is Settlement.COMPLETE:
        await receiver.complete_message(message)
    elif settlement is Settlement.DEAD_LETTER:
        await receiver.dead_letter_message(
            message,
            reason="freshness_unprocessable",
            error_description="poison or unparseable OTA event body",
        )
    else:  # ABANDON — broker redelivers, DLQs after max delivery count
        await receiver.abandon_message(message)


async def run_consumer(
    handle: FreshnessHandle,
    *,
    continuous: bool = True,
    stop: asyncio.Event | None = None,
) -> dict[str, int]:
    """Wire the Azure receivers around :func:`_drain_loop` and run them.

    One subscription receiver is opened per configured topic and the
    drain loops run concurrently, so reservation and conversation events
    are consumed in parallel under the shared ``brain-engine-cascade``
    subscription.  The combined settlement tally is returned.

    Args:
        handle: The assembled consumer dependencies (handler + pools).
        continuous: Keep long-polling until ``stop`` is set (always-on
            Deployment); ``False`` drains once per topic and returns.
        stop: Shutdown signal; a fresh :class:`asyncio.Event` when not
            supplied.

    Raises:
        RuntimeError: when no Service Bus connection string is set.
    """

    conn = os.environ.get(_CONN_ENV, "").strip()
    if not conn:
        raise RuntimeError(f"freshness consumer requires {_CONN_ENV}")
    subscription = os.environ.get(
        _SUBSCRIPTION_ENV, ""
    ).strip() or _DEFAULT_SUBSCRIPTION
    topics = _topics()
    stop_event = stop if stop is not None else asyncio.Event()

    from azure.servicebus.aio import ServiceBusClient

    batch = _env_int(_BATCH_ENV, _DEFAULT_BATCH)
    wait = _env_int(_WAIT_ENV, _DEFAULT_WAIT_SECONDS)
    idle = _env_int(_IDLE_ENV, _DEFAULT_IDLE_RECEIVES)
    max_messages = _env_int(_MAX_MESSAGES_ENV, _DEFAULT_MAX_MESSAGES)

    logger.info(
        "freshness_consumer.starting",
        topics=topics,
        subscription=subscription,
        continuous=continuous,
    )
    async with ServiceBusClient.from_connection_string(conn) as client:
        tallies = await asyncio.gather(
            *[
                _run_topic(
                    client,
                    handle.handler,
                    topic=topic,
                    subscription=subscription,
                    continuous=continuous,
                    stop=stop_event,
                    receive_batch=batch,
                    max_wait_seconds=wait,
                    max_idle_receives=idle,
                    max_messages=max_messages,
                )
                for topic in topics
            ]
        )
    return _merge_tallies(tallies)


async def _run_topic(
    client: Any,
    handler: FreshnessMessageHandler,
    *,
    topic: str,
    subscription: str,
    continuous: bool,
    stop: asyncio.Event,
    receive_batch: int,
    max_wait_seconds: int,
    max_idle_receives: int,
    max_messages: int,
) -> dict[str, int]:
    """Open one subscription receiver and drain it until stopped."""

    receiver = client.get_subscription_receiver(
        topic_name=topic,
        subscription_name=subscription,
    )
    async with receiver:
        logger.info(
            "freshness_consumer.topic_open",
            topic=topic,
            subscription=subscription,
        )
        return await _drain_loop(
            receiver,
            handler,
            continuous=continuous,
            stop=stop,
            max_messages=max_messages,
            receive_batch=receive_batch,
            max_wait_seconds=max_wait_seconds,
            max_idle_receives=max_idle_receives,
        )


async def main() -> None:
    """Build deps, install signal handlers, run the loops, tear down."""

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    handle = await build_freshness_handler()
    try:
        await run_consumer(handle, continuous=_continuous(), stop=stop)
    finally:
        await handle.aclose()


def _topics() -> list[str]:
    """Topics to subscribe to (comma-separated ``FRESHNESS_TOPICS`` env)."""

    raw = os.environ.get(_TOPICS_ENV, "").strip() or _DEFAULT_TOPICS
    return [topic.strip() for topic in raw.split(",") if topic.strip()]


def _merge_tallies(tallies: list[dict[str, int]]) -> dict[str, int]:
    """Sum per-topic settlement tallies into one."""

    total = {s.value: 0 for s in Settlement}
    for tally in tallies:
        for key, value in tally.items():
            total[key] += value
    return total


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Flip ``stop`` on SIGTERM/SIGINT for a graceful shutdown.

    ``add_signal_handler`` is unavailable on some platforms; there the
    consumer relies on process termination, which is safe because every
    event is idempotent (broker dedup + the in-DB status guard).
    """

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            logger.warning("freshness_consumer.signal_handler_unavailable")


def _stopping(stop: asyncio.Event | None) -> bool:
    """True when a stop event exists and has been set."""

    return stop is not None and stop.is_set()


def _continuous() -> bool:
    """Read the always-on flag from env (default ``True``)."""

    raw = os.environ.get(_CONTINUOUS_ENV, "true").strip().lower()
    return raw in _TRUTHY


def _env_int(name: str, default: int) -> int:
    """Read a positive int env var, clamped to ``>= 1``."""

    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    asyncio.run(main())
