"""Postgres-backed persistence for DecisionCases.

Production implementation of the :class:`DecisionCaseStore` Protocol
defined in :mod:`brain_engine.patterns.store`.  Uses ``asyncpg`` with a
JSONB codec registered at pool initialisation so that Python ``dict``
and ``list`` values round-trip into Postgres ``JSONB`` columns
natively.

Schema contract — table ``decision_cases`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``001_init.sql``).  The
``id`` (surrogate UUID), ``search_doc`` (GENERATED tsvector),
``message_embedding`` and ``context_embedding`` (pgvector) columns are
*not* written by this store: embeddings are the responsibility of a
separate ingestion pipeline, and ``search_doc`` is computed by
Postgres.

The store is stateless apart from the injected connection pool; all
queries are parameterised (no string interpolation of user data) and
use ``ON CONFLICT (case_id) DO NOTHING`` to remain idempotent against
the immutable :class:`DecisionCase` value object.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    ResolutionType,
    Scenario,
)

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_INSERT_CASE_SQL: Final[str] = """
INSERT INTO decision_cases (
    case_id,
    stage,
    scenario,
    decision_type,
    property_id,
    owner_id,
    reservation_id,
    guest_id,
    message_text,
    message_language,
    response_text,
    extracted_entities,
    pms_snapshot,
    calendar_snapshot,
    ops_snapshot,
    guest_snapshot,
    decision,
    executed_actions,
    outcome,
    evidence_source_ids,
    created_at,
    source,
    orchestrator_verdict,
    foundation_scenario_id,
    origin
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
    $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
    $21, $22, $23, $24, $25
)
ON CONFLICT (case_id) DO NOTHING
"""

_SELECT_COLUMNS: Final[str] = (
    "case_id, stage, scenario, property_id, owner_id, "
    "reservation_id, guest_id, message_text, message_language, "
    "response_text, extracted_entities, pms_snapshot, "
    "calendar_snapshot, ops_snapshot, guest_snapshot, "
    "decision, executed_actions, outcome, evidence_source_ids, "
    "created_at, source, orchestrator_verdict, archived_at, "
    "foundation_scenario_id, origin"
)

_SELECT_BY_ID_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM decision_cases WHERE case_id = $1"
)

_SELECT_BY_RESERVATION_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM decision_cases "
    "WHERE reservation_id = $1 ORDER BY created_at ASC"
)

# Sprint-4 soft-archive helpers — atomic UPDATE that flips
# ``archived_at`` only when it is currently NULL (idempotent
# re-runs do not bump the timestamp on already-archived rows).
_ARCHIVE_CASE_SQL: Final[str] = (
    "UPDATE decision_cases "
    "SET archived_at = now(), updated_at = now() "
    "WHERE case_id = $1 AND archived_at IS NULL "
    "RETURNING case_id"
)

# Selection query that finds candidates for archival: cases
# older than the cutoff date AND NOT referenced by any active
# PatternRule.source_case_ids.  Returns case_ids only — the
# caller decides whether to flip ``archived_at`` per row so the
# transaction stays small.
_SELECT_ARCHIVE_CANDIDATES_SQL: Final[str] = (
    "SELECT dc.case_id FROM decision_cases dc "
    "WHERE dc.archived_at IS NULL "
    "AND dc.created_at < $1 "
    "AND NOT EXISTS ("
    "    SELECT 1 FROM pattern_rules pr "
    "    WHERE pr.active = true "
    "    AND dc.case_id = ANY (pr.source_case_ids)"
    ") "
    "ORDER BY dc.created_at ASC LIMIT $2"
)


# ---------------------------------------------------------------------------
# Pool initialisation helper
# ---------------------------------------------------------------------------


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Teach asyncpg how to round-trip ``JSONB`` columns as Python objects.

    Registered once per connection via the pool ``init`` hook so that
    every acquired connection has the codec configured.

    Args:
        conn: A freshly-opened asyncpg connection.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_patterns_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create an asyncpg pool configured for the patterns store.

    The pool registers a JSONB codec on every connection so that
    :class:`dict` and :class:`list` values are serialised/deserialised
    automatically.

    Args:
        database_url: Postgres connection URI (``postgresql://…``).
        min_size: Minimum pool size.
        max_size: Maximum pool size.

    Returns:
        A live asyncpg connection pool.

    Raises:
        ImportError: If ``asyncpg`` is not installed in the environment.
    """
    import asyncpg  # local import — optional dependency

    return await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        init=_register_jsonb_codec,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _encode_decision(action: DecisionAction) -> dict[str, Any]:
    """Serialise a :class:`DecisionAction` into a JSON-safe ``dict``."""
    return {
        "action_type": action.action_type.value,
        "params": action.params,
    }


