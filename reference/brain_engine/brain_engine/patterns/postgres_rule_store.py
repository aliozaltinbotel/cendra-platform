"""Postgres-backed persistence for PatternRules.

Production implementation of the :class:`PatternRuleStore` Protocol
defined in :mod:`brain_engine.patterns.store`.  Unlike
:class:`DecisionCase`, a :class:`PatternRule` is *mutable* — its
``support_count``, ``counterexample_count``, ``confidence``,
``last_seen_at`` and ``active`` flags evolve as the extractor observes
new evidence.  The store therefore UPSERTs on ``pattern_id`` (a stable
extractor-assigned identifier) and mutates every non-key column on
conflict.

Schema contract — table ``pattern_rules`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``001_init.sql``).
The surrogate ``id`` UUID and ``superseded_by`` reference column are
*not* written by this store; extractor logic that chains supersessions
lives higher up the stack.

The store shares an asyncpg pool with
:class:`~brain_engine.patterns.postgres_store.PostgresDecisionCaseStore`
whenever convenient, but owns the pool when constructed via
:meth:`from_url`.  All queries are parameterised and the JSONB codec
registered by :func:`create_patterns_pool` makes ``dict`` values in
``conditions`` / ``action`` round-trip natively.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.patterns.models import (
    DecisionAction,
    DecisionType,
    ExecutionMode,
    PatternOrigin,
    PatternRule,
    PatternScope,
    RiskLevel,
    Scenario,
)
from brain_engine.patterns.postgres_store import create_patterns_pool

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_SELECT_COLUMNS: Final[str] = (
    "pattern_id, scenario, scope, scope_id, conditions, action, "
    "blocker_types, support_count, counterexample_count, confidence, "
    "risk_level, execution_mode, valid_from, valid_to, "
    "invalid_at, deactivated_at, last_seen_at, "
    "source_case_ids, created_at, active, foundation_scenario_id, "
    "origin"
)


_UPSERT_RULE_SQL: Final[str] = """
INSERT INTO pattern_rules (
    pattern_id,
    scenario,
    scope,
    scope_id,
    conditions,
    action,
    blocker_types,
    support_count,
    counterexample_count,
    confidence,
    risk_level,
    execution_mode,
    valid_from,
    valid_to,
    invalid_at,
    deactivated_at,
    last_seen_at,
    source_case_ids,
    created_at,
    active,
    foundation_scenario_id,
    origin,
    updated_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
    $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, now()
)
ON CONFLICT (pattern_id) DO UPDATE SET
    scenario = EXCLUDED.scenario,
    scope = EXCLUDED.scope,
    scope_id = EXCLUDED.scope_id,
    conditions = EXCLUDED.conditions,
    action = EXCLUDED.action,
    blocker_types = EXCLUDED.blocker_types,
    support_count = EXCLUDED.support_count,
    counterexample_count = EXCLUDED.counterexample_count,
    confidence = EXCLUDED.confidence,
    risk_level = EXCLUDED.risk_level,
    execution_mode = EXCLUDED.execution_mode,
    valid_from = EXCLUDED.valid_from,
    valid_to = EXCLUDED.valid_to,
    invalid_at = EXCLUDED.invalid_at,
    deactivated_at = EXCLUDED.deactivated_at,
    last_seen_at = EXCLUDED.last_seen_at,
    source_case_ids = EXCLUDED.source_case_ids,
    active = EXCLUDED.active,
    foundation_scenario_id = EXCLUDED.foundation_scenario_id,
    origin = EXCLUDED.origin,
    updated_at = now()
