"""Stage 2 bootstrap worker — drains ``bootstrap-intents`` and runs.

``python -m workers.bootstrap_worker`` is the out-of-process consumer
that closes architecture-doc gap P2: the heavy ``bootstrap_fast`` run
moves off the request path, so a burst of cold properties can no
longer starve the serving event loop and a pod restart no longer
loses in-flight work (the intent survives on the queue, the FSM lives
in Postgres).

Runtime shape (decided 2026-05-29, revised 2026-05-29 after KEDA was
found absent on the shared dev AKS): an **always-on Deployment in
namespace ``dev``**.  The worker long-polls the queue continuously
(``BOOTSTRAP_WORKER_CONTINUOUS=true``, the default) and drains each
batch as it arrives; SIGTERM flips a stop event for a graceful
shutdown between messages.  Setting the flag to ``false`` gives the
one-shot "drain then exit" behaviour the optional KEDA ScaledJob path
(`deploy/bootstrap-worker-scaledjob.yaml`) expects.  Long bootstraps
(up to the runner timeout) are protected by an :class:`AutoLockRenewer`
so the broker does not redeliver a message still being processed.

The pure loop (:func:`_drain` / :func:`_drain_loop`) is
transport-shaped but SDK-free, so it is unit-tested against a fake
receiver; the Azure client is wired only in :func:`run_worker`.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any, Final

import structlog

from brain_engine.integrations.service_bus import BOOTSTRAP_QUEUE
from workers.bootstrap_deps import WorkerContext, build_worker_context
from workers.bootstrap_message_handler import (
    BootstrapMessageHandler,
    Settlement,
)

__all__ = ["main", "run_worker"]


logger = structlog.get_logger(__name__)


_CONN_ENV: Final[str] = "AZURE_SERVICEBUS_CONNECTION_STRING"
_QUEUE_ENV: Final[str] = "BOOTSTRAP_WORKER_QUEUE"
_CONTINUOUS_ENV: Final[str] = "BOOTSTRAP_WORKER_CONTINUOUS"
_MAX_MESSAGES_ENV: Final[str] = "BOOTSTRAP_WORKER_MAX_MESSAGES"
_BATCH_ENV: Final[str] = "BOOTSTRAP_WORKER_RECEIVE_BATCH"
_WAIT_ENV: Final[str] = "BOOTSTRAP_WORKER_MAX_WAIT_SECONDS"
_IDLE_ENV: Final[str] = "BOOTSTRAP_WORKER_MAX_IDLE_RECEIVES"
_LOCK_RENEWAL_ENV: Final[str] = "BOOTSTRAP_WORKER_LOCK_RENEWAL_SECONDS"

_DEFAULT_MAX_MESSAGES: Final[int] = 50
_DEFAULT_BATCH: Final[int] = 1
_DEFAULT_WAIT_SECONDS: Final[int] = 5
_DEFAULT_IDLE_RECEIVES: Final[int] = 1
_LOCK_RENEWAL_BUFFER_SECONDS: Final[float] = 120.0
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


async def _drain(
    receiver: Any,
    handler: BootstrapMessageHandler,
    *,
    max_messages: int,
    receive_batch: int,
    max_wait_seconds: int,
    max_idle_receives: int,
    stop: asyncio.Event | None = None,
) -> dict[str, int]:
    """Receive, process, and settle until the queue drains or caps out.

    Returns a settlement tally for observability.  Works against any
    object exposing the Service Bus receiver surface
    (``receive_messages`` / ``complete_message`` / ``abandon_message``
    / ``dead_letter_message``), so tests inject a fake.  ``stop``, when
    set, ends the pass between messages for a responsive shutdown.
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
            settlement = await handler.handle(str(message))
            await _settle(receiver, message, settlement)
            tally[settlement.value] += 1
            processed += 1
            if processed >= max_messages or _stopping(stop):
                break
    logger.info("bootstrap_worker.drained", processed=processed, **tally)
    return tally


async def _drain_loop(
    receiver: Any,
    handler: BootstrapMessageHandler,
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
    drain-then-exit behaviour used by tests and the KEDA ScaledJob.
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
            reason="bootstrap_unprocessable",
            error_description="poison or missing property_state row",
        )
    else:  # ABANDON — broker redelivers, DLQs after max delivery count
        await receiver.abandon_message(message)


