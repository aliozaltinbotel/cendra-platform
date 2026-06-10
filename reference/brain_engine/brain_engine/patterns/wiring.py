"""Wiring utilities for :class:`DecisionCaseStore` and :class:`PatternRuleStore`.

Provides two entry points — :func:`build_decision_case_store` and
:func:`build_pattern_rule_store` — that select between the in-memory
reference stores and their Postgres-backed production counterparts
based on environment-driven backend flags.

Also ships :class:`DualWriteDecisionCaseStore`, a Protocol-satisfying
wrapper that writes every case to two stores (typically in-memory as
the primary and Postgres as a shadow) so that migration can roll out
without any reader-side risk: reads continue to come from the primary
until operators flip to ``postgres`` mode.  PatternRules do not use a
dual-write mode: rules are *mutable* and a split brain across two
backends would produce inconsistent confidence scores.

Configuration (env vars):
    DECISION_CASE_STORE_BACKEND
        One of ``memory`` / ``postgres`` / ``dual``.  Default ``memory``.
    PATTERN_RULE_STORE_BACKEND
        One of ``memory`` / ``postgres``.  Default ``memory``.
    DECISION_CASE_STORE_DATABASE_URL
        Postgres URI.  Required for any ``postgres``/``dual`` mode.
        Falls back to ``DATABASE_URL``.
    DECISION_CASE_STORE_POOL_MIN
        Minimum pool size (default ``2``).
    DECISION_CASE_STORE_POOL_MAX
        Maximum pool size (default ``10``).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from enum import StrEnum

import structlog

from brain_engine.patterns.models import BookingStage, DecisionCase, Scenario
from brain_engine.patterns.postgres_rule_store import PostgresPatternRuleStore
from brain_engine.patterns.postgres_store import PostgresDecisionCaseStore
from brain_engine.patterns.store import (
    DecisionCaseStore,
    InMemoryDecisionCaseStore,
    InMemoryPatternRuleStore,
    PatternRuleStore,
)

logger = structlog.get_logger(__name__)


def _shadow_write_exception_types() -> tuple[type[BaseException], ...]:
    """Return the tuple of exception classes ``_write_shadow`` catches.

    ``asyncpg`` is an optional dependency — imported lazily so that
    environments that run with the in-memory backend (tests, dev) do
    not require the driver.  The returned tuple always includes
    :class:`OSError` and :class:`ConnectionError` for socket-level
    faults, and appends asyncpg-specific operational errors when the
    driver is installed so that SQL and pool failures on a Postgres
    shadow are treated as best-effort.
    """
    types: list[type[BaseException]] = [OSError, ConnectionError]
    try:
        import asyncpg
    except ImportError:
        return tuple(types)
    types.extend([asyncpg.PostgresError, asyncpg.InterfaceError])
    return tuple(types)


_SHADOW_WRITE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    _shadow_write_exception_types()
)


# ---------------------------------------------------------------------------
# Backend selector
# ---------------------------------------------------------------------------

class DecisionCaseStoreBackend(StrEnum):
    """Selectable backends for the decision-case store."""

    MEMORY = "memory"
    POSTGRES = "postgres"
    DUAL = "dual"


_ENV_BACKEND: str = "DECISION_CASE_STORE_BACKEND"
_ENV_RULE_BACKEND: str = "PATTERN_RULE_STORE_BACKEND"
_ENV_URL: str = "DECISION_CASE_STORE_DATABASE_URL"
_ENV_URL_FALLBACK: str = "DATABASE_URL"
_ENV_POOL_MIN: str = "DECISION_CASE_STORE_POOL_MIN"
_ENV_POOL_MAX: str = "DECISION_CASE_STORE_POOL_MAX"


CloseCallable = Callable[[], Awaitable[None]]


async def _noop_close() -> None:
    """Default close callable for stores that do not own resources."""
    return None


# ---------------------------------------------------------------------------
# Dual-write store
# ---------------------------------------------------------------------------

class DualWriteDecisionCaseStore:
    """Write to two stores, read from the primary.

    Every mutating operation is awaited on the primary first and then
    on the shadow.  Shadow failures are logged but never propagate:
    the primary is authoritative by contract, and the shadow exists
    solely to validate the migration path (e.g. Postgres schema
    correctness, data volume, query latency).

    Read operations are served exclusively from the primary so that
    consumers observe a consistent view throughout the migration.

    Attributes:
        _primary: The authoritative store.
        _shadow: The mirror store that receives best-effort writes.
        _log: Structured logger bound to this component.
    """

    def __init__(
        self,
        primary: DecisionCaseStore,
        shadow: DecisionCaseStore,
    ) -> None:
        self._primary = primary
        self._shadow = shadow
        self._log = structlog.get_logger(__name__).bind(
            component="dual_write_case_store",
        )

    async def store(self, case: DecisionCase) -> str:
        """Write to both stores; primary result is authoritative."""
        primary_id = await self._primary.store(case)
        await self._write_shadow(case)
        return primary_id

    async def get(self, case_id: str) -> DecisionCase | None:
        """Read from the primary only."""
        return await self._primary.get(case_id)

    async def search(
        self,
        *,
        scenario: Scenario | None = None,
        property_id: str | None = None,
        owner_id: str | None = None,
        stage: BookingStage | None = None,
        limit: int = 100,
    ) -> list[DecisionCase]:
        """Delegate search to the primary store."""
        return await self._primary.search(
            scenario=scenario,
            property_id=property_id,
            owner_id=owner_id,
            stage=stage,
            limit=limit,
        )

    async def get_by_reservation(
        self,
        reservation_id: str,
    ) -> list[DecisionCase]:
        """Delegate reservation lookup to the primary store."""
        return await self._primary.get_by_reservation(reservation_id)

    async def count(
        self,
        *,
        scenario: Scenario | None = None,
        property_id: str | None = None,
    ) -> int:
        """Delegate counting to the primary store."""
        return await self._primary.count(
            scenario=scenario,
            property_id=property_id,
        )

    async def _write_shadow(self, case: DecisionCase) -> None:
        """Best-effort write to the shadow store.

        Catches operational failures — socket-level errors plus
        asyncpg's ``PostgresError`` / ``InterfaceError`` when the
        driver is installed — and logs them without raising so the
        primary write (already completed) remains the authoritative
        result.  Programming errors (``TypeError``, ``ValueError``, …)
        still propagate so genuine bugs surface during migration.
        """
        try:
            await self._shadow.store(case)
        except _SHADOW_WRITE_EXCEPTIONS as exc:
            self._log.warning(
                "shadow_write_failed",
                case_id=case.case_id[:8],
                error=type(exc).__name__,
            )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

async def build_decision_case_store(
    *,
    backend: DecisionCaseStoreBackend | None = None,
    database_url: str | None = None,
    pool_min: int | None = None,
    pool_max: int | None = None,
) -> tuple[DecisionCaseStore, CloseCallable]:
    """Assemble a :class:`DecisionCaseStore` according to configuration.

    Each argument defaults to the corresponding environment variable
    when left as ``None``.  The returned close callable must be awaited
    at application shutdown to release any pool this factory owned.

    Args:
        backend: Override for the backend selector.
        database_url: Override for the Postgres URI.
        pool_min: Override for the minimum pool size.
        pool_max: Override for the maximum pool size.

    Returns:
        A tuple ``(store, close)`` where ``store`` satisfies
        :class:`DecisionCaseStore` and ``close`` releases owned
        resources when awaited.

    Raises:
        ValueError: When a Postgres-backed mode is requested without a
            connection URI.
    """
    resolved = backend or _resolve_backend()
    url = database_url or _resolve_database_url()
    min_size, max_size = _resolve_pool_sizes(pool_min, pool_max)

    if resolved is DecisionCaseStoreBackend.MEMORY:
        return InMemoryDecisionCaseStore(), _noop_close

    if url is None:
        raise ValueError(
            "Postgres-backed decision case store requires a database URL "
            "(set DECISION_CASE_STORE_DATABASE_URL or DATABASE_URL).",
        )

    if resolved is DecisionCaseStoreBackend.POSTGRES:
        return await _build_postgres_only(url, min_size, max_size)

    return await _build_dual(url, min_size, max_size)


# ---------------------------------------------------------------------------
# Internal builders and resolvers
# ---------------------------------------------------------------------------

async def _build_postgres_only(
    url: str,
    min_size: int,
    max_size: int,
) -> tuple[DecisionCaseStore, CloseCallable]:
    """Build a pure Postgres-backed store with ownership semantics."""
    store = await PostgresDecisionCaseStore.from_url(
        url,
        min_size=min_size,
        max_size=max_size,
    )
    logger.info(
        "decision_case_store_backend",
        backend=DecisionCaseStoreBackend.POSTGRES.value,
    )
    return store, store.close


async def _build_dual(
    url: str,
    min_size: int,
    max_size: int,
) -> tuple[DecisionCaseStore, CloseCallable]:
    """Build a dual-write store: in-memory primary, Postgres shadow."""
    primary: DecisionCaseStore = InMemoryDecisionCaseStore()
    shadow = await PostgresDecisionCaseStore.from_url(
        url,
        min_size=min_size,
        max_size=max_size,
    )
    wrapper = DualWriteDecisionCaseStore(primary=primary, shadow=shadow)
    logger.info(
        "decision_case_store_backend",
        backend=DecisionCaseStoreBackend.DUAL.value,
    )
    return wrapper, shadow.close


def _resolve_backend() -> DecisionCaseStoreBackend:
    """Read the backend selector from the environment, defaulting to memory."""
    raw = os.getenv(_ENV_BACKEND, DecisionCaseStoreBackend.MEMORY.value)
    try:
        return DecisionCaseStoreBackend(raw.lower())
    except ValueError:
        logger.warning("unknown_backend_falling_back_to_memory", raw=raw)
        return DecisionCaseStoreBackend.MEMORY


def _resolve_database_url() -> str | None:
    """Resolve the Postgres URI with the documented fallback chain."""
    return os.getenv(_ENV_URL) or os.getenv(_ENV_URL_FALLBACK)


def _resolve_pool_sizes(
    pool_min: int | None,
    pool_max: int | None,
) -> tuple[int, int]:
    """Resolve pool sizes with environment-driven defaults."""
    min_size = pool_min if pool_min is not None else _read_int(_ENV_POOL_MIN, 2)
    max_size = pool_max if pool_max is not None else _read_int(_ENV_POOL_MAX, 10)
    return min_size, max_size


def _read_int(name: str, default: int) -> int:
    """Read an integer env var, falling back silently on malformed values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid_int_env_var", name=name, raw=raw)
        return default