"""


_SELECT_BY_ID_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM pattern_rules WHERE pattern_id = $1"
)


_DEACTIVATE_SQL: Final[str] = (
    "UPDATE pattern_rules "
    "SET active = false, "
    "    deactivated_at = COALESCE(deactivated_at, now()), "
    "    updated_at = now() "
    "WHERE pattern_id = $1 AND active = true "
    "RETURNING pattern_id"
)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _encode_action(action: DecisionAction) -> dict[str, Any]:
    """Serialise a :class:`DecisionAction` into a JSON-safe ``dict``."""
    return {
        "action_type": action.action_type.value,
        "params": action.params,
    }


def _decode_action(raw: dict[str, Any] | None) -> DecisionAction:
    """Rebuild a :class:`DecisionAction` from a JSONB payload."""
    payload = raw or {}
    action_type = DecisionType(
        payload.get("action_type", DecisionType.INFORM.value)
    )
    params = payload.get("params") or {}
    return DecisionAction(action_type=action_type, params=params)


def _to_float(raw: Any) -> float:
    """Coerce a Postgres ``NUMERIC`` (``Decimal``) value into ``float``.

    ``asyncpg`` decodes ``NUMERIC`` columns as :class:`decimal.Decimal` by
    default.  :class:`PatternRule.confidence` is typed as ``float`` so we
    normalise here rather than leaking Decimals into the domain model.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, Decimal):
        return float(raw)
    return float(raw)


def _row_to_rule(row: dict[str, Any]) -> PatternRule:
    """Convert a raw Postgres row into a :class:`PatternRule`.

    ``rationale`` is reconstructed from ``action.params["_rationale"]``
    so the field round-trips through Postgres without requiring a
    schema migration.  Older rows produced before the rationale
    feature land hit the empty-string default — the only effect is
    a missing one-line explanation, never a broken rule.
    """
    action = _decode_action(row.get("action"))
    rationale = ""
    if isinstance(action.params, dict):
        rationale = str(action.params.get("_rationale", "") or "")
    return PatternRule(
        pattern_id=row["pattern_id"],
        scenario=Scenario(row["scenario"]),
        scope=PatternScope(row["scope"]),
        scope_id=row["scope_id"],
        conditions=dict(row.get("conditions") or {}),
        action=action,
        blocker_types=tuple(row.get("blocker_types") or ()),
        support_count=int(row.get("support_count") or 0),
        counterexample_count=int(row.get("counterexample_count") or 0),
        confidence=_to_float(row.get("confidence")),
        risk_level=RiskLevel(row["risk_level"]),
        execution_mode=ExecutionMode(row["execution_mode"]),
        valid_from=row["valid_from"],
        valid_to=row.get("valid_to"),
        invalid_at=row.get("invalid_at"),
        deactivated_at=row.get("deactivated_at"),
        last_seen_at=row["last_seen_at"],
        source_case_ids=tuple(row.get("source_case_ids") or ()),
        created_at=row["created_at"],
        active=bool(row.get("active", True)),
        rationale=rationale,
        foundation_scenario_id=row.get("foundation_scenario_id"),
        origin=PatternOrigin.from_jsonable(row.get("origin")),
    )


def _rule_to_params(rule: PatternRule) -> tuple[Any, ...]:
    """Flatten a :class:`PatternRule` into positional UPSERT parameters.

    Order and count match :data:`_UPSERT_RULE_SQL`.
    """
    return (
        rule.pattern_id,
        rule.scenario.value,
        rule.scope.value,
        rule.scope_id,
        dict(rule.conditions),
        _encode_action(rule.action),
        list(rule.blocker_types),
        rule.support_count,
        rule.counterexample_count,
        rule.confidence,
        rule.risk_level.value,
        rule.execution_mode.value,
        rule.valid_from,
        rule.valid_to,
        rule.invalid_at,
        rule.deactivated_at,
        rule.last_seen_at,
        list(rule.source_case_ids),
        rule.created_at,
        rule.active,
        rule.foundation_scenario_id,
        rule.origin.to_jsonable(),
    )


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------


