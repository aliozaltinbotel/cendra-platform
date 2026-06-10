"""Persistence for the parsed foundation scenario catalog (FL-01).

The reactive foundation document
(``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md``)
defines 469 hospitality scenarios with a fixed 14-field schema.
:mod:`brain_engine.patterns.foundation_registry` turns the markdown
into immutable :class:`FoundationScenario` rows; this module owns
the Postgres-side store + an :class:`InMemoryFoundationCatalogStore`
for tests.

Design contract:

* Stores are accessed through the :class:`FoundationCatalogStore`
  :class:`~typing.Protocol`, so the consumer (Foundation Layer
  orchestrator and pattern miner gates introduced in Sprints 2-3)
  never imports a concrete store.
* The Postgres schema is ``foundation_scenarios_reactive`` defined in
  ``infra/postgres-init/026_foundation_scenarios_reactive.sql``.  The
  store does not own DDL — migrations land independently.
* Upserts are *whole-document*: pass the full tuple of parsed
  scenarios plus the SHA-256 of the markdown.  When the stored
  ``doc_hash`` already matches the current digest the store skips
  the write loop entirely so pod startup stays cheap.
* The store never raises on missing rows — :meth:`get` returns
  ``None`` instead, mirroring the loader's degrade-gracefully
  contract.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import structlog

from brain_engine.patterns.foundation_registry import FoundationScenario

if TYPE_CHECKING:
    import asyncpg


# Sprint 6 W4 — env flag toggling the FL-05 ``Should AI Learn
# Pattern: No`` gate inside ``PatternMiner``.  Default off so the
# legacy bootstrap path keeps producing the same rule set; flip
# the flag once the operator wants the safety scenarios (gas
# smell, broken glass, medical, etc.) excluded from mining.
_FOUNDATION_LEARN_GATE_ENV: Final[str] = (
    "BRAIN_FOUNDATION_LEARN_GATE_ENABLED"
)
_FALSY_LEARN_GATE_VALUES: Final[frozenset[str]] = frozenset(
    {"", "0", "false", "no", "off"},
)


def foundation_learn_gate_enabled() -> bool:
    """Whether ``compute_forbidden_foundation_ids`` produces a non-empty set.

    Read on every call so a deploy can flip
    ``BRAIN_FOUNDATION_LEARN_GATE_ENABLED`` without restarting the
    pod.  Default off — the helper short-circuits to an empty
    ``frozenset`` and the miner mines every case as before W4.
    """
    raw = os.environ.get(_FOUNDATION_LEARN_GATE_ENV, "").strip().lower()
    return raw not in _FALSY_LEARN_GATE_VALUES


async def compute_forbidden_foundation_ids(
    store: FoundationCatalogStore,
) -> frozenset[str]:
    """Return the foundation slugs that forbid pattern learning (W4).

    Walks the catalog once and collects every ``scenario_id`` whose
    ``should_learn_pattern`` is ``"No"`` (case-insensitive,
    whitespace-tolerant).  The returned :class:`frozenset` is
    suitable for passing into :class:`PatternMiner` constructor as
    ``forbidden_foundation_ids`` — the miner then filters supporting
    cases whose ``foundation_scenario_id`` lands in the set.

    Returns the empty :class:`frozenset` when
    :func:`foundation_learn_gate_enabled` returns ``False`` so an
    operator that has not opted in keeps the pre-W4 mining
    behaviour bit-for-bit.

    The helper is async so it can read from either the in-memory
    or Postgres store transparently.  Designed to be called once
    at pod boot / nightly wiring; the resulting frozenset is
    immutable and safe to share across miner instances.
    """
    if not foundation_learn_gate_enabled():
        return frozenset()
    rows = await store.list_all()
    return frozenset(
        scenario.scenario_id
        for scenario in rows
        if scenario.should_learn_pattern.strip().lower() == "no"
    )


__all__ = [
    "FoundationCatalogStore",
    "InMemoryFoundationCatalogStore",
    "PgFoundationCatalogStore",
    "UpsertResult",
    "compute_forbidden_foundation_ids",
    "foundation_learn_gate_enabled",
]


logger = structlog.get_logger(__name__)


# ── result value object ───────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """Outcome of a whole-catalog upsert.

    Attributes:
        upserted: Number of rows actually written to the table.
            Zero when the cache was already at ``doc_hash`` and the
            store skipped the write loop.
        skipped_reason: ``"hash_match"`` when the upsert was a
            no-op because the document had not changed; empty
            string when a real write happened.
    """

    upserted: int
    skipped_reason: str = ""


# ── Protocol — what consumers depend on ───────────────────── #


@runtime_checkable
class FoundationCatalogStore(Protocol):
    """Read/write façade for the parsed foundation catalog.

    Consumers (Foundation Layer orchestrator, pattern miner gates,
    LLM iterative-questioning hook) depend on this Protocol rather
    than a concrete store — :class:`InMemoryFoundationCatalogStore`
    satisfies it for tests; :class:`PgFoundationCatalogStore` is
    the production wiring.
    """

    async def upsert_many(
        self,
        scenarios: Sequence[FoundationScenario],
        *,
        doc_hash: str,
    ) -> UpsertResult:
        """Upsert every scenario, no-op if ``doc_hash`` is unchanged.

        Args:
            scenarios: Parsed foundation rows from
                :func:`~brain_engine.patterns.foundation_registry.
                load_foundation_scenarios`.
            doc_hash: SHA-256 hex digest of the markdown file these
                rows were parsed from.  When this digest matches
                the digest already stored, the call is a no-op.

        Returns:
            An :class:`UpsertResult` describing what happened.
        """
        ...

    async def get(self, scenario_id: str) -> FoundationScenario | None:
        """Return one scenario by id, or ``None`` if it does not exist."""
        ...

    async def list_all(self) -> tuple[FoundationScenario, ...]:
        """Return every stored scenario, ordered by stage and id.

        Ordering is deterministic so callers that compare snapshots
        (tests, audit diffs) do not need to sort post-fetch.
        """
        ...

    async def get_doc_hash(self) -> str | None:
        """Return the ``doc_hash`` last written by :meth:`upsert_many`.

        ``None`` when the table is empty.  Used by the pod startup
        wiring to decide whether to re-parse the markdown.
        """
        ...


# ── helpers shared by both impls ──────────────────────────── #


def _coerce_string_tuple(raw: object) -> tuple[str, ...]:
    """Cast a JSON-decoded value into a tuple of strings.

    JSONB columns deserialise to ``object`` from mypy's vantage so
    the rebuild helper cannot pass the value directly to
    ``tuple(...)``.  This wrapper performs the narrowing: an
    iterable of stringifiable items becomes a tuple of strings;
    anything else (a stray scalar from a hand-edited row, ``None``)
    collapses to ``()``.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        # A bare string would be iterable character-by-character —
        # not what callers want.  Treat it as a scalar bucket.
        return (raw,) if raw else ()
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)