def _encode_outcome(outcome: CaseOutcome) -> dict[str, Any]:
    """Serialise a :class:`CaseOutcome` into a JSON-safe ``dict``."""
    raw = asdict(outcome)
    resolution = outcome.resolution_type
    raw["resolution_type"] = (
        resolution.value if resolution is not None else None
    )
    return raw


def _decode_decision(raw: dict[str, Any] | None) -> DecisionAction:
    """Rebuild a :class:`DecisionAction` from a JSONB payload."""
    payload = raw or {}
    action_type = DecisionType(
        payload.get("action_type", DecisionType.INFORM.value)
    )
    params = payload.get("params") or {}
    return DecisionAction(action_type=action_type, params=params)


def _decode_outcome(raw: dict[str, Any] | None) -> CaseOutcome:
    """Rebuild a :class:`CaseOutcome` from a JSONB payload."""
    payload = raw or {}
    resolution_raw = payload.get("resolution_type")
    resolution = (
        ResolutionType(resolution_raw) if resolution_raw is not None else None
    )
    return CaseOutcome(
        guest_replied=bool(payload.get("guest_replied", False)),
        human_overrode=bool(payload.get("human_overrode", False)),
        approval_required=bool(payload.get("approval_required", False)),
        approved=payload.get("approved"),
        successful=payload.get("successful"),
        resolution_type=resolution,
        revenue_impact=payload.get("revenue_impact"),
    )


def _row_to_case(row: dict[str, Any]) -> DecisionCase:
    """Convert a raw Postgres row into a :class:`DecisionCase`."""
    return DecisionCase(
        case_id=row["case_id"],
        stage=BookingStage(row["stage"]),
        scenario=Scenario(row["scenario"]),
        property_id=row["property_id"],
        owner_id=row["owner_id"],
        reservation_id=row.get("reservation_id"),
        guest_id=row.get("guest_id"),
        message_text=row.get("message_text") or "",
        message_language=row.get("message_language") or "en",
        response_text=row.get("response_text") or "",
        extracted_entities=dict(row.get("extracted_entities") or {}),
        pms_snapshot=dict(row.get("pms_snapshot") or {}),
        calendar_snapshot=dict(row.get("calendar_snapshot") or {}),
        ops_snapshot=dict(row.get("ops_snapshot") or {}),
        guest_snapshot=dict(row.get("guest_snapshot") or {}),
        decision=_decode_decision(row.get("decision")),
        executed_actions=tuple(row.get("executed_actions") or ()),
        outcome=_decode_outcome(row.get("outcome")),
        evidence_source_ids=tuple(row.get("evidence_source_ids") or ()),
        created_at=row["created_at"],
        source=CaseSource(row.get("source") or CaseSource.LIVE.value),
        orchestrator_verdict=dict(row.get("orchestrator_verdict") or {}),
        archived_at=row.get("archived_at"),
        foundation_scenario_id=row.get("foundation_scenario_id"),
        origin=PatternOrigin.from_jsonable(row.get("origin")),
    )


def _case_to_params(case: DecisionCase) -> tuple[Any, ...]:
    """Flatten a :class:`DecisionCase` into positional insert parameters.

    Order and count match :data:`_INSERT_CASE_SQL`.
    """
    return (
        case.case_id,
        case.stage.value,
        case.scenario.value,
        case.decision.action_type.value,
        case.property_id,
        case.owner_id,
        case.reservation_id,
        case.guest_id,
        case.message_text,
        case.message_language,
        case.response_text,
        case.extracted_entities,
        case.pms_snapshot,
        case.calendar_snapshot,
        case.ops_snapshot,
        case.guest_snapshot,
        _encode_decision(case.decision),
        list(case.executed_actions),
        _encode_outcome(case.outcome),
        list(case.evidence_source_ids),
        case.created_at,
        case.source.value,
        case.orchestrator_verdict,
        case.foundation_scenario_id,
        case.origin.to_jsonable(),
    )


# ---------------------------------------------------------------------------
# Search-query builder
# ---------------------------------------------------------------------------


def _origin_event_filter_payload(source_event_id: str) -> str:
    """Render the JSONB containment payload used by the GIN index.

    Mümin 2026-05-15 round-5 #4 — drill-down query "every case
    whose origin trail references this upstream event id".  The
    payload is ``{"source_event_ids": ["<id>"]}`` so the predicate
    ``origin @> $N::jsonb`` hits the ``idx_decision_cases_origin``
    GIN index defined in migration 028
    (``infra/postgres-init/028_pattern_origin.sql``) and stays
    O(log n) at scale.
    """
    return json.dumps({"source_event_ids": [source_event_id]})


