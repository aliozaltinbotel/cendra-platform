"""HTTP surface for the V2 onboarding bootstrap pipeline.

Exposes three routes over the :class:`OnboardingBootstrapPipeline`:

1. ``POST /api/v1/onboarding/bootstrap`` — kicks off a background job
   that runs the pipeline for one-or-more properties and returns a
   job identifier immediately.
2. ``GET /api/v1/onboarding/bootstrap/{job_id}`` — returns the job's
   current state plus the :class:`BootstrapReport` when finished.
3. ``POST /api/v1/onboarding/bootstrap/property/{property_id}`` —
   synchronous single-property variant used by Mümin's V1 onboarding
   step 4 (PM picks one listing → ingest only that property).

The router keeps shared dependencies in a module-level dict populated
by ``server.py`` at lifespan start, matching the pattern already in
place for the interview, workflow and decision-card routers.

Jobs are tracked in an in-process :class:`dict`.  That keeps the
contract stable for the dev cluster (one replica) and, if a future
deployment needs horizontal scaling, swapping the registry for a
Redis-backed store is a localised change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from brain_engine.onboarding.bootstrap_pipeline import (
    BootstrapJobState,
    BootstrapPropertyReport,
    BootstrapRequest,
    OnboardingBootstrapPipeline,
)
from brain_engine.onboarding.event_bus import EventKind
from brain_engine.onboarding.job_store import (
    BootstrapJobStore,
    InMemoryBootstrapJobStore,
    NullBootstrapJobStore,
)

__all__ = [
    "configure_onboarding_deps",
    "router",
]


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/onboarding",
    tags=["Onboarding"],
)


# Shared deps — injected from server.py at lifespan start.
_deps: dict[str, Any] = {}

# Local-pod caches.  ``_jobs`` is now a fast-path mirror of the
# canonical :class:`BootstrapJobStore` (see ``_job_store_dep``)
# so single-replica or test deployments stay zero-cost.  The
# multi-pod contract goes through the store every read; the
# mirror is opportunistic and never the source of truth.
_jobs: dict[str, BootstrapJobState] = {}
_job_tasks: dict[str, asyncio.Task[None]] = {}


def _job_store() -> BootstrapJobStore:
    """Return the configured :class:`BootstrapJobStore`.

    Falls back to an :class:`InMemoryBootstrapJobStore` so callers
    that forget to wire a store on test rigs keep working with
    single-pod semantics.  Production deployments wire a
    :class:`RedisBootstrapJobStore` from ``server.py``.
    """
    store = _deps.get("onboarding_job_store")
    if store is None:
        store = InMemoryBootstrapJobStore()
        _deps["onboarding_job_store"] = store
    return store


async def _persist_state(state: BootstrapJobState) -> None:
    """Mirror the local snapshot into the cross-pod registry."""
    _jobs[state.job_id] = state
    try:
        await _job_store().put(state.job_id, state.as_dict())
    except Exception:  # noqa: BLE001 - cross-pod write is best-effort
        logger.exception(
            "onboarding.job_state_persist_failed",
            extra={"job_id": state.job_id},
        )


def configure_onboarding_deps(deps: dict[str, Any]) -> None:
    """Inject shared dependencies.

    Args:
        deps: Dependency dict populated at server startup.  Must
            contain ``"onboarding_bootstrap_pipeline"`` mapped to a
            live :class:`OnboardingBootstrapPipeline`.
    """
    _deps.update(deps)


def _pipeline() -> OnboardingBootstrapPipeline:
    """Return the configured :class:`OnboardingBootstrapPipeline`."""
    pipeline = _deps.get("onboarding_bootstrap_pipeline")
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="OnboardingBootstrapPipeline not configured",
        )
    return pipeline


# ── Wire models ──────────────────────────────────────────────────


class BootstrapJobRequest(BaseModel):
    """Input payload for ``POST /onboarding/bootstrap``.

    Attributes:
        property_ids: Properties to bootstrap.  At least one required.
        days: Look-back window size in days (clamped to ``[1, 730]``).
        limit_per_property: Cap on conversations per property.
        dry_run: When true, run the pipeline without persisting.
        mine_patterns: When true, run the pattern miner after cases
            are extracted.  Requires a configured rule store.
    """

    property_ids: list[str] = Field(..., min_length=1)
    # Mümin 2026-05-12 follow-up: ``None`` means "no window / no cap".
    # The pipeline coerces ``None`` to the system ceilings
    # (``_MAX_DAYS = 3650`` years, ``_MAX_LIMIT_PER_PROPERTY = 100_000``)
    # so the bootstrap ingests the entire archive by default.
    # Explicit numeric values still clamp into the validated ranges
    # for safety on misconfigured calls.
    days: int | None = Field(default=None, ge=1, le=3650)
    limit_per_property: int | None = Field(
        default=None, ge=1, le=100_000,
    )
    dry_run: bool = False
    mine_patterns: bool = True


class BootstrapJobAcceptedResponse(BaseModel):
    """Wire form of a freshly-submitted bootstrap job."""

    job_id: str
    status: str
    submitted_at: str


class SinglePropertyBootstrapRequest(BaseModel):
    """Input payload for the single-property bootstrap route.

    The ``property_id`` is taken from the URL so this body only carries
    the tunables.  Defaults match :class:`BootstrapRequest` so the
    single-property and batch paths behave identically when callers
    omit knobs.

    Cross-tenant overrides (Phase 1 multi-tenant bootstrap, 2026-05-21)
    let the caller replace the pod's default ``UNIFIED_DATA_*``
    workspace identifiers for this one request.  Used by the tester
    team to bootstrap properties that belong to a Cendra workspace
    other than the one the dev pod is configured for, without
    bouncing the pod or breaking the existing single-tenant default.
    All three are optional — when omitted the env-default applies
    exactly like before.
    """

    # ``None`` triggers "ingest the entire archive" semantics — see
    # :class:`BootstrapJobRequest` for the rationale.  V1 onboarding
    # step 4 sends an empty body so the operator's "ingest all I have
    # for this property" intent becomes the default behaviour.
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int | None = Field(default=None, ge=1, le=100_000)
    dry_run: bool = False
    mine_patterns: bool = True
    # Phase 1 cross-tenant override — see class docstring.
    customer_id: str | None = Field(default=None)
    org_id: str | None = Field(default=None)
    provider_type: str | None = Field(default=None)


class FastSinglePropertyBootstrapRequest(BaseModel):
    """Input payload for the fast cold-start single-property route.

    Designed for V1 onboarding step 4 when the operator must see a
    freshly-picked property as ``ready_for_live`` within seconds.
    Defaults bias the pipeline toward speed: a 30-day look-back
    window, an inner conversation worker pool of 8, and pattern
    mining deferred to a background task so the HTTP response does
    not wait on the heaviest LLM step.

    Attributes:
        days: Look-back window size in days; clamped into ``[1, 730]``.
        inner_concurrency: Per-property fan-out cap on conversation
            workers.  Default ``8`` matches Brain Engine's measured
            Azure OpenAI TPM headroom; raise this only after
            confirming the deployment has additional throughput.
        mine_patterns_inline: When ``True``, run pattern mining
            inside the request and surface ``rules_emitted``.
            Default ``False`` keeps cold-start in seconds.
        dry_run: Run the pipeline without persisting anything.
    """

    # ``None`` triggers "ingest the entire archive".  Fast cold-start
    # callers that need a tighter window can still pass a numeric
    # value; the schema clamps into ``[1, 3650]`` for safety.
    days: int | None = Field(default=None, ge=1, le=3650)
    inner_concurrency: int = Field(default=8, ge=1, le=64)
    mine_patterns_inline: bool = False
    dry_run: bool = False
    # Phase 1 cross-tenant override — see
    # :class:`SinglePropertyBootstrapRequest` docstring.
    customer_id: str | None = Field(default=None)
    org_id: str | None = Field(default=None)
    provider_type: str | None = Field(default=None)


class SinglePropertyBootstrapResponse(BaseModel):
    """Wire form of a synchronous single-property bootstrap result.

    Mirrors the per-report fields of
    :meth:`BootstrapReport.as_dict` so V1 (one property) and V2
    (multi-property job) clients can share parsing code.

    ``job_id`` is the audit-log handle: hit
    ``GET /onboarding/jobs/{job_id}/log`` to retrieve the structured
    per-conversation event stream the pipeline emitted during the
    call.
    """

    property_id: str
    job_id: str = ""
    conversations_loaded: int
    conversations_with_dates: int = 0
    episodes_emitted: int
    cases_extracted: int
    cases_skipped: int
    rules_emitted: int
    profile_built: bool
    unanswered_thread_count: int
    rate_plans_seen: int
    reviews_seen: int
    stage_distribution: dict[str, int] = Field(default_factory=dict)
    # Mümin 2026-05-12 (PR #B): ``loader_truncated`` is True when
    # the loader stopped because the caller's ``limit`` was hit
    # before the archive was exhausted; ``loader_limit`` records
    # the cap so the UI can prompt the operator to re-run with a
    # higher value.
    loader_truncated: bool = False
    loader_limit: int = 0
    error: str = ""


class BootstrapJobStatusResponse(BaseModel):
    """Wire form of a job's current state.

    ``report`` is populated when the job has finished successfully.
    ``error`` carries the failure summary when the job terminates
    unsuccessfully; both fields are otherwise empty.
    """

    job_id: str
    status: str
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str = ""
    report: dict[str, Any] | None = None


# ── Endpoints ────────────────────────────────────────────────────


@router.post(
    "/bootstrap",
    response_model=BootstrapJobAcceptedResponse,
    status_code=202,
)
async def submit_bootstrap(
    payload: BootstrapJobRequest,
) -> BootstrapJobAcceptedResponse:
    """Schedule a bootstrap job and return its identifier."""
    pipeline = _pipeline()
    job_id = uuid.uuid4().hex
    state = BootstrapJobState(job_id=job_id, status="pending")
    await _persist_state(state)
    request = BootstrapRequest(
        property_ids=tuple(payload.property_ids),
        days=payload.days,
        limit_per_property=payload.limit_per_property,
        dry_run=payload.dry_run,
        mine_patterns=payload.mine_patterns,
    )
    task = asyncio.create_task(
        _run_job(pipeline=pipeline, state=state, request=request),
        name=f"onboarding-bootstrap-{job_id}",
    )
    _job_tasks[job_id] = task
    task.add_done_callback(lambda t: _job_tasks.pop(job_id, None))
    return BootstrapJobAcceptedResponse(
        job_id=state.job_id,
        status=state.status,
        submitted_at=state.submitted_at.isoformat(),
    )


@router.post(
    "/bootstrap/property/{property_id}",
    response_model=SinglePropertyBootstrapResponse,
)
async def bootstrap_one_property(
    property_id: str,
    payload: SinglePropertyBootstrapRequest | None = None,
) -> SinglePropertyBootstrapResponse:
    """Run the bootstrap pipeline for one property and return its report.

    Powers Mümin's V1 onboarding step 4: after the PM authenticates
    against the PMS and picks a single listing, the UI hits this route
    to ingest *only* that property's conversations / reservations /
    rate plans / reviews.  The response is synchronous because a
    single property fits comfortably inside one HTTP request.
    """
    pipeline = _pipeline()
    if not property_id or not property_id.strip():
        raise HTTPException(
            status_code=400,
            detail="property_id path parameter is required",
        )
    body = payload or SinglePropertyBootstrapRequest()
    job_id = uuid.uuid4().hex
    try:
        report = await pipeline.bootstrap_one(
            property_id=property_id,
            days=body.days,
            limit=body.limit,
            dry_run=body.dry_run,
            mine_patterns=body.mine_patterns,
            job_id=job_id,
            customer_id_override=body.customer_id,
            org_id_override=body.org_id,
            provider_type_override=body.provider_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _record_tenant_after_bootstrap(property_id=property_id, body=body)
    return _property_report_to_response(report, job_id=job_id)


@router.post(
    "/bootstrap/property/{property_id}/async",
    response_model=BootstrapJobAcceptedResponse,
    status_code=202,
)
async def bootstrap_one_property_async(
    property_id: str,
    payload: SinglePropertyBootstrapRequest | None = None,
) -> BootstrapJobAcceptedResponse:
    """Schedule a one-property bootstrap as a background job.

    Mümin 2026-05-12 (PR #C): the synchronous
    :func:`bootstrap_one_property` route blocks the HTTP request for
    the full duration of the pipeline.  With PR #B raising the
    per-property cap to 100k conversations a deep cold-start can
    easily exceed the 30s ingress timeout.  This async variant
    queues the work behind an ``asyncio.Task`` and returns the
    ``job_id`` immediately so the UI can poll
    :func:`get_bootstrap_status` (or stream
    :func:`stream_audit_log`) for progress.
    """
    pipeline = _pipeline()
    if not property_id or not property_id.strip():
        raise HTTPException(
            status_code=400,
            detail="property_id path parameter is required",
        )
    body = payload or SinglePropertyBootstrapRequest()
    job_id = uuid.uuid4().hex
    state = BootstrapJobState(job_id=job_id, status="pending")
    await _persist_state(state)
    task = asyncio.create_task(
        _run_single_property_job(
            pipeline=pipeline,
            state=state,
            property_id=property_id,
            body=body,
            fast=False,
        ),
        name=f"onboarding-bootstrap-one-{job_id}",
    )
    _job_tasks[job_id] = task
    task.add_done_callback(lambda t: _job_tasks.pop(job_id, None))
    return BootstrapJobAcceptedResponse(
        job_id=state.job_id,
        status=state.status,
        submitted_at=state.submitted_at.isoformat(),
    )


@router.post(
    "/bootstrap/property/{property_id}/fast",
    response_model=SinglePropertyBootstrapResponse,
)
async def bootstrap_one_property_fast(
    property_id: str,
    payload: FastSinglePropertyBootstrapRequest | None = None,
) -> SinglePropertyBootstrapResponse:
    """Run the fast cold-start pipeline for one property.

    Same wire shape as the legacy
    :func:`bootstrap_one_property` route, but tuned for cold-start
    UX: a 30-day look-back window, parallel inner conversation
    workers, and background pattern mining.  See
    :meth:`OnboardingBootstrapPipeline.bootstrap_fast` for the
    accelerator stack and the trade-offs each one makes.
    """
    pipeline = _pipeline()
    if not property_id or not property_id.strip():
        raise HTTPException(
            status_code=400,
            detail="property_id path parameter is required",
        )
    body = payload or FastSinglePropertyBootstrapRequest()
    job_id = uuid.uuid4().hex
    try:
        report = await pipeline.bootstrap_fast(
            property_id=property_id,
            days=body.days,
            inner_concurrency=body.inner_concurrency,
            mine_patterns_inline=body.mine_patterns_inline,
            dry_run=body.dry_run,
            job_id=job_id,
            customer_id_override=body.customer_id,
            org_id_override=body.org_id,
            provider_type_override=body.provider_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _record_tenant_after_bootstrap(property_id=property_id, body=body)
    return _property_report_to_response(report, job_id=job_id)


@router.post(
    "/bootstrap/property/{property_id}/fast/async",
    response_model=BootstrapJobAcceptedResponse,
    status_code=202,
)
async def bootstrap_one_property_fast_async(
    property_id: str,
    payload: FastSinglePropertyBootstrapRequest | None = None,
) -> BootstrapJobAcceptedResponse:
    """Schedule a fast-path single-property bootstrap as a background job.

    Companion to :func:`bootstrap_one_property_async` for the fast
    cold-start path (parallel inner workers, optional inline
    mining).  Same async semantics: the job runs behind an
    ``asyncio.Task`` and the operator polls
    :func:`get_bootstrap_status` (or streams
    :func:`stream_audit_log`) for progress.
    """
    pipeline = _pipeline()
    if not property_id or not property_id.strip():
        raise HTTPException(
            status_code=400,
            detail="property_id path parameter is required",
        )
    body = payload or FastSinglePropertyBootstrapRequest()
    job_id = uuid.uuid4().hex
    state = BootstrapJobState(job_id=job_id, status="pending")
    await _persist_state(state)
    task = asyncio.create_task(
        _run_single_property_job(
            pipeline=pipeline,
            state=state,
            property_id=property_id,
            body=body,
            fast=True,
        ),
        name=f"onboarding-bootstrap-fast-{job_id}",
    )
    _job_tasks[job_id] = task
    task.add_done_callback(lambda t: _job_tasks.pop(job_id, None))
    return BootstrapJobAcceptedResponse(
        job_id=state.job_id,
        status=state.status,
        submitted_at=state.submitted_at.isoformat(),
    )


@router.get(
    "/bootstrap/{job_id}",
    response_model=BootstrapJobStatusResponse,
)
async def get_bootstrap_status(job_id: str) -> BootstrapJobStatusResponse:
    """Return the current state of a bootstrap job.

    Mümin 2026-05-12: the response is sourced from the
    :class:`BootstrapJobStore` (Redis-backed in production) so any
    replica can serve a job created on a different replica.  The
    local ``_jobs`` cache is consulted first as a fast path; the
    store is the canonical source.
    """
    local_state = _jobs.get(job_id)
    if local_state is not None:
        return _state_to_response(local_state)
    snapshot = await _job_store().get(job_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"bootstrap job {job_id!r} not found",
        )
    return _snapshot_to_response(snapshot)


# ── Realtime audit log endpoints ─────────────────────────────────


class BootstrapJobSummaryResponse(BaseModel):
    """Aggregated counters + skip-reason breakdown for one job."""

    job_id: str
    property_id: str
    started_at: str
    finished_at: str | None = None
    status: str
    counts: dict[str, int] = Field(default_factory=dict)
    skip_breakdown: dict[str, int] = Field(default_factory=dict)
    rule_block_breakdown: dict[str, int] = Field(default_factory=dict)
    last_error: str = ""


class BootstrapEventModel(BaseModel):
    """Wire form of a single :class:`BootstrapEvent`."""

    ts: str
    job_id: str
    property_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class BootstrapJobLogResponse(BaseModel):
    """Paginated event log for one job."""

    job_id: str
    since: int
    limit: int
    returned: int
    events: list[BootstrapEventModel] = Field(default_factory=list)


@router.get(
    "/jobs/{job_id}",
    response_model=BootstrapJobSummaryResponse,
)
async def get_audit_summary(job_id: str) -> BootstrapJobSummaryResponse:
    """Return the aggregated audit summary for ``job_id``.

    Reads from the bootstrap event bus — orthogonal to
    ``GET /bootstrap/{job_id}`` which returns the outer
    :class:`BootstrapJobState`.  Use this endpoint when you want to
    render a per-property progress bar mid-flight: the counts and
    skip / rule_block breakdowns update in real time.
    """
    pipeline = _pipeline()
    summary = await pipeline.event_bus.summary(job_id)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"audit log for {job_id!r} not found",
        )
    return BootstrapJobSummaryResponse(**summary.to_dict())


@router.get(
    "/jobs/{job_id}/log",
    response_model=BootstrapJobLogResponse,
)
async def get_audit_log(
    job_id: str,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    kind: list[str] | None = Query(default=None),
) -> BootstrapJobLogResponse:
    """Return up to ``limit`` events for ``job_id`` after ``since``.

    ``kind`` accepts repeated query-string values matching the
    :class:`EventKind` enum (``conversation_skipped``, ``rule_blocked``,
    …).  An unknown ``kind`` triggers HTTP 400 so callers learn the
    typo immediately rather than silently getting an empty list.
    """
    pipeline = _pipeline()
    kinds_tuple: tuple[EventKind, ...] | None = None
    if kind:
        kinds: list[EventKind] = []
        for value in kind:
            try:
                kinds.append(EventKind(value))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown event kind: {value!r}",
                ) from exc
        kinds_tuple = tuple(kinds)
    events = await pipeline.event_bus.history(
        job_id, since=since, limit=limit, kinds=kinds_tuple,
    )
    return BootstrapJobLogResponse(
        job_id=job_id,
        since=since,
        limit=limit,
        returned=len(events),
        events=[BootstrapEventModel(**e.to_dict()) for e in events],
    )


@router.get("/jobs/{job_id}/stream")
async def stream_audit_log(job_id: str) -> StreamingResponse:
    """Server-Sent Events tail of the audit log for ``job_id``.

    Emits ``event: <kind>\\ndata: <json>\\n\\n`` frames until the job
    reaches ``JOB_DONE`` / ``JOB_FAILED`` or the connection drops.
    Browser ``EventSource`` clients (Mümin's PM Chat panel) connect
    here for the realtime drip.
    """
    pipeline = _pipeline()
    bus = pipeline.event_bus

    async def _generator() -> Any:
        try:
            async for event in bus.stream(job_id):
                yield (
                    f"event: {event.kind.value}\n"
                    f"data: {json.dumps(event.to_dict())}\n\n"
                )
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Helpers ──────────────────────────────────────────────────────


async def _run_job(
    *,
    pipeline: OnboardingBootstrapPipeline,
    state: BootstrapJobState,
    request: BootstrapRequest,
) -> None:
    """Execute one bootstrap job and update its state as it progresses."""
    state.status = "running"
    state.started_at = datetime.now(timezone.utc)
    await _persist_state(state)
    try:
        report = await pipeline.bootstrap(request, job_id=state.job_id)
    except asyncio.CancelledError:
        state.status = "cancelled"
        state.finished_at = datetime.now(timezone.utc)
        await _persist_state(state)
        raise
    except Exception as exc:  # noqa: BLE001 - logged + surfaced via state
        logger.exception(
            "onboarding.bootstrap_job_failed",
            extra={"job_id": state.job_id},
        )
        state.status = "failed"
        state.error = str(exc) or exc.__class__.__name__
        state.finished_at = datetime.now(timezone.utc)
        await _persist_state(state)
        return
    state.report = report
    state.status = "completed"
    state.finished_at = datetime.now(timezone.utc)
    await _persist_state(state)


async def _run_single_property_job(
    *,
    pipeline: OnboardingBootstrapPipeline,
    state: BootstrapJobState,
    property_id: str,
    body: SinglePropertyBootstrapRequest | FastSinglePropertyBootstrapRequest,
    fast: bool,
) -> None:
    """Execute a single-property bootstrap and update job state.

    Mümin 2026-05-12 (PR #C): the synchronous single-property routes
    block the HTTP request for the full duration of the pipeline.
    With ``limit=100k`` raised by PR #B that can exceed the ingress
    timeout.  This helper drives the same
    :meth:`OnboardingBootstrapPipeline.bootstrap_one` /
    :meth:`bootstrap_fast` method off the request path and surfaces
    progress through:

    * the outer :class:`BootstrapJobState` (status / started_at /
      finished_at / property_report);
    * the realtime audit-log bus (every per-conversation /
      per-case / per-rule decision the pipeline emits during the
      run).

    Failures are captured into ``state.error`` rather than raised
    so a misbehaving pipeline cannot poison the asyncio task group;
    ``CancelledError`` propagates so task cancellation continues to
    unwind cleanly.
    """
    state.status = "running"
    state.started_at = datetime.now(timezone.utc)
    await _persist_state(state)
    try:
        if fast:
            assert isinstance(body, FastSinglePropertyBootstrapRequest)
            report = await pipeline.bootstrap_fast(
                property_id=property_id,
                days=body.days,
                inner_concurrency=body.inner_concurrency,
                mine_patterns_inline=body.mine_patterns_inline,
                dry_run=body.dry_run,
                job_id=state.job_id,
                customer_id_override=body.customer_id,
                org_id_override=body.org_id,
                provider_type_override=body.provider_type,
            )
        else:
            assert isinstance(body, SinglePropertyBootstrapRequest)
            report = await pipeline.bootstrap_one(
                property_id=property_id,
                days=body.days,
                limit=body.limit,
                dry_run=body.dry_run,
                mine_patterns=body.mine_patterns,
                job_id=state.job_id,
                customer_id_override=body.customer_id,
                org_id_override=body.org_id,
                provider_type_override=body.provider_type,
            )
    except asyncio.CancelledError:
        state.status = "cancelled"
        state.finished_at = datetime.now(timezone.utc)
        await _persist_state(state)
        raise
    except Exception as exc:  # noqa: BLE001 - logged + surfaced via state
        logger.exception(
            "onboarding.bootstrap_single_property_job_failed",
            extra={"job_id": state.job_id, "property_id": property_id},
        )
        state.status = "failed"
        state.error = str(exc) or exc.__class__.__name__
        state.finished_at = datetime.now(timezone.utc)
        await _persist_state(state)
        return
    state.property_report = report
    state.status = "completed"
    state.finished_at = datetime.now(timezone.utc)
    await _persist_state(state)
    await _record_tenant_after_bootstrap(property_id=property_id, body=body)


async def _record_tenant_after_bootstrap(
    *,
    property_id: str,
    body: SinglePropertyBootstrapRequest | FastSinglePropertyBootstrapRequest,
) -> None:
    """Persist the property → tenant mapping after a successful run.

    Phase 3 hook (PR follow-up to PR #331): when the operator passed
    ``customer_id`` / ``org_id`` / ``provider_type`` overrides in
    the body, write them into ``property_tenant_registry`` so the
    next Sandbox UI request against this property auto-resolves the
    correct tenant without the body fields.  Best-effort: any
    failure is logged inside
    :func:`record_bootstrap_tenant` and never re-raised.
    """

    from brain_engine.tenants.runtime import record_bootstrap_tenant

    await record_bootstrap_tenant(
        property_channel_id=property_id,
        customer_id=body.customer_id,
        org_id=body.org_id,
        provider_type=body.provider_type,
    )


def _property_report_to_response(
    report: BootstrapPropertyReport,
    *,
    job_id: str = "",
) -> SinglePropertyBootstrapResponse:
    """Adapt a :class:`BootstrapPropertyReport` to its wire model."""
    return SinglePropertyBootstrapResponse(
        property_id=report.property_id,
        job_id=job_id,
        conversations_loaded=report.conversations_loaded,
        conversations_with_dates=report.conversations_with_dates,
        episodes_emitted=report.episodes_emitted,
        cases_extracted=report.cases_extracted,
        cases_skipped=report.cases_skipped,
        rules_emitted=report.rules_emitted,
        profile_built=report.profile_built,
        unanswered_thread_count=report.unanswered_thread_count,
        rate_plans_seen=report.rate_plans_seen,
        reviews_seen=report.reviews_seen,
        stage_distribution=dict(report.stage_distribution),
        loader_truncated=report.loader_truncated,
        loader_limit=report.loader_limit,
        error=report.error,
    )


def _state_to_response(
    state: BootstrapJobState,
) -> BootstrapJobStatusResponse:
    """Turn a :class:`BootstrapJobState` into the wire response model.

    A job state carries *either* a multi-property
    :class:`BootstrapReport` (V2 batch path) *or* a single-property
    :class:`BootstrapPropertyReport` (PR #C async-single path).  The
    response normalises both into the same ``report`` key — clients
    distinguish by the presence of the ``property_reports`` field
    (only V2) versus ``conversations_loaded`` (single-property).
    """
    if state.report is not None:
        report_data: dict[str, Any] | None = state.report.as_dict()
    elif state.property_report is not None:
        report_data = state.property_report.as_dict()
    else:
        report_data = None
    return BootstrapJobStatusResponse(
        job_id=state.job_id,
        status=state.status,
        submitted_at=state.submitted_at.isoformat(),
        started_at=(
            state.started_at.isoformat() if state.started_at else None
        ),
        finished_at=(
            state.finished_at.isoformat() if state.finished_at else None
        ),
        error=state.error,
        report=report_data,
    )


def _snapshot_to_response(
    snapshot: dict[str, Any],
) -> BootstrapJobStatusResponse:
    """Build the response from a :class:`BootstrapJobStore` dict.

    The store persists :meth:`BootstrapJobState.as_dict` verbatim;
    reconstruction back into the dataclass is unnecessary because
    the wire shape is identical.  Falling back to safe defaults
    on missing keys keeps the response valid even when the
    serialised form skips an empty field.
    """
    return BootstrapJobStatusResponse(
        job_id=str(snapshot.get("job_id") or ""),
        status=str(snapshot.get("status") or "unknown"),
        submitted_at=str(snapshot.get("submitted_at") or ""),
        started_at=(
            str(snapshot["started_at"])
            if snapshot.get("started_at")
            else None
        ),
        finished_at=(
            str(snapshot["finished_at"])
            if snapshot.get("finished_at")
            else None
        ),
        error=str(snapshot.get("error") or ""),
        report=snapshot.get("report"),
    )
