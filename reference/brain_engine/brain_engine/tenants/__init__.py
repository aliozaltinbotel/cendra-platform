"""Phase 3 — Property → Tenant auto-resolution.

Public surface: every other module that needs tenant scope should
import from this package rather than reach into the internal
submodules.  The split into ``models`` / ``context`` /
``registry_store`` / ``resolver`` / ``middleware`` is purely an
organisational decision — callers see a single namespace.
"""

from __future__ import annotations

from brain_engine.tenants.auto_bootstrap import (
    AutoBootstrapTrigger,
    PipelineGetter,
)
from brain_engine.tenants.bootstrap_intent import (
    AsyncioBootstrapDispatcher,
    BootstrapDispatcher,
    BootstrapIntentResult,
    BootstrapWorkload,
    request_bootstrap,
)
from brain_engine.tenants.bootstrap_message import BootstrapIntentMessage
from brain_engine.tenants.bootstrap_reaper import BootstrapReaper
from brain_engine.tenants.bootstrap_runner import (
    BootstrapRunner,
    submit_bootstrap_intent,
)
from brain_engine.tenants.context import bind_tenant, current_tenant
from brain_engine.tenants.freshness import (
    mark_stale,
    submit_refresh_intent,
)
from brain_engine.tenants.lazy_probe import GraphQLLazyProbe
from brain_engine.tenants.middleware import TenantResolverMiddleware
from brain_engine.tenants.models import (
    TENANT_SOURCE_BOOTSTRAP,
    TENANT_SOURCE_ENV_DEFAULT,
    TENANT_SOURCE_LAZY,
    TENANT_SOURCE_MANUAL,
    TENANT_SOURCE_REGISTRY,
    TENANT_SOURCE_REQUEST_BODY,
    TENANT_SOURCE_SYNC,
    TenantContext,
)
from brain_engine.tenants.property_state import (
    ALLOWED_PROPERTY_STATUSES,
    PROPERTY_STATUS_COLD,
    PROPERTY_STATUS_FAILED,
    PROPERTY_STATUS_PRIMED,
    PROPERTY_STATUS_QUEUED,
    PROPERTY_STATUS_STALE,
    PROPERTY_STATUS_WARMING,
    PropertyState,
)
from brain_engine.tenants.property_state_postgres import (
    PostgresPropertyStateStore,
)
from brain_engine.tenants.property_state_store import (
    InMemoryPropertyStateStore,
    PropertyStateNotFoundError,
    PropertyStateStore,
)
from brain_engine.tenants.registry_store import (
    InMemoryPropertyTenantRegistry,
    PostgresPropertyTenantRegistry,
    PropertyTenantRegistry,
)
from brain_engine.tenants.resolver import (
    EnvDefaultFactory,
    LazyTenantProbe,
    TenantResolver,
)
from brain_engine.tenants.runtime import (
    active_auto_bootstrap_trigger,
    active_tenant_resolver,
    configure_auto_bootstrap_trigger,
    configure_tenant_resolver,
)
from brain_engine.tenants.service_bus_dispatcher import (
    QueueSender,
    ServiceBusBootstrapDispatcher,
)
from brain_engine.tenants.stale_sweeper import StaleSweeper, SweepResult

__all__ = [
    "ALLOWED_PROPERTY_STATUSES",
    "PROPERTY_STATUS_COLD",
    "PROPERTY_STATUS_FAILED",
    "PROPERTY_STATUS_PRIMED",
    "PROPERTY_STATUS_QUEUED",
    "PROPERTY_STATUS_STALE",
    "PROPERTY_STATUS_WARMING",
    "TENANT_SOURCE_BOOTSTRAP",
    "TENANT_SOURCE_ENV_DEFAULT",
    "TENANT_SOURCE_LAZY",
    "TENANT_SOURCE_MANUAL",
    "TENANT_SOURCE_REGISTRY",
    "TENANT_SOURCE_REQUEST_BODY",
    "TENANT_SOURCE_SYNC",
    "AsyncioBootstrapDispatcher",
    "AutoBootstrapTrigger",
    "BootstrapDispatcher",
    "BootstrapIntentMessage",
    "BootstrapIntentResult",
    "BootstrapReaper",
    "BootstrapRunner",
    "BootstrapWorkload",
    "EnvDefaultFactory",
    "GraphQLLazyProbe",
    "InMemoryPropertyStateStore",
    "InMemoryPropertyTenantRegistry",
    "LazyTenantProbe",
    "PipelineGetter",
    "PostgresPropertyStateStore",
    "PostgresPropertyTenantRegistry",
    "PropertyState",
    "PropertyStateNotFoundError",
    "PropertyStateStore",
    "PropertyTenantRegistry",
    "QueueSender",
    "ServiceBusBootstrapDispatcher",
    "StaleSweeper",
    "SweepResult",
    "TenantContext",
    "TenantResolver",
    "TenantResolverMiddleware",
    "active_auto_bootstrap_trigger",
    "active_tenant_resolver",
    "bind_tenant",
    "configure_auto_bootstrap_trigger",
    "configure_tenant_resolver",
    "current_tenant",
    "mark_stale",
    "request_bootstrap",
    "submit_bootstrap_intent",
    "submit_refresh_intent",
]
