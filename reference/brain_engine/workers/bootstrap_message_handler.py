"""Pure message → outcome logic for the bootstrap worker.

This module holds the decision the worker makes for a single queue
message, with **no** Azure SDK and **no** transport coupling: given a
raw message body, the bootstrap pipeline, and the ``property_state``
SSoT, :class:`BootstrapMessageHandler` decides whether the broker
should *complete*, *abandon*, or *dead-letter* the message.  Keeping
it transport-free is what makes the worker's core testable with a
fake store + fake pipeline instead of a live Service Bus.

The handler reuses the Stage 1
:class:`~brain_engine.tenants.bootstrap_runner.BootstrapRunner` to
drive the ``queued → warming → primed/failed`` state machine, so the
out-of-process path runs the *same* transitions the in-process
asyncio dispatcher does — only the trigger differs (a queue message
instead of ``asyncio.create_task``).  See arch doc §3.5.

Settlement contract:

* **DEAD_LETTER** — the message is unprocessable and redelivering it
  would never help: a malformed body (``from_json`` raised) or an
  intent whose ``property_state`` row does not exist.  Routed to the
  dead-letter sub-queue so a human can inspect it.
* **COMPLETE** — work ran to a terminal state (``primed`` on success,
  ``failed`` recorded on a pipeline error), *or* the row was already
  past ``queued`` (a duplicate delivery / the Stage 1 in-process path
  beat us to it).  Either way the message is done.
* **ABANDON** — an infrastructure fault escaped the runner (e.g. the
  Postgres write for the ``warming`` transition failed).  The broker
  redelivers with back-off and dead-letters after the queue's max
  delivery count, matching the retry intent of arch doc §3.5.
"""

from __future__ import annotations

import enum

import structlog

from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage
from brain_engine.tenants.bootstrap_runner import BootstrapRunner
from brain_engine.tenants.models import TENANT_SOURCE_SYNC, TenantContext
from brain_engine.tenants.property_state import PROPERTY_STATUS_QUEUED
from brain_engine.tenants.property_state_store import PropertyStateStore

__all__ = ["BootstrapMessageHandler", "Settlement"]


logger = structlog.get_logger(__name__)


class Settlement(enum.Enum):
    """How the broker should settle a processed message."""

    COMPLETE = "complete"
    ABANDON = "abandon"
    DEAD_LETTER = "dead_letter"


class BootstrapMessageHandler:
    """Turn one queue message into a broker settlement decision.

    Args:
        pipeline: The bootstrap pipeline the runner drives.  Typed
            loosely (``object``) so this module never imports the
            heavy pipeline class — the runner is the only thing that
            calls into it, and it duck-types ``bootstrap_fast``.
        state_store: The shared ``property_state`` SSoT (the same
            Postgres table the server's producer writes to).
        timeout_seconds: Hard ceiling forwarded to the runner for the
            single ``bootstrap_fast`` call; ``None`` disables it.
    """

    def __init__(
        self,
        *,
        pipeline: object,
        state_store: PropertyStateStore,
        timeout_seconds: float | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._state_store = state_store
        self._timeout_seconds = timeout_seconds

    async def handle(self, body: str) -> Settlement:
        """Process one message body and return its settlement.

        Never raises: every failure mode maps to a settlement so the
        caller's receive loop stays a thin transport shim.
        """

        try:
            intent = BootstrapIntentMessage.from_json(body)
        except ValueError as exc:
            logger.warning("bootstrap_worker.poison_message", error=str(exc))
            return Settlement.DEAD_LETTER

        log = logger.bind(
            property_channel_id=intent.property_channel_id,
            job_id=intent.job_id,
            reason=intent.reason,
        )

        try:
            row = await self._state_store.get(intent.property_channel_id)
        except Exception as exc:  # transient read fault → redeliver
            log.warning(
                "bootstrap_worker.state_read_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return Settlement.ABANDON

        if row is None:
            # The producer flips the row to ``queued`` *before* it
            # enqueues, so a missing row is not a race — the intent
            # points at a property the SSoT never recorded.  Redeliver
            # would never fix it; dead-letter for inspection.
            log.warning("bootstrap_worker.row_missing")
            return Settlement.DEAD_LETTER

        if row.status != PROPERTY_STATUS_QUEUED:
            # Idempotency guard: a redelivery, or the Stage 1
            # in-process path, already moved this row past ``queued``.
            # Re-running would double-bootstrap, so drop the duplicate.
            log.info("bootstrap_worker.skip_not_queued", status=row.status)
            return Settlement.COMPLETE

        # ``source`` is required by the model but never persisted by the
        # runner (it forwards only customer/org/provider as bootstrap
        # overrides), so ``sync`` simply tags "consumed from the queue".
        tenant = TenantContext(
            customer_id=intent.customer_id,
            org_id=intent.org_id,
            provider_type=intent.provider_type,
            property_channel_id=intent.property_channel_id,
            source=TENANT_SOURCE_SYNC,
        )
        runner = BootstrapRunner(
            pipeline=self._pipeline,  # type: ignore[arg-type]
            state_store=self._state_store,
            timeout_seconds=self._timeout_seconds,
        )
        workload = runner.workload_factory(tenant, intent.window_days)(
            row,
            intent.job_id,
        )
        try:
            await workload()
        except Exception as exc:  # infra fault escaped the runner
            # The runner records pipeline failures on the row and
            # returns normally; an exception reaching here is an
            # infrastructure fault (e.g. the ``warming`` DB write).
            # Abandon so the broker redelivers and eventually
            # dead-letters after the queue's max delivery count.
            log.warning(
                "bootstrap_worker.workload_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return Settlement.ABANDON

        return Settlement.COMPLETE
