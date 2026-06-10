"""Option B dependency assembly for the bootstrap worker.

Rebuilds — from the same environment variables the FastAPI lifespan
reads — only the dependencies the bootstrap pipeline needs, then
returns a ready :class:`WorkerContext` (pipeline + ``property_state``
SSoT + a single async ``close``).

It deliberately does **not** boot the FastAPI app or call
``FullSystem.initialize()``: the worker must not start the server's
background jobs (orphan reaper, nightly scheduler, auto-bootstrap
trigger) or the generic durable worker pool.  Memory tiers connect
lazily on first write through the fan-out, so seeding procedural
memory is unnecessary here.  This isolation is why Stage 2 ships the
worker as Option B (dedicated assembly) rather than re-running the
whole lifespan in worker mode.

Each builder mirrors a specific block of ``api_server/server.py``;
the mirrored line ranges are cited inline so the two stay auditable.
The shared store factories (``build_decision_case_store`` /
``build_pattern_rule_store``) are reused directly.  The inline store
selections (profile / sandbox / generator / foundation / fan-out) are
not yet extracted from the lifespan, so they are replicated here and
flagged for a future shared-builder follow-up — replicated, never
diverged.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import structlog
from fastapi import FastAPI

from api_server.bootstrap.elasticsearch import wire as wire_elasticsearch
from api_server.bootstrap.onboarding import wire as wire_onboarding
from api_server.bootstrap.pipeline_factory import build_bootstrap_pipeline
from api_server.bootstrap.unified_data import wire as wire_unified_data
from brain_engine.memory.factory import create_full_system
from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
)
from brain_engine.patterns.wiring import (
    build_decision_case_store,
    build_pattern_rule_store,
)
from brain_engine.profiles import (
    InMemoryPropertyProfileStore,
    PgPropertyProfileStore,
    PropertyProfileStore,
)
from brain_engine.sandbox import (
    InMemoryUnansweredThreadStore,
    PgUnansweredThreadStore,
)
from brain_engine.tenants import PostgresPropertyStateStore, PropertyStateStore
from config.settings import Settings
from workers._bootstrap_collaborators import (
    build_foundation_orchestrator,
    build_sandbox_generator,
)

__all__ = ["WorkerContext", "build_worker_context"]


logger = structlog.get_logger(__name__)


_DATABASE_URL_ENV: Final[str] = "DATABASE_URL"
_STATE_DB_URL_ENV: Final[str] = "TENANT_REGISTRY_DATABASE_URL"
_TIMEOUT_ENV: Final[str] = "BOOTSTRAP_WORKER_TIMEOUT_SECONDS"
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 1200.0  # 20 min, mirrors runner


@dataclass(frozen=True, slots=True)
class WorkerContext:
    """Everything the worker's message handler needs, plus teardown.

    Attributes:
        pipeline: The assembled bootstrap pipeline.
        state_store: The Postgres ``property_state`` SSoT — the same
            table the server's producer writes ``queued`` rows to.
        timeout_seconds: Hard ceiling for a single ``bootstrap_fast``
            run, forwarded to the runner.
        close: Releases every pool / client opened during assembly,
            in reverse dependency order.
    """

    pipeline: OnboardingBootstrapPipeline
    state_store: PropertyStateStore
    timeout_seconds: float | None
    close: Callable[[], Awaitable[None]]


async def build_worker_context() -> WorkerContext:
    """Assemble the worker's pipeline + SSoT from the environment.

    Raises:
        RuntimeError: when no Postgres connection string is configured
            — the worker is useless without the shared SSoT, so it
            fails loudly rather than silently using an isolated
            in-memory store that can never see the server's rows.
    """

    settings = Settings()
    closes: list[Callable[[], Awaitable[None]]] = []

    state_store, state_close = await _build_state_store()
    closes.append(state_close)

    # FullSystem gives the same redis client + memory tiers the server
    # uses (server.py:1826).  ``initialize()`` is intentionally skipped
    # — see the module docstring.
    full_system = create_full_system(
        redis_url=settings.redis_url,
        qdrant_url=settings.qdrant_url,
        llm_model=settings.llm_model,
    )
    closes.append(_redis_close(full_system.redis_client))
    memory_fanout = _build_memory_fanout(full_system.memory)

    case_store, case_close = await build_decision_case_store()
    closes.append(case_close)
    rule_store, rule_close = await build_pattern_rule_store()
    closes.append(rule_close)

    profile_store, profile_close = await _build_profile_store()
    if profile_close is not None:
        closes.append(profile_close)
    sandbox_store, sandbox_close = await _build_sandbox_store()
    if sandbox_close is not None:
        closes.append(sandbox_close)
    sandbox_generator = build_sandbox_generator(profile_store)
    foundation_orchestrator = await build_foundation_orchestrator()

    # The onboarding wire builds the GraphQL archive loader + profile
    # harvester from the unified-data client.  It writes to a throwaway
    # FastAPI ``state`` (worker process is isolated; the global
    # ``configure_profile_deps`` mutation is harmless here).
    holder = FastAPI()
    (
        unified_client,
        unified_customer_id,
        unified_org_id,
        unified_provider_type,
    ) = wire_unified_data(holder)
    if unified_client is not None:
        closes.append(unified_client.aclose)
    # Wire the Elasticsearch property reader onto the same holder BEFORE
    # onboarding so the harvester it builds picks up the ES overlay.  The
    # worker is the real harvest path under BOOTSTRAP_QUEUE_ENABLED, so
    # without this the API server's ES wiring would never run.  Returns
    # None (flag off / no key / init error) → GraphQL-only harvest.
    es_reader = wire_elasticsearch(holder)
    if es_reader is not None:
        es_client = getattr(holder.state, "elasticsearch_client", None)
        if es_client is not None:
            closes.append(es_client.aclose)
    archive_loader, _onboarding_service, profile_harvester = wire_onboarding(
        holder,
        unified_data_client=unified_client,
        unified_customer_id=unified_customer_id or None,
        unified_org_id=unified_org_id,
        unified_provider_type=unified_provider_type,
        case_store=case_store,
        property_profile_store=profile_store,
        card_store=None,
    )
    if archive_loader is None:
        # No archive loader = no conversations to mine.  The server
        # leaves the pipeline unwired (the bootstrap endpoint 503s);
        # the worker would have nothing to do, so fail loudly rather
        # than silently complete messages.  Usually means the
        # UNIFIED_DATA_* env trio is unset in the worker pod.
        raise RuntimeError(
            "bootstrap worker requires a GraphQL archive loader: set "
            "UNIFIED_DATA_CUSTOMER_ID (and its UNIFIED_DATA_* peers)",
        )

    pipeline, _job_store = build_bootstrap_pipeline(
        archive_loader=archive_loader,
        case_store=case_store,
        rule_store=rule_store,
        profile_harvester=profile_harvester,
        sandbox_generator=sandbox_generator,
        sandbox_store=sandbox_store,
        foundation_orchestrator=foundation_orchestrator,
        memory_fanout=memory_fanout,
        profile_customer_id=unified_customer_id or "",
        profile_org_id=unified_org_id or "",
        profile_provider_type=unified_provider_type or "",
        redis_client=full_system.redis_client,
    )

    logger.info(
        "bootstrap_worker.context_built",
        customer_id=unified_customer_id or "—",
        provider_type=unified_provider_type or "—",
        archive_loader=getattr(archive_loader, "name", None),
        rule_store=rule_store is not None,
        foundation=foundation_orchestrator is not None,
    )
    return WorkerContext(
        pipeline=pipeline,
        state_store=state_store,
        timeout_seconds=_timeout_seconds(),
        close=_chain_close(closes),
    )


async def _build_state_store() -> tuple[
    PropertyStateStore,
    Callable[[], Awaitable[None]],
]:
    """Open the Postgres ``property_state`` pool (mirrors tenant_resolver).

    Unlike the server — which may run with the SSoT disabled — the
    worker has nothing to do without it, so a missing URL is fatal.
    """

    url = os.environ.get(_STATE_DB_URL_ENV) or os.environ.get(
        _DATABASE_URL_ENV,
    )
    if not url:
        raise RuntimeError(
            "bootstrap worker requires a Postgres state store: set "
            f"{_STATE_DB_URL_ENV} or {_DATABASE_URL_ENV}",
        )
    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=url,
        min_size=1,
        max_size=5,
        command_timeout=10,
    )

    async def close() -> None:
        await pool.close()

    return PostgresPropertyStateStore(pool), close


async def _build_profile_store() -> tuple[
    PropertyProfileStore,
    Callable[[], Awaitable[None]] | None,
]:
    """Select the profile store backend (mirrors server.py:1978-2007)."""

    backend = os.getenv("PROPERTY_PROFILE_STORE_BACKEND", "memory").lower()
    if backend == "postgres":
        url = os.getenv("PROPERTY_PROFILE_STORE_DATABASE_URL") or os.getenv(
            _DATABASE_URL_ENV,
        )
        if url:
            store = await PgPropertyProfileStore.from_url(url)
            return store, store.close
        logger.warning("bootstrap_worker.profile_store_no_url_fallback")
    return InMemoryPropertyProfileStore(), None


async def _build_sandbox_store() -> tuple[
    Any,
    Callable[[], Awaitable[None]] | None,
]:
    """Select the unanswered-thread store (mirrors server.py:2272-2301)."""

    backend = os.getenv("SANDBOX_STORE_BACKEND", "memory").lower()
    if backend == "postgres":
        url = os.getenv("SANDBOX_STORE_DATABASE_URL") or os.getenv(
            _DATABASE_URL_ENV,
        )
        if url:
            store = await PgUnansweredThreadStore.from_url(url)
            return store, store.close
        logger.warning("bootstrap_worker.sandbox_store_no_url_fallback")
    return InMemoryUnansweredThreadStore(), None


def _build_memory_fanout(memory: Any) -> Any:
    """Build the shared fan-out from memory tiers (server.py:1688-1712)."""

    from brain_engine.memory.fanout import MemoryFanOut, NullMemoryFanOut

    if memory is not None and (
        getattr(memory, "episodic", None) is not None
        or getattr(memory, "semantic", None) is not None
        or getattr(memory, "knowledge_graph", None) is not None
    ):
        return MemoryFanOut(
            episodic=getattr(memory, "episodic", None),
            semantic=getattr(memory, "semantic", None),
            knowledge_graph=getattr(memory, "knowledge_graph", None),
        )
    return NullMemoryFanOut()


def _redis_close(client: Any) -> Callable[[], Awaitable[None]]:
    """Wrap a redis.asyncio client's close in the cleanup signature."""

    async def close() -> None:
        aclose = getattr(client, "aclose", None) or getattr(
            client,
            "close",
            None,
        )
        if aclose is not None:
            await aclose()

    return close


def _timeout_seconds() -> float | None:
    """Read the per-run bootstrap ceiling from env (default 20 min)."""

    raw = os.environ.get(_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else None


def _chain_close(
    closes: list[Callable[[], Awaitable[None]]],
) -> Callable[[], Awaitable[None]]:
    """Combine cleanups, run in reverse order, swallowing failures."""

    async def close_all() -> None:
        for close_one in reversed(closes):
            try:
                await close_one()
            except Exception as exc:  # best-effort teardown
                logger.warning(
                    "bootstrap_worker.close_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    return close_all
