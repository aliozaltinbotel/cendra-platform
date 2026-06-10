"""Background workload wrapper that drives the `property_state` FSM.

`BootstrapRunner` is the bridge between
:func:`request_bootstrap` (which only knows about
`PropertyState` and the dispatcher) and the actual
:class:`OnboardingBootstrapPipeline` that performs the archive
pull + extraction + mining.  Wrapping the pipeline call here is
what makes the state machine honest:

* enter ``warming`` (the intent layer left the row in ``queued``);
* run ``pipeline.bootstrap_fast(...)``;
* on success, transition to ``primed`` and persist the harvest
  counters that came back in the report;
* on failure, transition to ``failed`` with the error message and
  bump ``retry_count`` so the dead-letter logic in PR-C can pick
  it up.

The runner intentionally does NOT live inside
:mod:`bootstrap_intent`: that file's contract is "dedup + dispatch"
and pulling the heavy pipeline import into it would force every
test of the intent layer to satisfy the pipeline's constructor.
Decoupling also caps each file under ~300 lines per the project's
file-size discipline.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog

from brain_engine.tenants.bootstrap_intent import (
    BootstrapDispatcher,
    BootstrapIntentResult,
    BootstrapWorkload,
    request_bootstrap,
)
from brain_engine.tenants.models import TenantContext
from brain_engine.tenants.property_state import (
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_WARMING,
    PropertyState,
)
from brain_engine.tenants.property_state_store import (
    PropertyStateNotFoundError,
    PropertyStateStore,
)

if TYPE_CHECKING:
    from brain_engine.onboarding.bootstrap_pipeline import (
        OnboardingBootstrapPipeline,
    )

__all__ = ["BootstrapRunner", "submit_bootstrap_intent"]


logger = structlog.get_logger(__name__)


#: Hard ceiling on a single in-process ``bootstrap_fast`` run.  A
#: run that exceeds it is cancelled and the row goes to ``failed``
#: (``retry_count`` bumped) so one slow/stuck property cannot hold a
#: warming row open forever.  ``asyncio.wait_for`` cancellation is
#: cooperative — it reliably interrupts an I/O-bound stall (a hung
#: GraphQL / LLM await) but cannot pre-empt a pure CPU busy-loop;
#: the real isolation arrives with the Stage 2 out-of-process worker.
_DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS: Final[float] = 1200.0  # 20 minutes


class BootstrapRunner:
    """Drive state transitions around a single ``bootstrap_fast`` call."""

    def __init__(
        self,
        *,
        pipeline: OnboardingBootstrapPipeline,
        state_store: PropertyStateStore,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float | None = _DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
    ) -> None:
        self._pipeline = pipeline
        self._state_store = state_store
        self._clock = clock or _utcnow
        # ``None`` disables the ceiling (tests / explicit opt-out).
        self._timeout_seconds = timeout_seconds

    def workload_factory(
        self,
        tenant: TenantContext,
        window_days: int,
    ) -> Callable[[PropertyState, str], BootstrapWorkload]:
        """Curry the runner into the shape `request_bootstrap` expects.

        Returns a callable that, given the queued
        :class:`PropertyState` row and a job id, produces a
        zero-argument :data:`BootstrapWorkload` ready for the
        dispatcher to schedule.
        """

        def factory(
            state: PropertyState, job_id: str,
        ) -> BootstrapWorkload:
            async def workload() -> None:
                await self._run(
                    queued_state=state,
                    job_id=job_id,
                    tenant=tenant,
                    window_days=window_days,
                )

            return workload

        return factory

    async def _run(
        self,
        *,
        queued_state: PropertyState,
        job_id: str,
        tenant: TenantContext,
        window_days: int,
    ) -> None:
        channel_id = queued_state.property_channel_id

        # queued → warming.  The intent layer already set
        # current_job_id on the queued row, so we only flip the
        # status + bump updated_at here.
        try:
            warming_state = dataclasses.replace(
                queued_state,
                status=PROPERTY_STATUS_WARMING,
                updated_at=self._clock(),
            )
            await self._state_store.update(warming_state)
        except PropertyStateNotFoundError:
            logger.warning(
                "bootstrap_runner.state_vanished_before_warming",
                property_channel_id=channel_id,
                job_id=job_id,
            )
            return

        logger.info(
            "bootstrap_runner.warming",
            property_channel_id=channel_id,
            customer_id=tenant.customer_id,
            provider_type=tenant.provider_type,
            job_id=job_id,
            window_days=window_days,
        )

        try:
            report = await asyncio.wait_for(
                self._pipeline.bootstrap_fast(
                    property_id=channel_id,
                    days=window_days,
                    customer_id_override=tenant.customer_id,
                    org_id_override=tenant.org_id,
                    provider_type_override=tenant.provider_type,
                    job_id=job_id,
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            logger.warning(
                "bootstrap_runner.timeout",
                property_channel_id=channel_id,
                job_id=job_id,
                timeout_seconds=self._timeout_seconds,
            )
            await self._mark_failed(
                queued_state=warming_state,
                error=exc,
                job_id=job_id,
            )
            return
        except Exception as exc:
            await self._mark_failed(
                queued_state=warming_state,
                error=exc,
                job_id=job_id,
            )
            return

        primed_state = dataclasses.replace(
            warming_state,
            status=PROPERTY_STATUS_PRIMED,
            current_job_id=None,
            conversations_loaded=int(
                getattr(report, "conversations_loaded", 0) or 0,
            ),
            cases_extracted=int(
                getattr(report, "cases_extracted", 0) or 0,
            ),
            rules_emitted=int(
                getattr(report, "rules_emitted", 0) or 0,
            ),
            profile_built=bool(
                getattr(report, "profile_built", False),
            ),
            window_days=int(window_days),
            last_bootstrap_at=self._clock(),
            updated_at=self._clock(),
            last_error=None,
            retry_count=0,
        )
        try:
            await self._state_store.update(primed_state)
        except PropertyStateNotFoundError:
            logger.warning(
                "bootstrap_runner.state_vanished_before_primed",
                property_channel_id=channel_id,
                job_id=job_id,
            )
            return

        logger.info(
            "bootstrap_runner.primed",
            property_channel_id=channel_id,
            job_id=job_id,
            conversations_loaded=primed_state.conversations_loaded,
            cases_extracted=primed_state.cases_extracted,
            rules_emitted=primed_state.rules_emitted,
        )

    async def _mark_failed(
        self,
        *,
        queued_state: PropertyState,
        error: BaseException,
        job_id: str,
    ) -> None:
        channel_id = queued_state.property_channel_id
        failed_state = dataclasses.replace(
            queued_state,
            status=PROPERTY_STATUS_FAILED,
            current_job_id=None,
            last_error=f"{type(error).__name__}: {error}",
            retry_count=queued_state.retry_count + 1,
            updated_at=self._clock(),
        )
        try:
            await self._state_store.update(failed_state)
        except PropertyStateNotFoundError:
            logger.warning(
                "bootstrap_runner.state_vanished_before_failed",
                property_channel_id=channel_id,
                job_id=job_id,
                error_type=type(error).__name__,
            )
            return

        logger.warning(
            "bootstrap_runner.failed",
            property_channel_id=channel_id,
            job_id=job_id,
            error=str(error),
            error_type=type(error).__name__,
            retry_count=failed_state.retry_count,
        )


async def submit_bootstrap_intent(
    *,
    property_channel_id: str,
    tenant: TenantContext,
    pipeline: OnboardingBootstrapPipeline,
    state_store: PropertyStateStore,
    dispatcher: BootstrapDispatcher,
    window_days: int,
    reason: str,
    already_primed_probe: Callable[[str], Awaitable[bool]] | None = None,
    profile_exists_probe: Callable[[str], Awaitable[bool]] | None = None,
    timeout_seconds: float | None = _DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
    job_id: str | None = None,
) -> BootstrapIntentResult:
    """Route one bootstrap request through the dedup + runner path.

    The single bridge every real caller — the Phase 4
    :class:`AutoBootstrapTrigger`, the request-bootstrap HTTP
    endpoint, and the future webhook consumer — uses to turn a
    ``(property, tenant, window)`` request into a deduped,
    dispatched bootstrap.  It curries a :class:`BootstrapRunner`
    into the ``workload_factory`` shape :func:`request_bootstrap`
    expects, so the runner wiring lives in exactly one place
    instead of being copy-pasted into each caller.

    Args:
        property_channel_id: Short Cendra channel id.
        tenant: Resolved tenant scope for the property.
        pipeline: Live bootstrap pipeline the runner drives.
        state_store: The ``property_state`` SSoT contract.
        dispatcher: Pluggable execution model (asyncio in Stage 1,
            Service Bus in Stage 2).
        window_days: Archive look-back the workload should pull.
        reason: Observability tag (``ui_select`` / ``first_touch``
            / ``stale_refresh`` / ``webhook``).
        already_primed_probe: Optional async predicate forwarded to
            :func:`request_bootstrap` so a property that already has
            a built profile is adopted as ``primed`` instead of
            re-bootstrapped.
        profile_exists_probe: Optional async predicate forwarded to
            :func:`request_bootstrap`; lets a ``primed`` + fresh row
            whose profile is actually missing self-heal by being
            re-enqueued instead of short-circuited.
        timeout_seconds: Hard ceiling on the single ``bootstrap_fast``
            run the runner drives; ``None`` disables it.
        job_id: Optional caller-supplied id; generated when
            omitted.

    Returns:
        The :class:`BootstrapIntentResult` from
        :func:`request_bootstrap` — ``enqueued`` is ``False`` on
        every dedup short-circuit.
    """

    runner = BootstrapRunner(
        pipeline=pipeline,
        state_store=state_store,
        timeout_seconds=timeout_seconds,
    )
    return await request_bootstrap(
        property_channel_id=property_channel_id,
        tenant=tenant,
        window_days=window_days,
        reason=reason,
        state_store=state_store,
        dispatcher=dispatcher,
        workload_factory=runner.workload_factory(tenant, window_days),
        already_primed_probe=already_primed_probe,
        profile_exists_probe=profile_exists_probe,
        job_id=job_id,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)