def _build_active_rules_query(
    *,
    scenario: Scenario | None,
    scope: PatternScope | None,
    scope_id: str | None,
) -> tuple[str, list[Any]]:
    """Compose a parameterised SELECT for active, non-expired rules.

    The Postgres equivalent of :attr:`PatternRule.is_expired` is
    ``valid_to IS NOT NULL AND valid_to <= now()`` — the inverse guard
    is applied here so ``valid_to IS NULL`` (indefinite) rules remain
    visible alongside unexpired, dated ones.

    Args:
        scenario: Scenario filter (``None`` = any).
        scope: Scope-level filter.
        scope_id: Scope-identifier filter.

    Returns:
        A ``(sql, args)`` tuple suitable for ``pool.fetch``.
    """
    clauses: list[str] = [
        "active = true",
        "(valid_to IS NULL OR valid_to > now())",
    ]
    args: list[Any] = []

    if scenario is not None:
        args.append(scenario.value)
        clauses.append(f"scenario = ${len(args)}")
    if scope is not None:
        args.append(scope.value)
        clauses.append(f"scope = ${len(args)}")
    if scope_id is not None:
        args.append(scope_id)
        clauses.append(f"scope_id = ${len(args)}")

    where = " AND ".join(clauses)
    sql = (
        f"SELECT {_SELECT_COLUMNS} FROM pattern_rules "
        f"WHERE {where} ORDER BY confidence DESC"
    )
    return sql, args


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PostgresPatternRuleStore:
    """Postgres-backed :class:`PatternRuleStore` implementation.

    Satisfies the Protocol defined in
    :mod:`brain_engine.patterns.store` without inheritance — structural
    typing keeps the production store decoupled from the in-memory
    reference implementation.

    The store does not own the connection pool's lifecycle by default.
    When constructed via :meth:`from_url`, the caller *does* own the
    pool and must call :meth:`close`.

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
        self._log = structlog.get_logger(__name__).bind(
            component="pg_rule_store",
        )

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PostgresPatternRuleStore:
        """Build a store that owns a freshly-created pool.

        Args:
            database_url: Postgres connection URI.
            min_size: Minimum pool size.
            max_size: Maximum pool size.

        Returns:
            A fully-initialised store ready to serve requests.
        """
        pool = await create_patterns_pool(
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

    async def store(self, rule: PatternRule) -> str:
        """Persist or refresh a :class:`PatternRule` (UPSERT on ``pattern_id``).

        Because :class:`PatternRule` is mutable, re-storing the same
        ``pattern_id`` overwrites every non-key column with the incoming
        values (including ``active`` and ``confidence``).

        Args:
            rule: The rule to persist.

        Returns:
            The ``pattern_id`` of the stored rule.
        """
        params = _rule_to_params(rule)
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_RULE_SQL, *params)
        self._log.debug(
            "rule_stored",
            pattern_id=rule.pattern_id[:8],
            scenario=rule.scenario.value,
            confidence=round(rule.confidence, 2),
            active=rule.active,
        )
        return rule.pattern_id

    async def get(self, pattern_id: str) -> PatternRule | None:
        """Retrieve a rule by ``pattern_id``.

        Args:
            pattern_id: Unique rule identifier.

        Returns:
            The hydrated :class:`PatternRule` or ``None`` when missing.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_BY_ID_SQL, pattern_id)
        if row is None:
            return None
        return _row_to_rule(dict(row))

    async def get_active_rules(
        self,
        *,
        scenario: Scenario | None = None,
        scope: PatternScope | None = None,
        scope_id: str | None = None,
    ) -> list[PatternRule]:
        """Return active, non-expired rules matching the given scope.

        Results are ordered by ``confidence`` descending so that the
        most trustworthy rule for a given scenario / scope surfaces
        first at the call site.

        Args:
            scenario: Scenario filter.
            scope: Scope-level filter.
            scope_id: Scope-identifier filter.

        Returns:
            List of matching :class:`PatternRule` instances.
        """
        sql, args = _build_active_rules_query(
            scenario=scenario,
            scope=scope,
            scope_id=scope_id,
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row_to_rule(dict(row)) for row in rows]

    async def deactivate(self, pattern_id: str) -> bool:
        """Mark a rule as inactive.

        Uses ``UPDATE ... RETURNING`` so the boolean result reflects
        whether a row actually transitioned to inactive: re-deactivating
        an already-inactive rule returns ``False``.

        Args:
            pattern_id: Rule identifier.

        Returns:
            ``True`` when a previously active rule was deactivated.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_DEACTIVATE_SQL, pattern_id)
        changed = row is not None
        if changed:
            self._log.info("rule_deactivated", pattern_id=pattern_id[:8])
        return changed

    async def update(self, rule: PatternRule) -> None:
        """Update a rule after confidence / support refresh.

        Delegates to :meth:`store` since the underlying statement is an
        UPSERT — splitting ``update`` and ``store`` would only duplicate
        SQL without changing semantics.

        Args:
            rule: Updated :class:`PatternRule` instance.
        """
        await self.store(rule)
