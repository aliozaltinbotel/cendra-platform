"""Single entry point for Phase 3 + Phase 4 tenant wiring.

Bundles the resolver wire (Phase 3 — auto-resolve tenant from
``property_id``) and the auto-bootstrap trigger wire (Phase 4 —
fire ``bootstrap_fast`` on first property touch) so
``api_server/server.py`` can wire both subsystems with a single
call instead of two inline blocks of globals + dispatch + close
boilerplate.

Each phase remains independently feature-gated via its own env
flag (``TENANT_RESOLVER_ENABLED`` and ``AUTO_BOOTSTRAP_ENABLED``);
the returned :class:`MultiTenantHandles` may carry ``None`` for
either or both.  The shutdown contract collapses to a single
``handles.close()`` await, regardless of how many phases were
enabled.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import structlog

from api_server.bootstrap.auto_bootstrap import wire_auto_bootstrap_trigger
from api_server.bootstrap.tenant_resolver import wire_tenant_resolver
from brain_engine.tenants import (
    AsyncioBootstrapDispatcher,
    AutoBootstrapTrigger,
    BootstrapDispatcher,
    PropertyStateStore,
    PropertyTenantRegistry,
    ServiceBusBootstrapDispatcher,
    TenantResolver,
)

if TYPE_CHECKING:
    from brain_engine.onboarding.bootstrap_pipeline import (
        OnboardingBootstrapPipeline,
    )
    from brain_engine.profiles.store import PropertyProfileStore

__all__ = ["MultiTenantHandles", "wire_multi_tenant"]


logger = structlog.get_logger(__name__)


_MAX_CONCURRENCY_ENV: Final[str] = "AUTO_BOOTSTRAP_MAX_CONCURRENCY"
_DEFAULT_MAX_CONCURRENCY: Final[int] = 2
_QUEUE_ENABLED_ENV: Final[str] = "BOOTSTRAP_QUEUE_ENABLED"
_SERVICE_BUS_CONN_ENV: Final[str] = "AZURE_SERVICEBUS_CONNECTION_STRING"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _queue_enabled() -> bool:
    """True when the Stage 2 Service Bus producer is switched on."""

    raw = os.environ.get(_QUEUE_ENABLED_ENV, "").strip().lower()
    return raw in _TRUTHY


def _build_dispatcher() -> tuple[
    BootstrapDispatcher,
    Callable[[], Awaitable[None]] | None,
]:
    """Pick the bootstrap dispatcher from env, with a safe default.

    Returns the dispatcher plus an optional async cleanup callable
    (the Service Bus sender's ``aclose``; ``None`` for the asyncio
    dispatcher, which owns no external resource).

    ``BOOTSTRAP_QUEUE_ENABLED`` selects the Stage 2 out-of-process
    producer.  When it is on but no connection string is configured
    we fall back to the in-process dispatcher rather than crash the
    pod — a misconfigured flag must not take the serving path down.
    """

    if _queue_enabled():
        conn = os.environ.get(_SERVICE_BUS_CONN_ENV, "").strip()
        if conn:
            from brain_engine.integrations.service_bus import (
                BOOTSTRAP_QUEUE,
                ServiceBusQueueSender,
            )

            sender = ServiceBusQueueSender(
                connection_string=conn,
                queue_name=BOOTSTRAP_QUEUE,
            )
            logger.info(
                "bootstrap.dispatcher_selected",
                kind="service_bus",
                queue=BOOTSTRAP_QUEUE,
            )
            return ServiceBusBootstrapDispatcher(sender), sender.aclose
        logger.warning(
            "bootstrap.queue_enabled_but_no_connection_string",
            fallback="asyncio",
        )
    dispatcher = AsyncioBootstrapDispatcher(
        max_concurrency=_max_bootstrap_concurrency(),
    )
    return dispatcher, None


def _chain_close(
    *closes: Callable[[], Awaitable[None]] | None,
) -> Callable[[], Awaitable[None]] | None:
    """Combine non-``None`` async cleanups into one, or return ``None``.

    Callables run in the order given — the caller passes the Service
    Bus sender before the asyncpg pool so an in-flight enqueue cannot
    outlive its transport.
    """

    active = [c for c in closes if c is not None]
    if not active:
        return None

    async def _close_all() -> None:
        for close_one in active:
            await close_one()

    return _close_all


def _max_bootstrap_concurrency() -> int:
    """Read the in-process bootstrap concurrency cap from env.

    Defaults to ``2``; clamped to ``>= 1``.  Bounds how many
    background ``bootstrap_fast`` runs the asyncio dispatcher lets
    execute at once on the serving workers.
    """
    raw = os.environ.get(_MAX_CONCURRENCY_ENV, "").strip()
    if not raw:
        return _DEFAULT_MAX_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_CONCURRENCY
    return max(1, value)


@dataclass(frozen=True, slots=True)
class MultiTenantHandles:
    """Bundle returned by :func:`wire_multi_tenant`.

    Attributes:
        resolver: The Phase 3 :class:`TenantResolver`, or ``None``
            when ``TENANT_RESOLVER_ENABLED`` is off.  Kept on the
            bundle so lifespan code that wants to log subsystem
            wiring can introspect it cheaply.
        registry: The backing :class:`PropertyTenantRegistry`,
            or ``None`` when Phase 3 is off.
        trigger: The Phase 4 :class:`AutoBootstrapTrigger`, or
            ``None`` when ``AUTO_BOOTSTRAP_ENABLED`` is off.
        state_store: The Stage 1 ``property_state`` SSoT, or
            ``None`` when ``PROPERTY_STATE_ENABLED`` is off.  Kept
            on the bundle so ``server.py`` can wire the
            request-bootstrap endpoint against the same instance.
        dispatcher: The shared :class:`BootstrapDispatcher` both
            the trigger and the endpoint route work through, or
            ``None`` when ``state_store`` is ``None``.
        close: Async callable to release the asyncpg pool the
            Postgres registry backend opened.  ``None`` for the
            in-memory backend or when Phase 3 is off.
    """

    resolver: TenantResolver | None
    registry: PropertyTenantRegistry | None
    trigger: AutoBootstrapTrigger | None
    state_store: PropertyStateStore | None
    dispatcher: BootstrapDispatcher | None
    close: Callable[[], Awaitable[None]] | None


async def wire_multi_tenant(
    *,
    env_customer_id: str | None,
    env_org_id: str | None,
    env_provider_type: str | None,
    pipeline_getter: Callable[[], OnboardingBootstrapPipeline | None],
    profile_store: PropertyProfileStore,
    unified_data_client: object | None = None,
) -> MultiTenantHandles:
    """Wire Phase 3 + Phase 4 in one shot.

    Args:
        env_customer_id: Pod-default Cendra customer id (consumed
            by the env-default fallback factory).
        env_org_id: Pod-default org id (may be ``None``).
        env_provider_type: Pod-default provider type
            (``"HOSTAWAY"`` / ``"LODGIFY"`` …).
        pipeline_getter: Lazy accessor for the bootstrap pipeline.
            The trigger reads it at fire time so the pipeline can
            be wired before *or* after this call without ordering
            constraints.
        profile_store: The :class:`PropertyProfileStore` the
            trigger consults to decide whether a property has
            already been bootstrapped.

    Returns:
        A :class:`MultiTenantHandles` bundle.  ``close()`` should
        be awaited from the lifespan shutdown branch when set.
    """

    resolver, registry, state_store, close = await wire_tenant_resolver(
        env_customer_id=env_customer_id,
        env_org_id=env_org_id,
        env_provider_type=env_provider_type,
        unified_data_client=unified_data_client,
    )
    # One dispatcher per pod, shared by the Phase 4 trigger and the
    # request-bootstrap endpoint, so both enqueue through the same
    # path.  Only created when the SSoT is enabled — otherwise
    # neither caller routes through ``request_bootstrap``.  The
    # selection (in-process asyncio vs Stage 2 Service Bus producer)
    # lives in ``_build_dispatcher``; its optional cleanup closes the
    # Service Bus transport on shutdown.
    dispatcher: BootstrapDispatcher | None = None
    dispatcher_close: Callable[[], Awaitable[None]] | None = None
    if state_store is not None:
        dispatcher, dispatcher_close = _build_dispatcher()
    trigger = wire_auto_bootstrap_trigger(
        pipeline_getter=pipeline_getter,
        profile_store=profile_store,
        registry=registry,
        state_store=state_store,
        dispatcher=dispatcher,
    )
    return MultiTenantHandles(
        resolver=resolver,
        registry=registry,
        trigger=trigger,
        state_store=state_store,
        dispatcher=dispatcher,
        close=_chain_close(dispatcher_close, close),
    )
