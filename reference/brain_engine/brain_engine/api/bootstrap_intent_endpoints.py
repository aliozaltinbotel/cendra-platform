"""HTTP surface for the lightweight bootstrap-intent enqueue.

``POST /api/v1/onboarding/request-bootstrap/property/{property_channel_id}``

This is the *intent* counterpart to the heavy
``POST /api/v1/onboarding/bootstrap/property/{id}`` route.  Instead
of running the pipeline inside the request, it resolves the tenant,
records an intent in the ``property_state`` SSoT, and dispatches the
real work through the shared :func:`submit_bootstrap_intent` →
``request_bootstrap`` dedup path.  The Sandbox UI calls this on
property select; the Phase 4 middleware trigger calls the same
underlying function on first touch.  Three layers of dedup guarantee
exactly one real bootstrap per ``(property, fresh-window)`` — closing
P1 (double trigger) and P3 (no single status machine) from
``CENDRA_BRAIN_ENGINE_ARCHITECTURE_2026.md`` §2.

The route lives in its own module rather than extending
``onboarding_endpoints.py`` (already ~900 lines) per the project's
file-size discipline.

Activation is fully gated: the dependencies are wired only when
``PROPERTY_STATE_ENABLED`` is on (the SSoT exists) and
``TENANT_RESOLVER_ENABLED`` is on (a resolver is published).  With
either off the route answers ``503`` and the pre-Stage-1 behaviour
is unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from brain_engine.tenants import (
    active_tenant_resolver,
    submit_bootstrap_intent,
)

if TYPE_CHECKING:
    from brain_engine.onboarding.bootstrap_pipeline import (
        OnboardingBootstrapPipeline,
    )
    from brain_engine.profiles.store import PropertyProfileStore
    from brain_engine.tenants import BootstrapDispatcher, PropertyStateStore

__all__ = ["configure_bootstrap_intent_deps", "router"]


logger = structlog.get_logger(__name__)


router = APIRouter(
    prefix="/api/v1/onboarding",
    tags=["Onboarding"],
)


#: Default look-back window for an explicit UI warmup.  Matches the
#: Phase 4 trigger default (``AUTO_BOOTSTRAP_DAYS`` ≈ 2 years) so the
#: explicit and implicit paths request the *same* archive depth —
#: closing P7 (divergent windows) from the architecture doc §2.
_DEFAULT_WINDOW_DAYS: Final[int] = 730


# Shared deps — injected from ``server.py`` at lifespan start.  The
# resolver is read from the runtime singleton instead, so only the
# Stage 1 trio needs wiring here.
_deps: dict[str, Any] = {}


def configure_bootstrap_intent_deps(deps: dict[str, Any]) -> None:
    """Inject the Stage 1 dependencies.

    Args:
        deps: Must carry ``"state_store"`` (a
            :class:`PropertyStateStore`), ``"dispatcher"`` (a
            :class:`BootstrapDispatcher`), and ``"pipeline_getter"``
            (a zero-arg callable returning the live
            :class:`OnboardingBootstrapPipeline` or ``None``).  Any
            of them being ``None`` keeps the route disabled (503).
            May also carry ``"profile_store"`` (a
            :class:`PropertyProfileStore`); when present, a
            ``primed`` row whose profile is missing self-heals by
            re-enqueuing instead of short-circuiting.
    """
    _deps.update(deps)


class RequestBootstrapBody(BaseModel):
    """Optional body for the request-bootstrap route."""

    reason: str = Field(
        default="ui_select",
        max_length=64,
        description=(
            "Observability tag recorded on the intent. Typically "
            "'ui_select' for an operator pick; 'stale_refresh' / "
            "'webhook' for automated callers."
        ),
    )
    window_days: int | None = Field(
        default=None,
        ge=1,
        le=730,
        description=(
            "Archive look-back in days. Omit to use the unified "
            "default window shared with the auto-bootstrap trigger."
        ),
    )


class RequestBootstrapResponse(BaseModel):
    """Outcome of one request-bootstrap call."""

    enqueued: bool = Field(
        description="True iff this call started new work (False on dedup).",
    )
    status: str = Field(
        description="property_state status at exit (queued/warming/primed/…).",
    )
    job_id: str | None = Field(
        default=None,
        description="Current bootstrap job id when one is in flight.",
    )
    property_channel_id: str
    reason: str = Field(
        description="Dedup tag: new / primed_fresh / in_flight / invalid_input.",
    )


def _pipeline() -> OnboardingBootstrapPipeline:
    """Return the live pipeline or raise 503 when unavailable."""
    pipeline_getter = _deps.get("pipeline_getter")
    pipeline = pipeline_getter() if pipeline_getter is not None else None
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="bootstrap pipeline not configured",
        )
    return pipeline


def _profile_exists_probe() -> Callable[[str], Awaitable[bool]] | None:
    """Build a probe reporting whether a built profile exists right now.

    Returns ``None`` when no profile store is wired (keeps the legacy
    status-only dedup), otherwise an async predicate over the store —
    so a ``primed`` row whose profile was lost self-heals via re-harvest.
    """
    store: PropertyProfileStore | None = _deps.get("profile_store")
    if store is None:
        return None

    async def _probe(property_channel_id: str) -> bool:
        return await store.get(property_channel_id) is not None

    return _probe


@router.post(
    "/request-bootstrap/property/{property_channel_id}",
    response_model=RequestBootstrapResponse,
    summary="Enqueue a property bootstrap intent (lightweight).",
)
async def request_bootstrap_property(
    property_channel_id: str = Path(..., min_length=1),
    body: RequestBootstrapBody | None = None,
) -> RequestBootstrapResponse:
    """Resolve the tenant and enqueue a deduped bootstrap intent.

    Returns ``200`` with the intent outcome on success.  Raises
    ``503`` when the Stage 1 subsystem is not wired
    (``PROPERTY_STATE_ENABLED`` / ``TENANT_RESOLVER_ENABLED`` off)
    and ``409`` when the property resolves to a tenant without a
    customer id (cannot be bootstrapped).
    """

    state_store: PropertyStateStore | None = _deps.get("state_store")
    dispatcher: BootstrapDispatcher | None = _deps.get("dispatcher")
    if state_store is None or dispatcher is None:
        raise HTTPException(
            status_code=503,
            detail="property_state subsystem not enabled",
        )

    resolver = active_tenant_resolver()
    if resolver is None:
        raise HTTPException(
            status_code=503,
            detail="tenant resolver not enabled",
        )

    payload = body or RequestBootstrapBody()
    tenant = await resolver.resolve(property_channel_id)
    if not tenant.customer_id:
        raise HTTPException(
            status_code=409,
            detail="property resolved to a tenant without a customer id",
        )

    pipeline = _pipeline()
    window_days = payload.window_days or _DEFAULT_WINDOW_DAYS
    result = await submit_bootstrap_intent(
        property_channel_id=property_channel_id,
        tenant=tenant,
        pipeline=pipeline,
        state_store=state_store,
        dispatcher=dispatcher,
        window_days=window_days,
        reason=payload.reason,
        profile_exists_probe=_profile_exists_probe(),
    )

    job_id = result.state.current_job_id if result.state is not None else None
    logger.info(
        "request_bootstrap_endpoint.handled",
        property_channel_id=property_channel_id,
        customer_id=tenant.customer_id,
        enqueued=result.enqueued,
        status=result.status,
        reason=result.reason,
        window_days=window_days,
    )
    return RequestBootstrapResponse(
        enqueued=result.enqueued,
        status=result.status,
        job_id=job_id,
        property_channel_id=property_channel_id,
        reason=result.reason,
    )
