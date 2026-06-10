"""Wiring utilities for the A/B :class:`ExperimentStore`.

Provides :func:`build_experiment_store`, an env-driven factory that
returns either an :class:`InMemoryExperimentStore` (for tests and
Postgres-less environments) or a :class:`PgExperimentStore` (for
production), along with a close callable the caller is expected to
await at application shutdown.

Configuration (env vars):
    EXPERIMENT_STORE_BACKEND
        One of ``memory`` / ``postgres``.  Default ``memory``.
    EXPERIMENT_STORE_DATABASE_URL
        Postgres URI.  Required for the ``postgres`` backend.
        Falls back to ``DATABASE_URL`` to share the URL with the
        decision-case + pattern-rule stores when they all live on
        the same cluster (which they do today).
    EXPERIMENT_STORE_POOL_MIN
        Minimum pool size (default ``2``).
    EXPERIMENT_STORE_POOL_MAX
        Maximum pool size (default ``10``).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from enum import StrEnum

import structlog

from brain_engine.experiments.postgres_store import PgExperimentStore
from brain_engine.experiments.store import (
    ExperimentStore,
    InMemoryExperimentStore,
)

__all__ = [
    "ExperimentStoreBackend",
    "build_experiment_store",
]

logger = structlog.get_logger(__name__)


class ExperimentStoreBackend(StrEnum):
    """Selectable backends for the A/B experiment store."""

    MEMORY = "memory"
    POSTGRES = "postgres"


_ENV_BACKEND: str = "EXPERIMENT_STORE_BACKEND"
_ENV_URL: str = "EXPERIMENT_STORE_DATABASE_URL"
_ENV_URL_FALLBACK: str = "DATABASE_URL"
_ENV_POOL_MIN: str = "EXPERIMENT_STORE_POOL_MIN"
_ENV_POOL_MAX: str = "EXPERIMENT_STORE_POOL_MAX"


CloseCallable = Callable[[], Awaitable[None]]


async def _noop_close() -> None:
    """Default close callable for stores that own no resources."""
    return None


async def build_experiment_store(
    *,
    backend: ExperimentStoreBackend | None = None,
    database_url: str | None = None,
    pool_min: int | None = None,
    pool_max: int | None = None,
) -> tuple[ExperimentStore, CloseCallable]:
    """Assemble an :class:`ExperimentStore` per configuration.

    Each argument defaults to the corresponding environment
    variable when left as ``None``.  The returned close callable
    must be awaited at application shutdown to release any pool
    this factory owns.

    Args:
        backend: Override for the backend selector.
        database_url: Override for the Postgres URI.
        pool_min: Override for the minimum pool size.
        pool_max: Override for the maximum pool size.

    Returns:
        A tuple ``(store, close)`` where ``store`` satisfies the
        :class:`ExperimentStore` Protocol and ``close`` releases
        owned resources when awaited.

    Raises:
        ValueError: When Postgres mode is requested without a URI.
    """
    resolved = backend or _resolve_backend()
    url = database_url or _resolve_database_url()
    min_size, max_size = _resolve_pool_sizes(pool_min, pool_max)

    if resolved is ExperimentStoreBackend.MEMORY:
        logger.info(
            "experiment_store_backend",
            backend=ExperimentStoreBackend.MEMORY.value,
        )
        return InMemoryExperimentStore(), _noop_close

    if url is None:
        raise ValueError(
            "Postgres-backed experiment store requires a database "
            "URL (set EXPERIMENT_STORE_DATABASE_URL or "
            "DATABASE_URL).",
        )

    store = await PgExperimentStore.from_url(
        url,
        min_size=min_size,
        max_size=max_size,
    )
    logger.info(
        "experiment_store_backend",
        backend=ExperimentStoreBackend.POSTGRES.value,
    )
    return store, store.close


def _resolve_backend() -> ExperimentStoreBackend:
    """Read backend selector from env, defaulting to memory."""
    raw = os.getenv(
        _ENV_BACKEND,
        ExperimentStoreBackend.MEMORY.value,
    )
    try:
        return ExperimentStoreBackend(raw.lower())
    except ValueError:
        logger.warning(
            "unknown_experiment_backend_falling_back_to_memory",
            raw=raw,
        )
        return ExperimentStoreBackend.MEMORY


def _resolve_database_url() -> str | None:
    """Resolve the Postgres URI with the documented fallback."""
    return os.getenv(_ENV_URL) or os.getenv(_ENV_URL_FALLBACK)


def _resolve_pool_sizes(
    pool_min: int | None,
    pool_max: int | None,
) -> tuple[int, int]:
    """Resolve pool sizes with environment-driven defaults."""
    min_size = (
        pool_min
        if pool_min is not None
        else _read_int(_ENV_POOL_MIN, 2)
    )
    max_size = (
        pool_max
        if pool_max is not None
        else _read_int(_ENV_POOL_MAX, 10)
    )
    return min_size, max_size


def _read_int(name: str, default: int) -> int:
    """Read an integer env var, falling back on malformed input."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid_int_env_var", name=name, raw=raw)
        return default