def _scenario_to_payload(
    scenario: FoundationScenario,
) -> dict[str, object]:
    """Serialise the 14 sub-section fields into a JSON-safe dict.

    The header attributes (``scenario_id``, ``title``,
    ``stage_number``, ``stage_label``) live in their own columns, so
    they are NOT duplicated into the payload.  ``trigger`` is
    payload-side because consumers expect to find every sub-section
    field under ``payload`` for symmetry; the matcher uses
    :func:`~brain_engine.patterns.foundation_registry.
    load_foundation_examples` and never reads the payload anyway.
    """
    return {
        "trigger": scenario.trigger,
        "risk_level": scenario.risk_level,
        "ai_default_behavior": scenario.ai_default_behavior,
        "required_data_checks": list(scenario.required_data_checks),
        "signals_to_inspect": list(scenario.signals_to_inspect),
        "should_auto_reply": scenario.should_auto_reply,
        "should_escalate_to_pm": scenario.should_escalate_to_pm,
        "should_create_task": scenario.should_create_task,
        "should_learn_pattern": scenario.should_learn_pattern,
        "pattern_to_learn": scenario.pattern_to_learn,
        "example_learned_pattern": scenario.example_learned_pattern,
        "memory_types": list(scenario.memory_types),
        "what_not_to_learn": scenario.what_not_to_learn,
        "future_behavior_impact": scenario.future_behavior_impact,
    }


