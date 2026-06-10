"""Process-global handle to the active :class:`TenantResolver`.

The FastAPI middleware is instantiated at application import time,
long before the lifespan hook can construct a Postgres pool and a
real :class:`TenantResolver`.  Rather than rebuild the middleware
stack at runtime (FastAPI does not allow that), the lifespan hook
publishes the resolver into this module-level holder via
:func:`configure_tenant_resolver`, and the middleware reads it back
per request via :func:`active_tenant_resolver`.

When the holder is ``None`` the middleware is a no-op — Phase 3 is
fully opt-in at the runtime level so deployments that have not yet
applied migration ``032`` (or that want to disable auto-resolution
for a debugging window) can simply skip the configure call.

This module also exposes :func:`record_bootstrap_tenant`, a small
helper that bootstrap routes call after a successful pipeline run
so the property → tenant mapping is persisted with
``source='bootstrap'`` for future auto-resolution.
"""

from __future__ import annotations

import structlog

from brain_engine.tenants.auto_bootstrap import AutoBootstrapTrigger
from brain_engine.tenants.models import (
    TENANT_SOURCE_BOOTSTRAP,
    TenantContext,
)
from brain_engine.tenants.resolver import TenantResolver

__all__ = [
    "active_auto_bootstrap_trigger",
    "active_tenant_resolver",
    "configure_auto_bootstrap_trigger",
    "configure_tenant_resolver",
    "record_bootstrap_tenant",
]


logger = structlog.get_logger(__name__)


_ACTIVE_RESOLVER: TenantResolver | None = None
_ACTIVE_TRIGGER: AutoBootstrapTrigger | None = None


def configure_tenant_resolver(resolver: TenantResolver | None) -> None:
    """Publish ``resolver`` as the process-wide active resolver.

    Pass ``None`` to detach the previously configured resolver
    (useful for test teardown).  Called exactly once from the
    FastAPI lifespan hook in production.
    """

    global _ACTIVE_RESOLVER
    _ACTIVE_RESOLVER = resolver


def active_tenant_resolver() -> TenantResolver | None:
    """Return the currently configured :class:`TenantResolver`."""

    return _ACTIVE_RESOLVER


def configure_auto_bootstrap_trigger(
    trigger: AutoBootstrapTrigger | None,
) -> None:
    """Publish ``trigger`` as the process-wide auto-bootstrap hook.

    Mirrors :func:`configure_tenant_resolver`: the FastAPI
    middleware reads the active trigger per request and is a no-op
    pass-through when no trigger is configured (Phase 4 disabled).
    Pass ``None`` to detach (test teardown).
    """

    global _ACTIVE_TRIGGER
    _ACTIVE_TRIGGER = trigger


def active_auto_bootstrap_trigger() -> AutoBootstrapTrigger | None:
    """Return the currently configured :class:`AutoBootstrapTrigger`."""

    return _ACTIVE_TRIGGER


async def record_bootstrap_tenant(
    *,
    property_channel_id: str,
    customer_id: str | None,
    org_id: str | None,
    provider_type: str | None,
) -> None:
    """Persist a property → tenant mapping after a successful bootstrap.

    No-op when no resolver is configured or when the caller did not
    supply a non-blank ``customer_id`` (a blank customer id means
    "use the pod default" — the resolver's env-default fallback
    already handles that case, no registry row needed).

    Args:
        property_channel_id: The bootstrapped property's channel id.
        customer_id: Customer override that the route received in
            the request body (Phase 1 contract).  ``None`` or blank
            means "no row to write".
        org_id: Workspace override (optional).
        provider_type: PMS override (optional, blank → not stored).
    """

    resolver = active_tenant_resolver()
    if resolver is None:
        return
    if customer_id is None or not customer_id.strip():
        return
    if not property_channel_id or not property_channel_id.strip():
        return

    cleaned_provider = (provider_type or "").strip()
    if not cleaned_provider:
        return

    context = TenantContext(
        customer_id=customer_id.strip(),
        org_id=(org_id.strip() if org_id and org_id.strip() else None),
        provider_type=cleaned_provider,
        property_channel_id=property_channel_id.strip(),
        source=TENANT_SOURCE_BOOTSTRAP,
    )
    try:
        await resolver.record(context)
        logger.info(
            "tenant_registry.bootstrap_upsert",
            property_channel_id=context.property_channel_id,
            customer_id=context.customer_id,
            provider_type=context.provider_type,
        )
    except Exception as exc:
        logger.warning(
            "tenant_registry.bootstrap_upsert_failed",
            property_channel_id=context.property_channel_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
