"""Postgres-backed persistence for the A/B experiment registry.

Production implementation of the
:class:`brain_engine.experiments.store.ExperimentStore` Protocol.
Mirrors the conventions of
:mod:`brain_engine.blockers.postgres_store` so the two production
stores stay shaped the same way: a JSONB codec is registered on
every connection, all SQL is parameterised, and the store
optionally owns the ``asyncpg`` pool's lifecycle.

Schema contract — tables ``ab_experiments`` and ``ab_outcomes``
defined in ``deploy/postgres-migrations.yaml`` (migration
``020_ab_experiments.sql``).

Read path: :meth:`load_aggregates` runs a single ``GROUP BY``
on the outcomes ledger so the verdict pipeline always sees a
consistent snapshot.  We deliberately avoid maintaining a cached
counter column because the ledger doubles as audit evidence —
one INSERT per recorded outcome, never an UPDATE.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.experiments.ab_test_engine import (
    DEFAULT_MIN_TRIALS_PER_ARM,
    Experiment,
    Variant,
)
from brain_engine.experiments.statistical_significance import (
    DEFAULT_ALPHA,
)
from brain_engine.experiments.store import VariantTally

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------- #
# SQL statements
# ----------------------------------------------------------------- #


_UPSERT_EXPERIMENT_SQL: Final[str] = """
INSERT INTO ab_experiments (
    experiment_id,
    name,
    hypothesis,
    variants,
    salt,
    alpha,
    min_trials_per_arm,
    control_id,
    status,
    created_at,
    ended_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9,
    COALESCE($10, now()), $11
)
ON CONFLICT (experiment_id) DO UPDATE SET
    name               = EXCLUDED.name,
    hypothesis         = EXCLUDED.hypothesis,
    variants           = EXCLUDED.variants,
    salt               = EXCLUDED.salt,
    alpha              = EXCLUDED.alpha,
    min_trials_per_arm = EXCLUDED.min_trials_per_arm,
    control_id         = EXCLUDED.control_id,
    status             = EXCLUDED.status,
    ended_at           = EXCLUDED.ended_at
"""

_SELECT_EXPERIMENT_COLUMNS: Final[str] = (
    "experiment_id, name, hypothesis, variants, salt, alpha, "
    "min_trials_per_arm, control_id, status, created_at, ended_at"
)

_SELECT_ALL_EXPERIMENTS_SQL: Final[str] = (
    f"SELECT {_SELECT_EXPERIMENT_COLUMNS} "  # noqa: S608
    "FROM ab_experiments ORDER BY created_at ASC"
)

_INSERT_OUTCOME_SQL: Final[str] = """
INSERT INTO ab_outcomes (
    experiment_id,
    variant_id,
    success,
    metadata
)
VALUES ($1, $2, $3, $4)
"""

_AGGREGATE_OUTCOMES_SQL: Final[str] = """
SELECT
    variant_id,
    COUNT(*) AS trials,
    COUNT(*) FILTER (WHERE success) AS successes
FROM ab_outcomes
WHERE experiment_id = $1
GROUP BY variant_id
"""


# ----------------------------------------------------------------- #
# Pool helpers
# ----------------------------------------------------------------- #


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register a JSON codec for ``JSONB`` columns."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_experiments_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create an ``asyncpg`` pool wired with the JSONB codec.

    Args:
        database_url: Postgres URI (``postgresql://…``).
        min_size: Minimum pool size.
        max_size: Maximum pool size.

    Returns:
        A live asyncpg connection pool.

    Raises:
        ImportError: When ``asyncpg`` is not installed.
    """
    import asyncpg  # local import — optional dependency

    return await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        init=_register_jsonb_codec,
    )


# ----------------------------------------------------------------- #
# Serialisation helpers
# ----------------------------------------------------------------- #


def _experiment_to_params(
    experiment: Experiment,
    *,
    name: str = "",
    hypothesis: str = "",
    status: str = "running",
    created_at: Any = None,
    ended_at: Any = None,
) -> tuple[Any, ...]:
    """Flatten an :class:`Experiment` into upsert parameters.

    Order and count mirror :data:`_UPSERT_EXPERIMENT_SQL`.
    """
    variants_payload = [
        {
            "variant_id": v.variant_id,
            "weight": v.weight,
            "is_control": v.is_control,
        }
        for v in experiment.variants
    ]
    return (
        experiment.experiment_id,
        name,
        hypothesis,
        variants_payload,
        experiment.salt,
        experiment.alpha,
        experiment.min_trials_per_arm,
        experiment.control_id,
        status,
        created_at,
        ended_at,
    )