# ---------------------------------------------------------------------------
# PatternRuleStore backend + factory
# ---------------------------------------------------------------------------

class PatternRuleStoreBackend(StrEnum):
    """Selectable backends for the pattern-rule store.

    Unlike :class:`DecisionCaseStoreBackend`, no ``dual`` mode is
    exposed: :class:`PatternRule` is mutable, so dual writes would
    create a split-brain where the primary and shadow disagree on
    ``confidence`` / ``support_count`` after successive updates.
    """

    MEMORY = "memory"
    POSTGRES = "postgres"


async def build_pattern_rule_store(
    *,
    backend: PatternRuleStoreBackend | None = None,
    database_url: str | None = None,
    pool_min: int | None = None,
    pool_max: int | None = None,
) -> tuple[PatternRuleStore, CloseCallable]:
    """Assemble a :class:`PatternRuleStore` according to configuration.

    Each argument defaults to the corresponding environment variable
    when left as ``None``.  The returned close callable must be awaited
    at application shutdown to release any pool this factory owned.

    Args:
        backend: Override for the backend selector.
        database_url: Override for the Postgres URI.
        pool_min: Override for the minimum pool size.
        pool_max: Override for the maximum pool size.

    Returns:
        A tuple ``(store, close)`` where ``store`` satisfies
        :class:`PatternRuleStore` and ``close`` releases owned
        resources when awaited.

    Raises:
        ValueError: When Postgres mode is requested without a URI.
    """
    resolved = backend or _resolve_rule_backend()
    url = database_url or _resolve_database_url()
    min_size, max_size = _resolve_pool_sizes(pool_min, pool_max)

    if resolved is PatternRuleStoreBackend.MEMORY:
        return InMemoryPatternRuleStore(), _noop_close

    if url is None:
        raise ValueError(
            "Postgres-backed pattern rule store requires a database URL "
            "(set DECISION_CASE_STORE_DATABASE_URL or DATABASE_URL).",
        )

    store = await PostgresPatternRuleStore.from_url(
        url,
        min_size=min_size,
        max_size=max_size,
    )
    logger.info(
        "pattern_rule_store_backend",
        backend=PatternRuleStoreBackend.POSTGRES.value,
    )
    return store, store.close


def _resolve_rule_backend() -> PatternRuleStoreBackend:
    """Read the rule-store backend selector from the environment."""
    raw = os.getenv(_ENV_RULE_BACKEND, PatternRuleStoreBackend.MEMORY.value)
    try:
        return PatternRuleStoreBackend(raw.lower())
    except ValueError:
        logger.warning("unknown_rule_backend_falling_back_to_memory", raw=raw)
        return PatternRuleStoreBackend.MEMORY
