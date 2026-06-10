# ruff: noqa: RUF002
# Module docstring quotes Ali's Turkish requirement verbatim;
# the Turkish letters are intentional, not typos.
"""Customer-facing foundation tier (FL-14).

Closes Ali's Turkish requirement #3 — *"customer-facing
foundation olarak eklemek ve onlarida bir şekilde referans
noktası almak gerekir mi"*: every rule a PM authors in the
``rule_creation`` UI gains a second life as a foundation
reference for the orchestrator's matcher and the FL-15 LLM
iterative-questioning prompt.

The model is two-tier:

* **Core foundation** (FL-01) — the 469 hospitality scenarios
  curated in
  ``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_
  Foundation.md`` and stored in ``foundation_scenarios_reactive``.
  Internal, never exposed to the customer.
* **Customer foundation** (this module) — customer-generated
  scenarios derived from PM-authored rules.  Scoped by
  ``customer_id`` so one customer's preferences never leak into
  another's reasoning.  Surfaces back to the customer via the UI
  ("show me the rules I have for early check-in").

A customer scenario reuses the FL-01 schema (Memory Type, Should-
AI flags, Pattern to Learn, …) so the orchestrator can blend the
two tiers with a single lookup: a query for
``"property_id=X, scenario_text=..."`` returns the strongest core
match and the strongest customer-scoped match, and downstream
gates (FL-04 routing, FL-05 safety, FL-13 drift detection)
operate on the union.

Sprint 5 FL-14 ships the **data layer only**: the dataclass, the
storage Protocol with in-memory + Postgres implementations, and a
SQL migration.  The wiring that copies a finalised rule from
``brain_engine/rule_creation/workflow.py`` into the customer
foundation, and the orchestrator change that consults both tiers,
land in FL-14b once the FL-12 / FL-16 stack has merged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    import asyncpg


__all__ = [
    "FoundationCustomerCatalogStore",
    "FoundationCustomerScenario",
    "InMemoryFoundationCustomerCatalogStore",
    "PgFoundationCustomerCatalogStore",
]


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return current UTC datetime — extracted for testability."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FoundationCustomerScenario:
    """One customer-authored scenario stored in the second-tier catalog.

    Schema mirrors :class:`brain_engine.patterns.foundation_registry.
    FoundationScenario` so the orchestrator can fold customer rows
    into the same matcher index without a translation layer.  The
    only difference is the prepended ``customer_id`` plus the
    ``source_rule_id`` provenance link back to the
    ``rule_creation`` workflow that produced the scenario.

    Attributes:
        customer_id: Customer the scenario belongs to.  Scopes the
            row so one customer's preferences never leak into
            another's reasoning.
        scenario_id: Deterministic slug — the consumer chooses the
            convention.  ``rule_creation``-derived scenarios use
            ``"c{customer_id}_{rule_id}"`` so the slug stays
            stable across re-saves of the same rule.
        title: Human-readable title surfaced in the UI ("My early
            check-in policy").
        trigger: Free-form text the matcher embeds.  Built from
            the PM-authored description.
        risk_level: ``Low | Medium | High | Critical`` — defaults
            to ``Medium`` for PM-authored rules so they never
            outrank a core ``Critical`` scenario.
        ai_default_behavior: Free-form guidance for the agent.
        required_data_checks: Bullet items the orchestrator should
            verify before the agent acts on the rule.
        signals_to_inspect: Bullet items the classifier should
            consult.
        should_auto_reply: ``Yes | No | Conditional``.
        should_escalate_to_pm: ``Yes | No | Conditional``.
        should_create_task: ``Yes | No | Conditional``.
        should_learn_pattern: ``Yes | No`` — PM-authored customer
            scenarios rarely warrant learning a global pattern;
            defaults to ``"No"`` so the orchestrator does not
            promote them into the cross-customer rule store.
        pattern_to_learn: Free-form description (usually empty
            for customer-authored entries — the PM already
            decided what they want).
        example_learned_pattern: Illustrative example for
            documentation.
        memory_types: Tuple of memory-tier labels (matches the
            core schema).  Empty by default — the FL-14b wiring
            decides which tier(s) the rule lives in based on
            ``rule_creation`` payload.
        what_not_to_learn: Free-form safety guardrails.
        future_behavior_impact: Free-form expected impact.
        source_rule_id: ``rule_creation`` workflow id (or PM rule
            id) that produced this scenario — provenance link
            used by the future ``/foundation/customer/{id}/source``
            API.
        created_at: When the scenario was first persisted.

    Raises:
        ValueError: When ``customer_id``, ``scenario_id``, or
            ``title`` is empty.
    """

    customer_id: str
    scenario_id: str
    title: str
    trigger: str = ""
    risk_level: str = "Medium"
    ai_default_behavior: str = ""
    required_data_checks: tuple[str, ...] = ()
    signals_to_inspect: tuple[str, ...] = ()
    should_auto_reply: str = ""
    should_escalate_to_pm: str = ""
    should_create_task: str = ""
    should_learn_pattern: str = "No"
    pattern_to_learn: str = ""
    example_learned_pattern: str = ""
    memory_types: tuple[str, ...] = ()
    what_not_to_learn: str = ""
    future_behavior_impact: str = ""
    source_rule_id: str = ""
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.customer_id:
            raise ValueError("customer_id required")
        if not self.scenario_id:
            raise ValueError("scenario_id required")
        if not self.title:
            raise ValueError("title required")


# ── Protocol + implementations ────────────────────────────── #


@runtime_checkable
class FoundationCustomerCatalogStore(Protocol):
    """Read/write façade for the customer-foundation catalog.

    Consumers (FL-14b orchestrator hook, future admin API) depend
    on this Protocol rather than a concrete store.  The
    :class:`InMemoryFoundationCustomerCatalogStore` satisfies it
    for tests; :class:`PgFoundationCustomerCatalogStore` is the
    production wiring.
    """

    async def upsert(
        self,
        scenario: FoundationCustomerScenario,
    ) -> None:
        """Persist a scenario, idempotent on ``(customer_id, scenario_id)``."""
        ...

    async def get(
        self,
        customer_id: str,
        scenario_id: str,
    ) -> FoundationCustomerScenario | None:
        """Return one scenario, or ``None`` if it does not exist."""
        ...

    async def list_for_customer(
        self,
        customer_id: str,
    ) -> tuple[FoundationCustomerScenario, ...]:
        """Return every scenario authored by ``customer_id``.

        Ordering is ``scenario_id`` ASC so callers comparing
        snapshots (tests, audit diffs) see deterministic output.
        """
        ...

    async def delete(
        self,
        customer_id: str,
        scenario_id: str,
    ) -> bool:
        """Remove one scenario; return ``True`` when a row was deleted."""
        ...


class InMemoryFoundationCustomerCatalogStore:
    """Process-local store for tests and dev environments."""

    __slots__ = ("_rows",)

    def __init__(self) -> None:
        self._rows: dict[
            tuple[str, str],
            FoundationCustomerScenario,
        ] = {}

    async def upsert(
        self,
        scenario: FoundationCustomerScenario,
    ) -> None:
        self._rows[(scenario.customer_id, scenario.scenario_id)] = (
            scenario
        )

    async def get(
        self,
        customer_id: str,
        scenario_id: str,
    ) -> FoundationCustomerScenario | None:
        return self._rows.get((customer_id, scenario_id))

    async def list_for_customer(
        self,
        customer_id: str,
    ) -> tuple[FoundationCustomerScenario, ...]:
        rows = [
            scenario
            for (cid, _), scenario in self._rows.items()
            if cid == customer_id
        ]
        ordered = sorted(rows, key=lambda s: s.scenario_id)
        return tuple(ordered)

    async def delete(
        self,
        customer_id: str,
        scenario_id: str,
    ) -> bool:
        return self._rows.pop((customer_id, scenario_id), None) is not None


# ── Postgres implementation ───────────────────────────────── #


_UPSERT_SQL: Final[str] = """
INSERT INTO foundation_scenarios_customer (
    customer_id,
    scenario_id,
    title,
    payload,
    source_rule_id,
    created_at
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (customer_id, scenario_id) DO UPDATE SET
    title = EXCLUDED.title,
    payload = EXCLUDED.payload,
    source_rule_id = EXCLUDED.source_rule_id,
    updated_at = now()
"""

_SELECT_COLUMNS: Final[str] = (
    "customer_id, scenario_id, title, payload, source_rule_id, created_at"
)

_SELECT_BY_KEY_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM foundation_scenarios_customer "
    "WHERE customer_id = $1 AND scenario_id = $2"
)

_SELECT_BY_CUSTOMER_SQL: Final[str] = (
    f"SELECT {_SELECT_COLUMNS} FROM foundation_scenarios_customer "
    "WHERE customer_id = $1 ORDER BY scenario_id ASC"
)

_DELETE_SQL: Final[str] = (
    "DELETE FROM foundation_scenarios_customer "
    "WHERE customer_id = $1 AND scenario_id = $2 "
    "RETURNING scenario_id"
)


class PgFoundationCustomerCatalogStore:
    """Production implementation backed by ``asyncpg``."""

    __slots__ = ("_pool",)

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert(
        self,
        scenario: FoundationCustomerScenario,
    ) -> None:
        params = (
            scenario.customer_id,
            scenario.scenario_id,
            scenario.title,
            json.dumps(_scenario_to_payload(scenario)),
            scenario.source_rule_id,
            scenario.created_at,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, *params)
        logger.info(
            "foundation_customer.upsert customer_id=%s scenario_id=%s",
            scenario.customer_id,
            scenario.scenario_id,
        )

    async def get(
        self,
        customer_id: str,
        scenario_id: str,
    ) -> FoundationCustomerScenario | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_BY_KEY_SQL,
                customer_id,
                scenario_id,
            )
        if row is None:
            return None
        return _row_to_scenario(dict(row))

    async def list_for_customer(
        self,
        customer_id: str,
    ) -> tuple[FoundationCustomerScenario, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_BY_CUSTOMER_SQL, customer_id)
        return tuple(_row_to_scenario(dict(row)) for row in rows)

    async def delete(
        self,
        customer_id: str,
        scenario_id: str,
    ) -> bool:
        async with self._pool.acquire() as conn:
            deleted = await conn.fetchval(
                _DELETE_SQL,
                customer_id,
                scenario_id,
            )
        return deleted is not None


# ── payload helpers ───────────────────────────────────────── #


def _scenario_to_payload(
    scenario: FoundationCustomerScenario,
) -> dict[str, object]:
    """Serialise everything except header fields into a JSONB payload."""
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


def _row_to_scenario(
    row: dict[str, object],
) -> FoundationCustomerScenario:
    """Rebuild a :class:`FoundationCustomerScenario` from a Postgres row."""
    raw_payload: object = row.get("payload") or {}
    if isinstance(raw_payload, str):
        raw_payload = json.loads(raw_payload)
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    return FoundationCustomerScenario(
        customer_id=str(row["customer_id"]),
        scenario_id=str(row["scenario_id"]),
        title=str(row["title"]),
        trigger=str(raw_payload.get("trigger", "")),
        risk_level=str(raw_payload.get("risk_level", "Medium")),
        ai_default_behavior=str(
            raw_payload.get("ai_default_behavior", ""),
        ),
        required_data_checks=_coerce_str_tuple(
            raw_payload.get("required_data_checks"),
        ),
        signals_to_inspect=_coerce_str_tuple(
            raw_payload.get("signals_to_inspect"),
        ),
        should_auto_reply=str(raw_payload.get("should_auto_reply", "")),
        should_escalate_to_pm=str(
            raw_payload.get("should_escalate_to_pm", ""),
        ),
        should_create_task=str(
            raw_payload.get("should_create_task", ""),
        ),
        should_learn_pattern=str(
            raw_payload.get("should_learn_pattern", "No"),
        ),
        pattern_to_learn=str(raw_payload.get("pattern_to_learn", "")),
        example_learned_pattern=str(
            raw_payload.get("example_learned_pattern", ""),
        ),
        memory_types=_coerce_str_tuple(
            raw_payload.get("memory_types"),
        ),
        what_not_to_learn=str(
            raw_payload.get("what_not_to_learn", ""),
        ),
        future_behavior_impact=str(
            raw_payload.get("future_behavior_impact", ""),
        ),
        source_rule_id=str(row.get("source_rule_id") or ""),
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


def _coerce_str_tuple(raw: object) -> tuple[str, ...]:
    """Cast a JSON value into a tuple of strings, tolerant of bad input."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if isinstance(raw, list | tuple):
        return tuple(str(item) for item in raw if item is not None)
    return ()


async def upsert_batch(
    store: FoundationCustomerCatalogStore,
    scenarios: Sequence[FoundationCustomerScenario],
) -> int:
    """Upsert ``scenarios`` through ``store``; return the count written."""
    written = 0
    for scenario in scenarios:
        await store.upsert(scenario)
        written += 1
    return written