def _payload_to_scenario(
    *,
    scenario_id: str,
    stage_number: int,
    stage_label: str,
    title: str,
    payload: dict[str, object],
) -> FoundationScenario:
    """Rebuild a :class:`FoundationScenario` from a JSONB payload.

    The payload is trusted (the store writes it itself) but each
    value is coerced through the expected Python type so a manual
    edit in Postgres cannot poison the matcher with the wrong shape
    (e.g. a string where a list of bullets is expected).
    """
    return FoundationScenario(
        scenario_id=scenario_id,
        title=title,
        stage_number=stage_number,
        stage_label=stage_label,
        trigger=str(payload.get("trigger", "")),
        risk_level=str(payload.get("risk_level", "")),
        ai_default_behavior=str(
            payload.get("ai_default_behavior", ""),
        ),
        required_data_checks=_coerce_string_tuple(
            payload.get("required_data_checks"),
        ),
        signals_to_inspect=_coerce_string_tuple(
            payload.get("signals_to_inspect"),
        ),
        should_auto_reply=str(payload.get("should_auto_reply", "")),
        should_escalate_to_pm=str(
            payload.get("should_escalate_to_pm", ""),
        ),
        should_create_task=str(
            payload.get("should_create_task", ""),
        ),
        should_learn_pattern=str(
            payload.get("should_learn_pattern", ""),
        ),
        pattern_to_learn=str(payload.get("pattern_to_learn", "")),
        example_learned_pattern=str(
            payload.get("example_learned_pattern", ""),
        ),
        memory_types=_coerce_string_tuple(
            payload.get("memory_types"),
        ),
        what_not_to_learn=str(payload.get("what_not_to_learn", "")),
        future_behavior_impact=str(
            payload.get("future_behavior_impact", ""),
        ),
    )


# ── in-memory store ───────────────────────────────────────── #


class InMemoryFoundationCatalogStore:
    """Process-local store, used by unit tests and dev environments.

    Maintains insertion order via Python 3.7+ dict guarantees, then
    sorts deterministically on :meth:`list_all` so test snapshots
    compare cleanly regardless of upsert order.
    """

    __slots__ = ("_doc_hash", "_rows")

    def __init__(self) -> None:
        self._rows: dict[str, FoundationScenario] = {}
        self._doc_hash: str | None = None

    async def upsert_many(
        self,
        scenarios: Sequence[FoundationScenario],
        *,
        doc_hash: str,
    ) -> UpsertResult:
        if self._doc_hash == doc_hash and self._rows:
            return UpsertResult(upserted=0, skipped_reason="hash_match")
        for scenario in scenarios:
            self._rows[scenario.scenario_id] = scenario
        self._doc_hash = doc_hash
        return UpsertResult(upserted=len(scenarios))

    async def get(self, scenario_id: str) -> FoundationScenario | None:
        return self._rows.get(scenario_id)

    async def list_all(self) -> tuple[FoundationScenario, ...]:
        ordered = sorted(
            self._rows.values(),
            key=lambda s: (s.stage_number, s.scenario_id),
        )
        return tuple(ordered)

    async def get_doc_hash(self) -> str | None:
        return self._doc_hash


# ── Postgres store ────────────────────────────────────────── #


