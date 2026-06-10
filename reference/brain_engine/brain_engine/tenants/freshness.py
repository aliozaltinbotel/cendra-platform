"""Reactive freshness — mark a property stale on new OTA data + refresh.

Stage 3 Track A.  When the backend signals new data for a property (a
fresh reservation or guest message lands on the OTA → Service Bus
events), the property's learned knowledge is now behind reality.  This
module is the domain layer the Stage 3 freshness consumer composes; it
adds **no new execution path** — a refresh is just a small-window
bootstrap routed through the Stage 2 queue + worker.

Two primitives:

* :func:`mark_stale` — flip a ``primed`` row to ``stale`` and record
  ``last_data_event_at``.  The stale transition is what re-opens a
  recently-primed property: the freshness dedup inside
  :func:`request_bootstrap` would otherwise skip it as
  ``primed_fresh``.
* :func:`submit_refresh_intent` — mark stale, then enqueue a delta
  bootstrap through the existing :func:`submit_bootstrap_intent`
  path, **only when the property was genuinely primed**.  A cold /
  never-bootstrapped / in-flight property is left to the full
  first-touch bootstrap path, so a webhook never turns it into a
  too-small delta pull or disturbs a running bootstrap.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog

from brain_engine.tenants.bootstrap_intent import (
    BootstrapDispatcher,
    BootstrapIntentResult,
)
from brain_engine.tenants.bootstrap_runner import submit_bootstrap_intent
from brain_engine.tenants.models import TenantContext
from brain_engine.tenants.property_state import (
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_STALE,
    PropertyState,
)
from brain_engine.tenants.property_state_store import PropertyStateStore

if TYPE_CHECKING:
    from brain_engine.onboarding.bootstrap_pipeline import (
        OnboardingBootstrapPipeline,
    )

__all__ = ["mark_stale", "submit_refresh_intent"]


logger = structlog.get_logger(__name__)


#: Delta look-back for a reactive refresh — small, because a primed
#: property only needs the slice of history since it was last warmed.
#: The full window stays with the cold first-touch bootstrap.
_DEFAULT_REFRESH_WINDOW_DAYS: Final[int] = 7

#: Observability tag for an OTA-event-driven refresh.
_REFRESH_REASON: Final[str] = "webhook"


async def mark_stale(
    state_store: PropertyStateStore,
    property_channel_id: str,
    *,
    event_at: datetime,
    now: datetime | None = None,
) -> PropertyState | None:
    """Flip a ``primed`` row to ``stale`` and record the OTA event time.

    Args:
        state_store: The ``property_state`` SSoT.
        property_channel_id: Short Cendra channel id.
        event_at: When the backend's source event occurred — stored on
            ``last_data_event_at`` for the nightly TTL sweep and for
            honest temporal anchoring downstream.
        now: Test seam for ``updated_at``; defaults to ``now(UTC)``.

    Returns:
        The updated row when a ``primed`` property was transitioned to
        ``stale``; the **unchanged** row when it is not ``primed``
        (in-flight ``queued`` / ``warming``, or not-yet-primed
        ``cold`` / ``failed`` / already ``stale`` rows are left
        untouched so a webhook never disturbs the FSM); ``None`` when
        the property has no state row yet.
    """

    row = await state_store.get(property_channel_id)
    if row is None:
        return None
    if row.status != PROPERTY_STATUS_PRIMED:
        logger.info(
            "freshness.mark_stale_skipped",
            property_channel_id=property_channel_id,
            status=row.status,
        )
        return row
    staled = dataclasses.replace(
        row,
        status=PROPERTY_STATUS_STALE,
        last_data_event_at=event_at,
        updated_at=now or datetime.now(UTC),
    )
    persisted = await state_store.update(staled)
    logger.info(
        "freshness.marked_stale",
        property_channel_id=property_channel_id,
        last_data_event_at=event_at.isoformat(),
    )
    return persisted


async def submit_refresh_intent(
    *,
    property_channel_id: str,
    tenant: TenantContext,
    pipeline: OnboardingBootstrapPipeline,
    state_store: PropertyStateStore,
    dispatcher: BootstrapDispatcher,
    event_at: datetime,
    window_days: int = _DEFAULT_REFRESH_WINDOW_DAYS,
    now: datetime | None = None,
) -> BootstrapIntentResult:
    """Mark stale on new OTA data, then enqueue a delta refresh.

    Composes :func:`mark_stale` + :func:`submit_bootstrap_intent` so the
    freshness consumer (Stage 3 A2) has one call per backend event.
    Enqueues **only** when the property was ``primed`` and is now
    ``stale``; otherwise returns a ``not_primed`` no-op result and the
    caller routes the property through the full first-touch bootstrap
    instead of a too-small delta.

    Returns:
        The :class:`BootstrapIntentResult` from the dedup path, or a
        ``not_primed`` result (``enqueued=False``) when the property is
        not a refresh candidate.
    """

    staled = await mark_stale(
        state_store,
        property_channel_id,
        event_at=event_at,
        now=now,
    )
    if staled is None or staled.status != PROPERTY_STATUS_STALE:
        return BootstrapIntentResult(
            enqueued=False,
            status=staled.status if staled is not None else "",
            state=staled,
            reason="not_primed",
        )
    return await submit_bootstrap_intent(
        property_channel_id=property_channel_id,
        tenant=tenant,
        pipeline=pipeline,
        state_store=state_store,
        dispatcher=dispatcher,
        window_days=window_days,
        reason=_REFRESH_REASON,
    )
