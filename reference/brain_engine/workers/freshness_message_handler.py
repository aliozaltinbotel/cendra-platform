"""Pure message → outcome logic for the freshness consumer (Stage 3).

Given one backend ``botel-*-sync`` change event, decide what the broker
should do with it and — when the affected property is genuinely primed
— enqueue a delta refresh.  No Azure SDK, no live pipeline: the handler
is a **producer**.  It marks the property stale and routes a
small-window bootstrap through the Stage 2 ``bootstrap-intents`` queue;
the Stage 2 worker runs the actual ``bootstrap_fast``.

Settlement contract (mirrors the bootstrap worker):

* **DEAD_LETTER** — the event body is unparseable (poison); redelivery
  would never help.
* **COMPLETE** — handled: either a refresh was enqueued (primed → stale
  → queued), or there was nothing to do (the property is cold /
  in-flight / unknown, so it is not a refresh candidate — the full
  first-touch path owns those).
* **ABANDON** — a transient fault (Postgres / Service Bus) escaped; the
  broker redelivers.

The handler does **not** use :func:`submit_refresh_intent` (A1) because
that pulls in a bootstrap pipeline for in-process callers; the
standalone consumer is pipeline-free and composes :func:`mark_stale`
with :func:`request_bootstrap` directly through a no-op workload (the
Service Bus dispatcher discards it).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import structlog

from brain_engine.integrations.ota_event import parse_ota_event
from brain_engine.tenants import (
    PROPERTY_STATUS_STALE,
    TENANT_SOURCE_SYNC,
    BootstrapDispatcher,
    PropertyState,
    PropertyStateStore,
    TenantContext,
    mark_stale,
    request_bootstrap,
)
from brain_engine.tenants.bootstrap_intent import BootstrapWorkload
from workers.bootstrap_message_handler import Settlement

__all__ = ["FreshnessMessageHandler"]


logger = structlog.get_logger(__name__)

_REFRESH_REASON = "webhook"


def _noop_workload_factory() -> (
    Callable[[PropertyState, str], BootstrapWorkload]
):
    """A workload the Service Bus dispatcher discards (work runs remote)."""

    def factory(_state: PropertyState, _job_id: str) -> BootstrapWorkload:
        async def _noop() -> None:
            return None

        return _noop

    return factory


class FreshnessMessageHandler:
    """Turn one OTA change event into a broker settlement + refresh.

    Args:
        state_store: The ``property_state`` SSoT (shared with the API
            and the bootstrap worker).
        dispatcher: The Service Bus producer that enqueues the refresh
            intent onto ``bootstrap-intents``.
        window_days: Delta look-back for the refresh bootstrap.
    """

    def __init__(
        self,
        *,
        state_store: PropertyStateStore,
        dispatcher: BootstrapDispatcher,
        window_days: int = 7,
    ) -> None:
        self._state_store = state_store
        self._dispatcher = dispatcher
        self._window_days = window_days

    async def handle(
        self,
        body: str,
        *,
        enqueued_at: datetime,
    ) -> Settlement:
        """Process one change event; never raises."""

        try:
            event = parse_ota_event(body, enqueued_at=enqueued_at)
        except ValueError as exc:
            logger.warning("freshness.poison_event", error=str(exc))
            return Settlement.DEAD_LETTER

        log = logger.bind(
            property_channel_id=event.property_channel_id,
            entity_id=event.entity_id,
            provider_type=event.provider_type,
        )
        try:
            staled = await mark_stale(
                self._state_store,
                event.property_channel_id,
                event_at=event.event_at,
            )
            if staled is None or staled.status != PROPERTY_STATUS_STALE:
                # Cold / in-flight / unknown property — not a refresh
                # candidate.  Drop the event; the full first-touch path
                # owns those.
                log.info(
                    "freshness.skip_not_refresh_candidate",
                    status=staled.status if staled is not None else "absent",
                )
                return Settlement.COMPLETE

            tenant = TenantContext(
                customer_id=event.customer_id,
                org_id=event.org_id,
                provider_type=event.provider_type,
                property_channel_id=event.property_channel_id,
                source=TENANT_SOURCE_SYNC,
            )
            result = await request_bootstrap(
                property_channel_id=event.property_channel_id,
                tenant=tenant,
                window_days=self._window_days,
                reason=_REFRESH_REASON,
                state_store=self._state_store,
                dispatcher=self._dispatcher,
                workload_factory=_noop_workload_factory(),
            )
            log.info(
                "freshness.refresh_dispatched",
                enqueued=result.enqueued,
                status=result.status,
                reason=result.reason,
            )
            return Settlement.COMPLETE
        except Exception as exc:  # transient infra fault → redeliver
            log.warning(
                "freshness.handler_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return Settlement.ABANDON
