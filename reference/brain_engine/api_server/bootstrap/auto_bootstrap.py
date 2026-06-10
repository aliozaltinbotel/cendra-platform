"""Lifespan wiring for the Phase 4 auto-bootstrap trigger.

Constructs an :class:`AutoBootstrapTrigger` bound to the running
:class:`OnboardingBootstrapPipeline` and
:class:`PropertyProfileStore`, then publishes it into the runtime
singleton so :class:`TenantResolverMiddleware` fires it after every
successful tenant resolution.

The trigger uses a *getter* for the pipeline rather than a direct
reference because the pipeline is constructed late in the lifespan
(after every store / harvester / event-bus has been wired) while
the trigger itself can be set up earlier alongside the tenant
resolver.  Reading the pipeline at fire time also lets the trigger
stay a no-op if the pipeline was disabled by env var.

Phase 4 is fully opt-in:

* ``AUTO_BOOTSTRAP_ENABLED=true`` flips the trigger on; default
  off — middleware stays a no-op pass-through for this hook.
* ``AUTO_BOOTSTRAP_DAYS`` overrides the look-back window passed to
  ``bootstrap_fast`` (default 30, capped to ``[1, 730]`` by the
  pipeline itself).
* ``AUTO_BOOTSTRAP_LIMIT`` reserved for future per-property cap
  wiring; currently read for forward compatibility only.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import structlog

from brain_engine.tenants import (
    AutoBootstrapTrigger,
    PropertyTenantRegistry,
    configure_auto_bootstrap_trigger,
)

if TYPE_CHECKING:
    from brain_engine.onboarding.bootstrap_pipeline import (
        OnboardingBootstrapPipeline,
    )
    from brain_engine.profiles.store import PropertyProfileStore
    from brain_engine.tenants import BootstrapDispatcher, PropertyStateStore

logger = structlog.get_logger(__name__)


_ENABLED_ENV: Final[str] = "AUTO_BOOTSTRAP_ENABLED"
_DAYS_ENV: Final[str] = "AUTO_BOOTSTRAP_DAYS"
_COOLDOWN_HOURS_ENV: Final[str] = "AUTO_BOOTSTRAP_COOLDOWN_HOURS"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def auto_bootstrap_enabled() -> bool:
    """Return ``True`` when Phase 4 auto-bootstrap is turned on."""
    raw = os.environ.get(_ENABLED_ENV, "").strip().lower()
    return raw in _TRUTHY


def wire_auto_bootstrap_trigger(
    *,
    pipeline_getter: Callable[[], OnboardingBootstrapPipeline | None],
    profile_store: PropertyProfileStore,
    registry: PropertyTenantRegistry | None,
    state_store: PropertyStateStore | None = None,
    dispatcher: BootstrapDispatcher | None = None,
) -> AutoBootstrapTrigger | None:
    """Build + publish the trigger.  No-op when feature flag is off.

    Returns the constructed trigger so the lifespan can keep a
    handle for shutdown (currently unused — the trigger holds no
    resources of its own, all I/O flows through the borrowed
    pipeline + profile store + registry).

    When ``state_store`` is supplied (``PROPERTY_STATE_ENABLED``
    on) the trigger routes ``maybe_fire`` through the shared
    ``request_bootstrap`` dedup path instead of its legacy in-proc
    ``asyncio.create_task``; ``None`` keeps the pre-Stage-1
    behaviour.
    """

    if not auto_bootstrap_enabled():
        logger.info("auto_bootstrap.disabled")
        return None
    if registry is None:
        # The trigger needs the registry for its cooldown column.
        # Phase 3 not wired → Phase 4 cannot run.
        logger.warning("auto_bootstrap.disabled_no_registry")
        return None

    days = _read_int_env(_DAYS_ENV, default=730, minimum=1)
    cooldown_hours = _read_int_env(
        _COOLDOWN_HOURS_ENV, default=1, minimum=1,
    )
    trigger = AutoBootstrapTrigger(
        pipeline_getter=pipeline_getter,
        profile_store=profile_store,
        registry=registry,
        bootstrap_days=days,
        cooldown=timedelta(hours=cooldown_hours),
        state_store=state_store,
        dispatcher=dispatcher,
    )
    configure_auto_bootstrap_trigger(trigger)
    logger.info(
        "auto_bootstrap.wired",
        days=days,
        cooldown_hours=cooldown_hours,
        property_state=state_store is not None,
    )
    return trigger


def _read_int_env(name: str, *, default: int, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "auto_bootstrap.invalid_int_env",
            name=name,
            raw=raw,
            default=default,
        )
        return default
    return max(minimum, value)