def _build_search_query(
    *,
    scenario: Scenario | None,
    property_id: str | None,
    owner_id: str | None,
    stage: BookingStage | None,
    source_event_id: str | None,
    limit: int,
    offset: int = 0,
    include_archived: bool = False,
) -> tuple[str, list[Any]]:
    """Compose a parameterised SELECT with AND-combined filters.

    Sprint-4: archived rows are EXCLUDED by default — the working
    set the miner / extractor consume must not regrow when stale
    cases get re-loaded.  Audit / forensics callers can opt in via
    ``include_archived=True`` to scan the full table.

    Args:
        scenario: Scenario filter (optional).
        property_id: Property filter (optional).
        owner_id: Owner filter (optional).
        stage: Stage filter (optional).
        source_event_id: When provided, restricts the result to
            cases whose JSONB ``origin->'source_event_ids'``
            contains the id.  Uses the migration-028 GIN index for
            cheap drill-down lookups.
        limit: Maximum rows to return.
        offset: Number of leading rows (after newest-first sort) to
            skip.  Defaults to ``0`` for backward compatibility.
        include_archived: When ``True``, rows with non-NULL
            ``archived_at`` are returned alongside the active
            working set.  Defaults to ``False`` so existing
            callers see no behaviour change until they opt in.

    Returns:
        A ``(sql, args)`` tuple suitable for ``pool.fetch``.
    """
    clauses: list[str] = []
    args: list[Any] = []

    if not include_archived:
        clauses.append("archived_at IS NULL")
    if scenario is not None:
        args.append(scenario.value)
        clauses.append(f"scenario = ${len(args)}")
    if property_id is not None:
        args.append(property_id)
        clauses.append(f"property_id = ${len(args)}")
    if owner_id is not None:
        args.append(owner_id)
        clauses.append(f"owner_id = ${len(args)}")
    if stage is not None:
        args.append(stage.value)
        clauses.append(f"stage = ${len(args)}")
    if source_event_id is not None:
        args.append(_origin_event_filter_payload(source_event_id))
        clauses.append(f"origin @> ${len(args)}::jsonb")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.append(limit)
    limit_placeholder = f"${len(args)}"
    args.append(offset)
    offset_placeholder = f"${len(args)}"
    sql = (
        f"SELECT {_SELECT_COLUMNS} FROM decision_cases "
        f"{where} ORDER BY created_at DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    return sql, args


def _build_count_query(
    *,
    scenario: Scenario | None,
    property_id: str | None,
    owner_id: str | None = None,
    stage: BookingStage | None = None,
    source_event_id: str | None = None,
) -> tuple[str, list[Any]]:
    """Compose a parameterised ``COUNT(*)`` with AND-combined filters.

    Mirrors :func:`_build_search_query`'s filter set so paginated
    callers can derive an unfiltered total alongside the limited page.
    Soft-archived rows are excluded — count is over the working set.
    """
    clauses: list[str] = ["archived_at IS NULL"]
    args: list[Any] = []

    if scenario is not None:
        args.append(scenario.value)
        clauses.append(f"scenario = ${len(args)}")
    if property_id is not None:
        args.append(property_id)
        clauses.append(f"property_id = ${len(args)}")
    if owner_id is not None:
        args.append(owner_id)
        clauses.append(f"owner_id = ${len(args)}")
    if stage is not None:
        args.append(stage.value)
        clauses.append(f"stage = ${len(args)}")
    if source_event_id is not None:
        args.append(_origin_event_filter_payload(source_event_id))
        clauses.append(f"origin @> ${len(args)}::jsonb")

    where = f"WHERE {' AND '.join(clauses)}"
    sql = f"SELECT COUNT(*) AS n FROM decision_cases {where}"
    return sql, args


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PostgresDecisionCaseStore:
    """Postgres-backed :class:`DecisionCaseStore` implementation.

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
            component="pg_case_store",
        )

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PostgresDecisionCaseStore:
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

    async def store(self, case: DecisionCase) -> str:
        """Persist a :class:`DecisionCase`.

        Inserts are idempotent — re-storing the same ``case_id`` is a
        no-op because :class:`DecisionCase` is immutable.

        Args:
            case: The case to persist.

        Returns:
            The ``case_id`` of the stored (or already-present) case.
        """
        params = _case_to_params(case)
        async with self._pool.acquire() as conn:
            await conn.execute(_INSERT_CASE_SQL, *params)
        self._log.debug(
            "case_stored",
            case_id=case.case_id[:8],
            scenario=case.scenario.value,
            stage=case.stage.value,
        )
        return case.case_id

    async def get(self, case_id: str) -> DecisionCase | None:
        """Retrieve a case by ``case_id``.

        Args:
            case_id: Unique case identifier.

        Returns:
            The hydrated :class:`DecisionCase` or ``None`` when missing.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_BY_ID_SQL, case_id)
        if row is None:
            return None
        return _row_to_case(dict(row))

    async def search(
        self,
        *,
        scenario: Scenario | None = None,
        property_id: str | None = None,
        owner_id: str | None = None,
        stage: BookingStage | None = None,
        source_event_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list[DecisionCase]:
        """Search cases with AND-combined filters, newest first.

        Args:
            scenario: Scenario filter (``None`` = any).
            property_id: Property filter.
            owner_id: Owner filter.
            stage: Booking-stage filter.
            source_event_id: Mümin 2026-05-15 round-5 #4 — restricts
                the result to cases whose
                :pyattr:`DecisionCase.origin.source_event_ids` tuple
                contains the supplied id.  Uses the migration-028
                ``idx_decision_cases_origin`` GIN index, so the
                lookup stays cheap regardless of table size.
            limit: Maximum rows to return.
            offset: Number of leading rows (after newest-first sort)
                to skip.  Defaults to ``0`` for backward compatibility.
            include_archived: When ``True`` returns archived
                rows alongside active ones.  Sprint-4 default
                ``False`` keeps the miner / extractor blind to
                soft-archived cases — they never feed learning
                again, but the rows stay in place for audit.

        Returns:
            List of matching cases, ordered by ``created_at`` DESC,
            after ``offset`` is skipped and ``limit`` is applied.
        """
        sql, args = _build_search_query(
            scenario=scenario,
            property_id=property_id,
            owner_id=owner_id,
            stage=stage,
            source_event_id=source_event_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row_to_case(dict(row)) for row in rows]

    async def archive(self, case_id: str) -> bool:
        """Soft-archive a single case by setting ``archived_at = now()``.

        Idempotent — re-archiving an already-archived case returns
        ``False`` (the ``WHERE archived_at IS NULL`` clause filters
        the row out).  Pure metadata flip, no data loss.

        Args:
            case_id: Unique case identifier.

        Returns:
            ``True`` when the row transitioned from active to
            archived, ``False`` when it was already archived (or
            does not exist).
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_ARCHIVE_CASE_SQL, case_id)
        changed = row is not None
        if changed:
            self._log.info(
                "case_archived",
                case_id=case_id[:8],
            )
        return changed

    async def select_archive_candidates(
        self,
        *,
        cutoff: datetime,
        limit: int = 1000,
    ) -> list[str]:
        """Return ``case_id``s eligible for archival.

        A case is eligible when **both** conditions hold:

        * ``created_at < cutoff`` — older than the operator's
          retention horizon (typically ``utc_now() - 90 days``).
        * The ``case_id`` does NOT appear in any active
          ``PatternRule.source_case_ids`` array.  Cases that
          still feed an active rule must stay in the hot
          working set so re-mining can refresh the rule's
          confidence.

        The selection runs as a single ``WHERE NOT EXISTS``
        subquery so the database does the join — Python only
        sees the candidate ids.  Caller iterates and calls
        :meth:`archive` per id.

        Args:
            cutoff: Cases older than this instant are
                considered.  Use timezone-aware UTC datetimes.
            limit: Maximum candidates to return per call so a
                very large backlog can be drained in batches.

        Returns:
            List of ``case_id`` strings, oldest first.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _SELECT_ARCHIVE_CANDIDATES_SQL,
                cutoff,
                limit,
            )
        return [row["case_id"] for row in rows]

    async def get_by_reservation(
        self,
        reservation_id: str,
    ) -> list[DecisionCase]:
        """Return every case tied to a single reservation, oldest first.

        Args:
            reservation_id: PMS reservation identifier.

        Returns:
            List of :class:`DecisionCase`, ordered by ``created_at`` ASC.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_BY_RESERVATION_SQL, reservation_id)
        return [_row_to_case(dict(row)) for row in rows]

    async def count(
        self,
        *,
        scenario: Scenario | None = None,
        property_id: str | None = None,
        owner_id: str | None = None,
        stage: BookingStage | None = None,
        source_event_id: str | None = None,
    ) -> int:
        """Count cases matching the given filters.

        Mirrors :meth:`search`'s filter set so paginated callers can
        derive an unfiltered total alongside the limited page.
        Soft-archived rows are excluded — count is over the working
        set the miner / extractor consume.

        Args:
            scenario: Scenario filter.
            property_id: Property filter.
            owner_id: Owner filter.
            stage: Booking-stage filter.
            source_event_id: Filter by membership in
                ``case.origin.source_event_ids``.

        Returns:
            Non-negative integer count.
        """
        sql, args = _build_count_query(
            scenario=scenario,
            property_id=property_id,
            owner_id=owner_id,
            stage=stage,
            source_event_id=source_event_id,
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
        return int(row["n"]) if row is not None else 0