async def run_worker(
    context: WorkerContext,
    *,
    continuous: bool = True,
    stop: asyncio.Event | None = None,
) -> dict[str, int]:
    """Wire the Azure receiver around :func:`_drain_loop` and run it.

    Args:
        context: The assembled worker dependencies.
        continuous: Keep long-polling until ``stop`` is set (always-on
            Deployment); ``False`` drains once and returns (ScaledJob).
        stop: Shutdown signal; a fresh :class:`asyncio.Event` when not
            supplied (so the worker runs until the process is killed).

    Raises:
        RuntimeError: when no Service Bus connection string is set.
    """

    conn = os.environ.get(_CONN_ENV, "").strip()
    if not conn:
        raise RuntimeError(f"bootstrap worker requires {_CONN_ENV}")
    queue = os.environ.get(_QUEUE_ENV, "").strip() or BOOTSTRAP_QUEUE
    stop_event = stop if stop is not None else asyncio.Event()

    from azure.servicebus.aio import AutoLockRenewer, ServiceBusClient

    handler = BootstrapMessageHandler(
        pipeline=context.pipeline,
        state_store=context.state_store,
        timeout_seconds=context.timeout_seconds,
    )
    renewer = AutoLockRenewer(
        max_lock_renewal_duration=_lock_renewal_seconds(
            context.timeout_seconds,
        ),
    )
    logger.info(
        "bootstrap_worker.starting",
        queue=queue,
        continuous=continuous,
    )
    async with ServiceBusClient.from_connection_string(conn) as client:
        receiver = client.get_queue_receiver(
            queue_name=queue,
            # The async AutoLockRenewer is the correct runtime type;
            # the SDK stub for get_queue_receiver references the sync
            # one, so the keyword trips mypy despite working.
            auto_lock_renewer=renewer,  # type: ignore[arg-type]
        )
        async with receiver:
            return await _drain_loop(
                receiver,
                handler,
                continuous=continuous,
                stop=stop_event,
                max_messages=_env_int(
                    _MAX_MESSAGES_ENV, _DEFAULT_MAX_MESSAGES
                ),
                receive_batch=_env_int(_BATCH_ENV, _DEFAULT_BATCH),
                max_wait_seconds=_env_int(_WAIT_ENV, _DEFAULT_WAIT_SECONDS),
                max_idle_receives=_env_int(_IDLE_ENV, _DEFAULT_IDLE_RECEIVES),
            )


async def main() -> None:
    """Build the worker context, run the drain loop, and tear down."""

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    context = await build_worker_context()
    try:
        await run_worker(context, continuous=_continuous(), stop=stop)
    finally:
        await context.close()


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Flip ``stop`` on SIGTERM/SIGINT for a graceful shutdown.

    ``add_signal_handler`` is unavailable on some platforms (e.g.
    Windows) — there the worker simply relies on process termination,
    which is acceptable because the orphan reaper reclaims any row left
    in ``warming``.
    """

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            logger.warning("bootstrap_worker.signal_handler_unavailable")


def _stopping(stop: asyncio.Event | None) -> bool:
    """True when a stop event exists and has been set."""

    return stop is not None and stop.is_set()


def _continuous() -> bool:
    """Read the always-on flag from env (default ``True``)."""

    raw = os.environ.get(_CONTINUOUS_ENV, "true").strip().lower()
    return raw in _TRUTHY


def _lock_renewal_seconds(timeout_seconds: float | None) -> float:
    """Keep the message lock alive past the worst-case run length.

    An explicit ``BOOTSTRAP_WORKER_LOCK_RENEWAL_SECONDS`` wins; the
    default derives from the per-run ceiling plus a safety buffer so
    the broker never redelivers a message still being processed.
    """

    override = os.environ.get(_LOCK_RENEWAL_ENV, "").strip()
    if override:
        try:
            value = float(override)
        except ValueError:
            value = 0.0
        if value > 0:
            return value
    base = (
        timeout_seconds if timeout_seconds and timeout_seconds > 0 else 1200.0
    )
    return base + _LOCK_RENEWAL_BUFFER_SECONDS


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
