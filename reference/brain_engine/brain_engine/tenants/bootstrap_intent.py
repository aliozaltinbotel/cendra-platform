"""Central enqueue surface for property bootstrap intents.

`request_bootstrap()` is the one place every caller (Sandbox UI
warmup endpoint, Phase 4 middleware trigger, future webhook
consumer) goes through to ask the engine to warm up a property.
Three layers of dedup decide whether real work runs:

1. **Status layer.** If `property_state.status` is already
   ``primed`` and the row is not stale, return without enqueuing.
2. **In-flight layer.** If the status is ``queued`` or
   ``warming``, another caller is already on it — return that
   status, do not start a duplicate.
3. **Insert race layer.** Two callers that both pass layers 1/2
   race on `create_if_absent`; the loser observes the winner's
   row through the same `PropertyStateStore` and bails out.

Work execution is **delegated** through a `BootstrapDispatcher`
Protocol — Stage 1 ships an `AsyncioBootstrapDispatcher` that
schedules via `asyncio.create_task`; Stage 2 will swap in a
Service Bus implementation without changing this module's
public contract.

Why split this out of `auto_bootstrap.py`:

* The Phase 4 trigger is one of three callers — keeping the
  shared dedup logic in its own module means the new UI
  endpoint and the future webhook handler can call it without
  importing the trigger.
* The file size discipline (~300 lines max per module) is
  easier to hold when public surface (intent + dispatcher) and
  background execution (runner) live in separate files.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

import structlog

from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage
from brain_engine.tenants.models import TenantContext
from brain_engine.tenants.property_state import (
    PROPERTY_STATUS_COLD,
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_STALE,
    PROPERTY_STATUS_WARMING,
    PropertyState,
)
from brain_engine.tenants.property_state_store import PropertyStateStore

__all__ = [
    "AsyncioBootstrapDispatcher",
    "BootstrapDispatcher",
    "BootstrapIntentMessage",
    "BootstrapIntentResult",
    "BootstrapWorkload",
    "request_bootstrap",
]


logger = structlog.get_logger(__name__)


#: Default freshness window — a ``primed`` row newer than this is
#: a no-op for new intents.  Tunable through the
#: ``fresh_window`` argument of :func:`request_bootstrap`.
_DEFAULT_FRESH_WINDOW: Final[timedelta] = timedelta(days=1)


#: Type of the awaitable a dispatcher receives.  The intent layer
#: never *runs* the workload itself — it hands the dispatcher a
#: zero-argument callable returning an Awaitable, so the
#: dispatcher chooses the execution model (asyncio task / Service
#: Bus message / synchronous inline).
BootstrapWorkload = Callable[[], Awaitable[None]]


class BootstrapDispatcher(Protocol):
    """How to launch a bootstrap workload."""

    async def dispatch(
        self,
        *,
        property_channel_id: str,
        job_id: str,
        workload: BootstrapWorkload,
        intent: BootstrapIntentMessage,
    ) -> None:
        """Schedule a bootstrap for ``property_channel_id``.

        Implementations choose the execution model from the two
        equivalent descriptions of the same work they receive:

        * ``workload`` — a zero-argument coroutine bound to this
          pod's pipeline.  In-process dispatchers (asyncio, inline
          test) run it directly.
        * ``intent`` — the same request as a serialisable message.
          Out-of-process dispatchers (Service Bus) put it on a
          queue and discard ``workload``, since a coroutine cannot
          cross the process boundary to the worker.

        They may return as soon as the work is queued (Service Bus,
        asyncio task) or after completion (inline for tests).
        """
        ...


class AsyncioBootstrapDispatcher:
    """Default dispatcher — `asyncio.create_task` fire-and-forget.

    Stage 1 keeps the legacy execution model so the rollout is
    purely an SSoT/dedup change.  The task captures its own
    reference so Python's task GC does not collect it before
    completion — same trick used by the legacy Phase 4 trigger.
    """

    def __init__(self, *, max_concurrency: int = 0) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        # ``0`` / negative ⇒ unbounded (the legacy behaviour).  A
        # positive cap bounds how many in-process bootstraps run at
        # once so a burst of cold properties cannot saturate the
        # serving event loop — a Stage 1 safeguard until the heavy
        # work moves to an out-of-process worker (Stage 2).
        self._semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(max_concurrency) if max_concurrency > 0 else None
        )

    async def dispatch(
        self,
        *,
        property_channel_id: str,
        job_id: str,
        workload: BootstrapWorkload,
        intent: BootstrapIntentMessage,
    ) -> None:
        del intent  # in-process execution runs the live workload.
        runnable = (
            workload if self._semaphore is None else self._guarded(workload)
        )
        task = asyncio.create_task(
            runnable(),
            name=f"bootstrap-{property_channel_id}-{job_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _guarded(self, workload: BootstrapWorkload) -> BootstrapWorkload:
        semaphore = self._semaphore
        assert semaphore is not None

        async def _run() -> None:
            async with semaphore:
                await workload()

        return _run


@dataclass(frozen=True, slots=True)
class BootstrapIntentResult:
    """Outcome of one :func:`request_bootstrap` invocation.

    Attributes:
        enqueued: ``True`` iff the dispatcher was invoked — i.e.
            a new bootstrap workload is now in flight.  ``False``
            on every dedup short-circuit (primed-fresh, already
            queued/warming, lost insert race).
        status: The status of the row at exit.  Always one of
            :data:`PROPERTY_STATUS_*`.
        state: The :class:`PropertyState` row observed at exit,
            or ``None`` when the intent could not be recorded
            (rare — only when tenant/property identifiers are
            missing).
        reason: Short observability tag — never user-visible.
            Values: ``new``, ``primed_fresh``, ``in_flight``,
            ``invalid_input``.
    """

    enqueued: bool
    status: str
    state: PropertyState | None
    reason: str


def _is_fresh(
    state: PropertyState,
    fresh_window: timedelta,
    now: datetime,
) -> bool:
    """Return True when a primed row is younger than the window."""
    if state.last_bootstrap_at is None:
        # Primed-without-bootstrap-timestamp is a programming
        # bug, but treat as stale so the next intent re-runs
        # rather than masking the bug behind a silent skip.
        return False
    return (now - state.last_bootstrap_at) < fresh_window


async def request_bootstrap(
    *,
    property_channel_id: str,
    tenant: TenantContext,
    window_days: int,
    reason: str,
    state_store: PropertyStateStore,
    dispatcher: BootstrapDispatcher,
    workload_factory: Callable[[PropertyState, str], BootstrapWorkload],
    already_primed_probe: Callable[[str], Awaitable[bool]] | None = None,
    profile_exists_probe: Callable[[str], Awaitable[bool]] | None = None,
    job_id: str | None = None,
    fresh_window: timedelta = _DEFAULT_FRESH_WINDOW,
    now: datetime | None = None,
) -> BootstrapIntentResult:
    """Record an intent + dispatch work iff the property is cold/stale.

    Args:
        property_channel_id: Short Cendra channel id.
        tenant: Resolved tenant for the property.
        window_days: Archive look-back the workload should pull.
        reason: One of ``ui_select`` / ``first_touch`` /
            ``stale_refresh`` / ``webhook`` — recorded in logs
            for observability pivots.
        state_store: SSoT contract for the row.
        dispatcher: Pluggable execution model (asyncio in Stage
            1, Service Bus in Stage 2).
        workload_factory: Builds the actual bootstrap workload
            from the observed state + job id.  Decoupling the
            factory from this module keeps the runner module
            invisible to the intent layer — tests can pass a
            no-op factory and assert dedup semantics without
            standing up a pipeline.
        already_primed_probe: Optional async predicate answering
            "does this property already have a built profile?".
            When a freshly-seeded ``cold`` row matches it, the
            property was primed by a pre-SSoT bootstrap; we adopt
            it as ``primed`` instead of re-running the heavy
            pipeline.  This is what prevents a re-bootstrap storm
            when ``property_state`` is empty but profiles exist.
        profile_exists_probe: Optional async predicate answering
            "does this property have a built profile right now?".
            Guards Layer 1: a ``primed`` + fresh row whose profile is
            actually missing (state outlived the profile) is treated
            as eligible and re-enqueued instead of short-circuiting,
            so the property self-heals.  When ``None`` the legacy
            status-only short-circuit is kept (backward compatible).
        job_id: Optional caller-supplied id.  Generated via
            :mod:`uuid` when omitted.
        fresh_window: A ``primed`` row newer than this is treated
            as no-op.  Defaults to one day.
        now: Test seam.  Defaults to ``datetime.now(UTC)``.
    """

    if not property_channel_id:
        return BootstrapIntentResult(
            enqueued=False,
            status="",
            state=None,
            reason="invalid_input",
        )
    if not tenant.customer_id:
        return BootstrapIntentResult(
            enqueued=False,
            status="",
            state=None,
            reason="invalid_input",
        )

    now_ts = now or datetime.now(UTC)
    resolved_job_id = job_id or _generate_job_id()

    # Layer 3 (insert race): create_if_absent returns the
    # winner's row; the loser observes the existing status and
    # short-circuits.  Doing this BEFORE layers 1/2 means we
    # always seed a row for cold properties even when the caller
    # immediately observes "primed" for whatever reason.
    seed = PropertyState(
        property_channel_id=property_channel_id,
        customer_id=tenant.customer_id,
        provider_type=tenant.provider_type,
        org_id=tenant.org_id,
        status=PROPERTY_STATUS_COLD,
        first_seen_at=now_ts,
        updated_at=now_ts,
    )
    observed = await state_store.create_if_absent(seed)

    # Layer 0 (adopt): a freshly-seeded cold row for a property
    # that already has a built profile was primed by a pre-SSoT
    # bootstrap.  Adopt it as primed instead of re-running the
    # heavy pipeline — this is what stops a re-bootstrap storm
    # when property_state is empty but profiles already exist.
    if (
        observed.status == PROPERTY_STATUS_COLD
        and already_primed_probe is not None
        and await already_primed_probe(property_channel_id)
    ):
        adopted = dataclasses.replace(
            observed,
            status=PROPERTY_STATUS_PRIMED,
            last_bootstrap_at=now_ts,
            updated_at=now_ts,
        )
        persisted = await state_store.update(adopted)
        logger.info(
            "bootstrap_intent.adopted_existing",
            property_channel_id=property_channel_id,
            customer_id=tenant.customer_id,
            reason=reason,
        )
        return BootstrapIntentResult(
            enqueued=False,
            status=PROPERTY_STATUS_PRIMED,
            state=persisted,
            reason="adopted_existing",
        )

    # Layer 1 (status): primed + fresh = no-op — but ONLY when the
    # built profile actually exists.  ``property_state`` (Postgres) can
    # outlive the profile (e.g. an InMemory PropertyProfileStore reset on
    # pod restart), leaving a primed+fresh row whose profile is gone.
    # Trusting the status alone would block re-harvest forever and the
    # agent would defer every answer ("no data, ask PM").  When a profile
    # probe is supplied and reports the profile missing, fall through to
    # re-enqueue so the property self-heals.
    if observed.status == PROPERTY_STATUS_PRIMED and _is_fresh(
        observed, fresh_window, now_ts,
    ):
        if profile_exists_probe is None or await profile_exists_probe(
            property_channel_id,
        ):
            return BootstrapIntentResult(
                enqueued=False,
                status=observed.status,
                state=observed,
                reason="primed_fresh",
            )
        logger.warning(
            "bootstrap_intent.primed_without_profile",
            property_channel_id=property_channel_id,
            customer_id=tenant.customer_id,
        )

    # Layer 2 (in-flight): queued or warming = no-op.
    if observed.status in (
        PROPERTY_STATUS_QUEUED,
        PROPERTY_STATUS_WARMING,
    ):
        return BootstrapIntentResult(
            enqueued=False,
            status=observed.status,
            state=observed,
            reason="in_flight",
        )

    # Cold / stale / failed / primed-stale → eligible.
    # Transition to queued atomically, then dispatch.
    if observed.status not in (
        PROPERTY_STATUS_COLD,
        PROPERTY_STATUS_STALE,
        PROPERTY_STATUS_FAILED,
        PROPERTY_STATUS_PRIMED,  # reached when not fresh, or fresh-but-profile-missing
    ):
        # Defensive: any other status means schema drift.
        return BootstrapIntentResult(
            enqueued=False,
            status=observed.status,
            state=observed,
            reason="invalid_input",
        )

    queued_state = dataclasses.replace(
        observed,
        status=PROPERTY_STATUS_QUEUED,
        current_job_id=resolved_job_id,
        updated_at=now_ts,
        last_error=None,
    )
    persisted = await state_store.update(queued_state)

    logger.info(
        "bootstrap_intent.enqueued",
        property_channel_id=property_channel_id,
        customer_id=tenant.customer_id,
        provider_type=tenant.provider_type,
        reason=reason,
        job_id=resolved_job_id,
        previous_status=observed.status,
        window_days=window_days,
    )

    workload = workload_factory(persisted, resolved_job_id)
    intent = BootstrapIntentMessage(
        property_channel_id=property_channel_id,
        customer_id=tenant.customer_id,
        provider_type=tenant.provider_type,
        window_days=window_days,
        reason=reason,
        job_id=resolved_job_id,
        org_id=tenant.org_id,
    )
    await dispatcher.dispatch(
        property_channel_id=property_channel_id,
        job_id=resolved_job_id,
        workload=workload,
        intent=intent,
    )

    return BootstrapIntentResult(
        enqueued=True,
        status=PROPERTY_STATUS_QUEUED,
        state=persisted,
        reason="new",
    )


def _generate_job_id() -> str:
    """Stable hex job id — same shape as onboarding_endpoints uses."""
    import uuid

    return uuid.uuid4().hex