def _row_to_experiment(row: Mapping[str, Any]) -> Experiment:
    """Hydrate an :class:`Experiment` from a raw Postgres row.

    The runtime schema only carries the math-relevant fields
    (``experiment_id``, ``variants``, ``salt``, ``alpha``,
    ``min_trials_per_arm``); descriptive fields like ``name``
    or ``hypothesis`` stay in the table for analyst tooling but
    are not part of the in-process :class:`Experiment` value.
    """
    raw_variants = row.get("variants") or ()
    variants = tuple(
        Variant(
            variant_id=str(v["variant_id"]),
            weight=float(v.get("weight", 0.0)),
            is_control=bool(v.get("is_control", False)),
        )
        for v in raw_variants
    )
    salt = row.get("salt") or ""
    alpha_raw = row.get("alpha")
    alpha = float(alpha_raw) if alpha_raw is not None else DEFAULT_ALPHA
    min_trials_raw = row.get("min_trials_per_arm")
    min_trials = (
        int(min_trials_raw)
        if min_trials_raw is not None
        else DEFAULT_MIN_TRIALS_PER_ARM
    )
    return Experiment(
        experiment_id=str(row["experiment_id"]),
        variants=variants,
        salt=salt,
        alpha=alpha,
        min_trials_per_arm=min_trials,
    )


# ----------------------------------------------------------------- #
# Store implementation
# ----------------------------------------------------------------- #


class PgExperimentStore:
    """Postgres-backed :class:`ExperimentStore` implementation.

    Satisfies the structural Protocol declared in
    :mod:`brain_engine.experiments.store` without inheritance.
    The store does not own the pool by default; constructing via
    :meth:`from_url` flips ``owns_pool`` so :meth:`close`
    releases it.

    Attributes:
        _pool: Injected asyncpg pool.
        _log: Structured logger bound to this component.
        _owns_pool: Whether :meth:`close` should close the pool.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        owns_pool: bool = False,
    ) -> None:
        self._pool = pool
        self._owns_pool = owns_pool
        self._log = logger.bind(component="pg_experiment_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PgExperimentStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_experiments_pool(
            database_url,
            min_size=min_size,
            max_size=max_size,
        )
        return cls(pool, owns_pool=True)

    async def close(self) -> None:
        """Close the underlying pool if this store owns it."""
        if self._owns_pool:
            await self._pool.close()
            self._log.info("pool_closed")

    # ── ExperimentStore Protocol ─────────────────────────── #

    async def save_experiment(
        self,
        experiment: Experiment,
        *,
        name: str = "",
        hypothesis: str = "",
        status: str = "running",
    ) -> None:
        """Persist (or upsert) an experiment registration."""
        params = _experiment_to_params(
            experiment,
            name=name,
            hypothesis=hypothesis,
            status=status,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_EXPERIMENT_SQL, *params)
        self._log.debug(
            "experiment_saved",
            experiment_id=experiment.experiment_id,
            variant_count=len(experiment.variants),
            status=status,
        )

    async def load_experiments(self) -> list[Experiment]:
        """Return every persisted experiment, oldest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_ALL_EXPERIMENTS_SQL)
        return [_row_to_experiment(dict(r)) for r in rows]

    async def record_outcome(
        self,
        experiment_id: str,
        variant_id: str,
        *,
        success: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one outcome row to the durable ledger."""
        payload = dict(metadata) if metadata else {}
        async with self._pool.acquire() as conn:
            await conn.execute(
                _INSERT_OUTCOME_SQL,
                experiment_id,
                variant_id,
                success,
                payload,
            )

    async def load_aggregates(
        self,
        experiment_id: str,
    ) -> Mapping[str, VariantTally]:
        """Return ``{variant_id: (trials, successes)}`` aggregates."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _AGGREGATE_OUTCOMES_SQL,
                experiment_id,
            )
        return {
            str(r["variant_id"]): (
                int(r["trials"]),
                int(r["successes"]),
            )
            for r in rows
        }
