"""Phase 4 — fire-and-forget bootstrap on first property touch.

When a Sandbox UI request hits a property the brain has never
bootstrapped (no ``PropertyProfile`` row, no decision cases), Phase
3's tenant resolver still returns a sensible
:class:`TenantContext` — but the downstream pipeline has no
patterns / profile / history to draw on so the response degrades.

:class:`AutoBootstrapTrigger` closes that gap: after the middleware
binds the :class:`TenantContext`, it asks the trigger whether the
property is "primed".  If not, the trigger fires
``pipeline.bootstrap_fast(...)`` in the background and returns
immediately — the current request goes through with whatever data
exists, and within ~10-20 minutes (the trigger defaults to a 2-year
look-back window) the property is fully bootstrapped for every
subsequent request.

Dedup layers, checked in order:

  1. In-process ``set[str]`` guarded by :class:`asyncio.Lock` —
     blocks concurrent fires within a single pod.
  2. Postgres ``last_auto_attempted_at`` column on
     ``property_tenant_registry`` (migration 033) — multi-pod and
     restart-safe cooldown that prevents eternal re-fires for
     properties that genuinely produce no data on bootstrap (the
     edge case where the harvester cannot write a profile because
     unified-data GraphQL returns no detail).
  3. ``PropertyProfileStore.get`` — once the harvester has written
     a profile the property is considered fully primed.

The cooldown default is 1 hour; tunable via the constructor (and
the ``AUTO_BOOTSTRAP_COOLDOWN_HOURS`` env var on the wire side).

Stage 1 supersedes all three layers: when a
:class:`PropertyStateStore` is wired (``PROPERTY_STATE_ENABLED``
on) :meth:`maybe_fire` delegates to
:func:`submit_bootstrap_intent`, whose ``request_bootstrap``
core owns the dedup against the ``property_state`` SSoT
(primed-fresh / in-flight / insert-race).  The in-proc set,
registry cooldown, and profile probe are bypassed on that path so
the UI endpoint, this trigger, and the future webhook all dedup
through one Postgres-backed status machine instead of three
divergent signals.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

from brain_engine.tenants.bootstrap_intent import AsyncioBootstrapDispatcher
from brain_engine.tenants.bootstrap_runner import submit_bootstrap_intent
from brain_engine.tenants.models import (
    TENANT_SOURCE_ENV_DEFAULT,
    TenantContext,
)

if TYPE_CHECKING:
    from brain_engine.onboarding.bootstrap_pipeline import (
        OnboardingBootstrapPipeline,
    )
    from brain_engine.profiles.store import PropertyProfileStore
    from brain_engine.tenants.bootstrap_intent import BootstrapDispatcher
    from brain_engine.tenants.property_state_store import PropertyStateStore
    from brain_engine.tenants.registry_store import PropertyTenantRegistry

__all__ = ["AutoBootstrapTrigger", "PipelineGetter"]


logger = structlog.get_logger(__name__)


#: Lazy accessor for the configured bootstrap pipeline.  The
#: pipeline is wired by ``api_server/server.py`` lifespan *after*
#: the trigger itself is instantiated, so the trigger stores a
#: callable that resolves the pipeline at fire time rather than a
#: direct reference.  Returning ``None`` skips the fire — useful
#: when the trigger ships before the pipeline does.
PipelineGetter = Callable[[], "OnboardingBootstrapPipeline | None"]


#: Default window the auto-trigger asks ``bootstrap_fast`` to
#: scan — 730 days ≈ 2 years, which is the effective max the
#: unified-data gateway will honour.  The hypothesis behind the
#: large default: a Sandbox UI operator who picks a property
#: should see ALL of its historical conversations / patterns /
#: profile after the background bootstrap, not just the last 30
#: days.  Tunable via ``AUTO_BOOTSTRAP_DAYS``.
_DEFAULT_BOOTSTRAP_DAYS: Final[int] = 730

#: Default cooldown — prevents the trigger from re-firing on every
#: request for a property that consistently bootstraps with no
#: data (eg unified-data GraphQL has no detail row for the
#: ``(customer_id, provider_type, channel_entity_id)`` triplet).
#: Tunable via ``AUTO_BOOTSTRAP_COOLDOWN_HOURS``.
_DEFAULT_COOLDOWN: Final[timedelta] = timedelta(hours=1)


class AutoBootstrapTrigger:
    """Decide whether to fire a background bootstrap for a property."""

    def __init__(
        self,
        pipeline_getter: PipelineGetter,
        profile_store: PropertyProfileStore,
        registry: PropertyTenantRegistry,
        *,
        bootstrap_days: int = _DEFAULT_BOOTSTRAP_DAYS,
        cooldown: timedelta = _DEFAULT_COOLDOWN,
        state_store: PropertyStateStore | None = None,
        dispatcher: BootstrapDispatcher | None = None,
    ) -> None:
        self._pipeline_getter = pipeline_getter
        self._profile_store = profile_store
        self._registry = registry
        self._bootstrap_days = max(1, int(bootstrap_days))
        # Allow ``cooldown=timedelta(0)`` to opt out — tests
        # exercise the dedup paths without waiting and operators
        # may want to disable cooldown for diagnostic windows.
        self._cooldown = max(timedelta(0), cooldown)
        self._pending: set[str] = set()
        self._lock = asyncio.Lock()
        # Stage 1 SSoT wiring.  When ``state_store`` is supplied
        # ``maybe_fire`` routes through ``request_bootstrap`` (the
        # shared dedup path) and the three legacy dedup layers
        # above become dead weight; ``None`` keeps the pre-Stage-1
        # in-proc behaviour byte-for-byte.  A default dispatcher is
        # synthesised so callers only have to opt in with the store.
        self._state_store = state_store
        self._dispatcher: BootstrapDispatcher | None = (
            dispatcher
            if dispatcher is not None
            else (AsyncioBootstrapDispatcher() if state_store is not None else None)
        )

    async def maybe_fire(
        self,
        property_channel_id: str,
        tenant_context: TenantContext,
    ) -> bool:
        """Fire bootstrap if the property is unknown and not in-flight.

        Returns:
            ``True`` if a background bootstrap task was scheduled,
            ``False`` if the trigger declined (already bootstrapped,
            in cooldown window, already in flight, no tenant, or
            pipeline not wired).
        """

        if not property_channel_id:
            return False
        if tenant_context.source == TENANT_SOURCE_ENV_DEFAULT:
            return False
        if not tenant_context.customer_id:
            return False

        if self._state_store is not None:
            return await self._fire_via_intent(
                property_channel_id, tenant_context,
            )

        async with self._lock:
            if property_channel_id in self._pending:
                return False
            if await self._inside_cooldown(property_channel_id):
                return False
            existing = await self._profile_store.get(property_channel_id)
            if existing is not None:
                return False
            pipeline = self._pipeline_getter()
            if pipeline is None:
                return False
            self._pending.add(property_channel_id)

        # Fire-and-forget — the task captures its own reference so
        # Python's task GC does not collect it before completion.
        task = asyncio.create_task(
            self._run(pipeline, property_channel_id, tenant_context),
            name=f"auto-bootstrap-{property_channel_id}",
        )
        task.add_done_callback(
            lambda _t: self._pending.discard(property_channel_id),
        )
        return True

    async def _fire_via_intent(
        self,
        property_channel_id: str,
        tenant_context: TenantContext,
    ) -> bool:
        """Stage 1 path — delegate dedup + dispatch to the SSoT.

        ``request_bootstrap`` owns all three dedup layers
        (primed-fresh / in-flight / insert-race) against
        ``property_state``, so the legacy in-proc set, the registry
        cooldown, and the profile-store probe are all bypassed
        here.  Returns ``True`` only when this call actually
        enqueued new work; a dedup short-circuit returns ``False``.
        """

        pipeline = self._pipeline_getter()
        if pipeline is None:
            return False
        # ``_dispatcher`` is always set when ``_state_store`` is —
        # the constructor synthesises a default — but assert the
        # invariant for the type checker rather than silently
        # passing ``None`` into the dispatcher contract.
        if self._dispatcher is None:
            return False
        result = await submit_bootstrap_intent(
            property_channel_id=property_channel_id,
            tenant=tenant_context,
            pipeline=pipeline,
            state_store=self._state_store,
            dispatcher=self._dispatcher,
            window_days=self._bootstrap_days,
            reason="first_touch",
            already_primed_probe=self._profile_already_built,
        )
        return result.enqueued

    async def _profile_already_built(self, property_channel_id: str) -> bool:
        """Adopt-existing probe — True when a profile already exists.

        Lets :func:`request_bootstrap` short-circuit a property that
        a pre-SSoT bootstrap already primed (its profile is in the
        store) into a ``primed`` row instead of re-running the heavy
        pipeline.  Without this the empty ``property_state`` table
        makes every already-warmed property look cold and triggers a
        re-bootstrap storm on first touch.
        """
        existing = await self._profile_store.get(property_channel_id)
        return existing is not None

    async def _inside_cooldown(self, property_channel_id: str) -> bool:
        last = await self._registry.get_last_auto_attempt(
            property_channel_id,
        )
        if last is None:
            return False
        if last.tzinfo is None:
            # Defensive: treat naive timestamps as UTC so the
            # cooldown math does not silently drift by the
            # process timezone offset.
            last = last.replace(tzinfo=UTC)
        return (datetime.now(UTC) - last) < self._cooldown

    async def _run(
        self,
        pipeline: OnboardingBootstrapPipeline,
        property_channel_id: str,
        tenant_context: TenantContext,
    ) -> None:
        logger.info(
            "auto_bootstrap.dispatching",
            property_channel_id=property_channel_id,
            customer_id=tenant_context.customer_id,
            provider_type=tenant_context.provider_type,
            days=self._bootstrap_days,
        )
        try:
            report = await pipeline.bootstrap_fast(
                property_id=property_channel_id,
                days=self._bootstrap_days,
                mine_patterns_inline=False,
                dry_run=False,
                customer_id_override=tenant_context.customer_id,
                org_id_override=tenant_context.org_id,
                provider_type_override=tenant_context.provider_type,
            )
        except Exception as exc:
            logger.warning(
                "auto_bootstrap.failed",
                property_channel_id=property_channel_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await self._record_attempt_silently(property_channel_id)
            return
        logger.info(
            "auto_bootstrap.completed",
            property_channel_id=property_channel_id,
            conversations=report.conversations_loaded,
            cases=report.cases_extracted,
            rules=report.rules_emitted,
        )
        await self._record_attempt_silently(property_channel_id)

    async def _record_attempt_silently(
        self,
        property_channel_id: str,
    ) -> None:
        try:
            await self._registry.record_auto_attempt(property_channel_id)
        except Exception as exc:
            logger.warning(
                "auto_bootstrap.cooldown_write_failed",
                property_channel_id=property_channel_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