_UPSERT_SQL: Final[str] = """
INSERT INTO foundation_scenarios_reactive (
    scenario_id,
    stage_number,
    stage_label,
    title,
    risk_level,
    payload,
    doc_hash,
    parsed_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, now())
ON CONFLICT (scenario_id) DO UPDATE SET
    stage_number = EXCLUDED.stage_number,
    stage_label = EXCLUDED.stage_label,
    title = EXCLUDED.title,
    risk_level = EXCLUDED.risk_level,
    payload = EXCLUDED.payload,
    doc_hash = EXCLUDED.doc_hash,
    parsed_at = now()
"""

_SELECT_BY_ID_SQL: Final[str] = (
    "SELECT scenario_id, stage_number, stage_label, title, payload "
    "FROM foundation_scenarios_reactive WHERE scenario_id = $1"
)

_SELECT_ALL_SQL: Final[str] = (
    "SELECT scenario_id, stage_number, stage_label, title, payload "
    "FROM foundation_scenarios_reactive "
    "ORDER BY stage_number ASC, scenario_id ASC"
)

_SELECT_DOC_HASH_SQL: Final[str] = (
    "SELECT doc_hash FROM foundation_scenarios_reactive "
    "ORDER BY parsed_at DESC LIMIT 1"
)


class PgFoundationCatalogStore:
    """Production implementation backed by ``asyncpg``.

    The pool must have a JSONB codec registered (see
    :func:`brain_engine.patterns.postgres_store._register_jsonb_codec`
    for the canonical hook).  The store re-uses the same pool the
    rest of the patterns subsystem owns to avoid a parallel
    connection pool just for foundation data.
    """

    __slots__ = ("_pool",)

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_many(
        self,
        scenarios: Sequence[FoundationScenario],
        *,
        doc_hash: str,
    ) -> UpsertResult:
        current = await self.get_doc_hash()
        if current == doc_hash and scenarios:
            logger.info(
                "foundation_catalog_store.skip_unchanged",
                doc_hash=doc_hash,
                rows=len(scenarios),
            )
            return UpsertResult(upserted=0, skipped_reason="hash_match")
        # Pre-serialise the payloads once outside the connection
        # block so the connection is held for the minimal time.
        rows = [
            (
                scenario.scenario_id,
                scenario.stage_number,
                scenario.stage_label,
                scenario.title,
                scenario.risk_level,
                json.dumps(_scenario_to_payload(scenario)),
                doc_hash,
            )
            for scenario in scenarios
        ]
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.executemany(_UPSERT_SQL, rows)
        logger.info(
            "foundation_catalog_store.upsert_complete",
            rows=len(rows),
            doc_hash=doc_hash,
        )
        return UpsertResult(upserted=len(rows))

    async def get(self, scenario_id: str) -> FoundationScenario | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_BY_ID_SQL, scenario_id)
        if row is None:
            return None
        return _payload_to_scenario(
            scenario_id=row["scenario_id"],
            stage_number=int(row["stage_number"]),
            stage_label=row["stage_label"],
            title=row["title"],
            payload=self._coerce_payload(row["payload"]),
        )

    async def list_all(self) -> tuple[FoundationScenario, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_ALL_SQL)
        return tuple(
            _payload_to_scenario(
                scenario_id=row["scenario_id"],
                stage_number=int(row["stage_number"]),
                stage_label=row["stage_label"],
                title=row["title"],
                payload=self._coerce_payload(row["payload"]),
            )
            for row in rows
        )

    async def get_doc_hash(self) -> str | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(_SELECT_DOC_HASH_SQL)
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _coerce_payload(raw: object) -> dict[str, object]:
        """Normalise the JSONB payload into a plain ``dict``.

        ``asyncpg`` returns the JSONB column as either a string (when
        no codec is registered) or a Python object (when the codec
        decodes via ``json.loads``).  Both branches converge into a
        regular ``dict`` so :func:`_payload_to_scenario` does not need
        to care which path produced the value.
        """
        if isinstance(raw, str):
            decoded = json.loads(raw)
        else:
            decoded = raw
        if not isinstance(decoded, dict):
            raise TypeError(
                "foundation payload must decode to a JSON object, "
                f"got {type(decoded).__name__}",
            )
        return decoded
