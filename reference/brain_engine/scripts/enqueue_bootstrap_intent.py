"""Enqueue one bootstrap intent — isolated Stage 2 worker smoke.

Lets an operator validate the Stage 2 worker end-to-end *before*
flipping the live ``BOOTSTRAP_QUEUE_ENABLED`` flag on the API.  It
drives the **real** producer path — no logic is re-implemented here:

  * ``request_bootstrap`` seeds the ``property_state`` row to
    ``queued`` (the worker only runs ``queued`` rows) and dispatches,
  * ``ServiceBusBootstrapDispatcher`` serialises the intent and puts
    it on the ``bootstrap-intents`` queue with the same daily dedup
    ``MessageId`` the API would use,
  * the deployed worker then loads the row and runs ``bootstrap_fast``,
    walking it ``queued → warming → primed``.

The in-process ``workload`` ``request_bootstrap`` expects is a no-op
here: the Service Bus dispatcher discards it (the real work runs in
the worker), so we never build a pipeline in this script.

**Modes**

* ``--dry-run`` — build the message and show the exact queue body +
  dedup key via a recording sender.  No Postgres, no network; safe to
  run anywhere (used to prove the tool offline).
* default — connect to Postgres (``DATABASE_URL`` /
  ``TENANT_REGISTRY_DATABASE_URL``) and Service Bus
  (``AZURE_SERVICEBUS_CONNECTION_STRING``), seed the row, and enqueue.

**This writes to dev** (a ``queued`` row + a queue message).  Point it
only at dev; never at prod.

Example::

    export AZURE_SERVICEBUS_CONNECTION_STRING='Endpoint=sb://…'
    export DATABASE_URL='postgresql://…/brain_engine'
    # PYTHONPATH=. so the script (run by path) can import brain_engine.
    PYTHONPATH=. .venv/bin/python scripts/enqueue_bootstrap_intent.py \\
        --property 323133 --customer <uuid> --provider HOSTAWAY \\
        --window-days 30 --reason ui_select

    # Offline check (no Postgres / Service Bus): add --dry-run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable

from brain_engine.integrations.service_bus import (
    BOOTSTRAP_QUEUE,
    ServiceBusQueueSender,
)
from brain_engine.tenants import (
    TENANT_SOURCE_MANUAL,
    TenantContext,
)
from brain_engine.tenants.bootstrap_intent import request_bootstrap
from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage
from brain_engine.tenants.service_bus_dispatcher import (
    ServiceBusBootstrapDispatcher,
)

logger = logging.getLogger("enqueue_intent")

_DB_URL_ENVS = ("TENANT_REGISTRY_DATABASE_URL", "DATABASE_URL")
_CONN_ENV = "AZURE_SERVICEBUS_CONNECTION_STRING"


class _RecordingSender:
    """Dry-run ``QueueSender``: records instead of sending."""

    def __init__(self, queue_name: str) -> None:
        self.queue_name = queue_name
        self.sent: list[tuple[str, str]] = []

    async def send(self, *, message_id: str, body: str) -> None:
        self.sent.append((message_id, body))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enqueue one bootstrap intent (Stage 2 smoke).",
    )
    parser.add_argument("--property", required=True, help="channel id")
    parser.add_argument("--customer", required=True, help="customer uuid")
    parser.add_argument("--provider", required=True, help="e.g. HOSTAWAY")
    parser.add_argument("--org", default=None, help="org uuid (optional)")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--reason", default="ui_select")
    parser.add_argument("--job-id", default=None, help="default: generated")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + show the message without Postgres/Service Bus.",
    )
    return parser.parse_args(argv)


def _noop_workload_factory() -> Callable[..., Callable[[], Awaitable[None]]]:
    """A workload the Service Bus dispatcher discards (work runs remote)."""

    def factory(_state: object, _job_id: str) -> Callable[[], Awaitable[None]]:
        async def _noop() -> None:
            return None

        return _noop

    return factory


async def _dry_run(args: argparse.Namespace) -> int:
    """Show the exact body + dedup key the producer would enqueue."""

    message = BootstrapIntentMessage(
        property_channel_id=args.property,
        customer_id=args.customer,
        provider_type=args.provider,
        window_days=args.window_days,
        reason=args.reason,
        job_id=args.job_id or "dry-run-job",
        org_id=args.org,
    )
    sender = _RecordingSender(BOOTSTRAP_QUEUE)
    dispatcher = ServiceBusBootstrapDispatcher(sender)
    await dispatcher.dispatch(
        property_channel_id=message.property_channel_id,
        job_id=message.job_id,
        workload=_noop_workload_factory()(None, message.job_id),
        intent=message,
    )
    message_id, body = sender.sent[0]
    logger.info("queue:      %s", BOOTSTRAP_QUEUE)
    logger.info("dedup id:   %s", message_id)
    logger.info("body:       %s", body)
    return 0


async def _live(args: argparse.Namespace) -> int:
    """Seed the queued row + enqueue via the real producer path."""

    conn = os.environ.get(_CONN_ENV, "").strip()
    if not conn:
        logger.error("set %s", _CONN_ENV)
        return 2
    db_url = next(
        (os.environ[e] for e in _DB_URL_ENVS if os.environ.get(e)),
        None,
    )
    if not db_url:
        logger.error("set one of %s", " / ".join(_DB_URL_ENVS))
        return 2

    import asyncpg

    from brain_engine.tenants import PostgresPropertyStateStore

    pool = await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=2)
    sender = ServiceBusQueueSender(
        connection_string=conn,
        queue_name=BOOTSTRAP_QUEUE,
    )
    try:
        state_store = PostgresPropertyStateStore(pool)
        dispatcher = ServiceBusBootstrapDispatcher(sender)
        tenant = TenantContext(
            customer_id=args.customer,
            org_id=args.org,
            provider_type=args.provider,
            property_channel_id=args.property,
            source=TENANT_SOURCE_MANUAL,
        )
        result = await request_bootstrap(
            property_channel_id=args.property,
            tenant=tenant,
            window_days=args.window_days,
            reason=args.reason,
            state_store=state_store,
            dispatcher=dispatcher,
            workload_factory=_noop_workload_factory(),
            job_id=args.job_id,
        )
        logger.info(
            "result: enqueued=%s status=%s reason=%s job_id=%s",
            result.enqueued,
            result.status,
            result.reason,
            getattr(result.state, "current_job_id", None),
        )
        if not result.enqueued:
            logger.info(
                "Not enqueued (dedup short-circuit) — pick a cold/stale "
                "property or clear its property_state row to re-smoke.",
            )
        return 0
    finally:
        await sender.aclose()
        await pool.close()


async def _main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("azure", "uamqp", "asyncpg"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args = _parse_args(argv)
    if args.dry_run:
        return await _dry_run(args)
    return await _live(args)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
