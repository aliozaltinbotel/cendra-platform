"""Foundation store — per-property feature importance (Sprint I).

The Sprint H stop-gap uses a hand-curated dict in
``scenario_features.py``: an engineer writes which features matter
for, say, ``access_code_release`` based on a single PM complaint.
That breaks down the moment a second property has different domain
expectations — a luxury rental's access-code policy is not the same
as a hostel's.

Sprint I replaces the hand-curated map with one the foundation
analyser learns nightly from 6 months of DecisionCase history per
``(property_id, scenario)`` pair.  This module owns the storage
side: a small value object (``ScenarioFoundation``), a Protocol
(``FoundationStore``) describing what consumers need, and an
:class:`InMemoryFoundationStore` for tests and dev environments.

The Postgres implementation is intentionally a follow-up ticket so
this commit can ship without coupling to the runtime DB session
factory.  Once the consumer of the store is also wired
(synthesizer integration is deferred), a thin
``PgScenarioFoundationStore`` will land alongside the SQL migration
already created in ``infra/postgres-init/024_scenario_foundation.sql``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScenarioFoundation:
    """One learned importance row.

    Attributes:
        property_id: Property the importance was learned for.
        scenario: Scenario StrEnum value (``Scenario.X.value``).
            Stored as ``str`` to keep the store decoupled from
            :class:`brain_engine.patterns.models.Scenario`'s enum
            churn — adding a new scenario should not require a
            store-side schema change.
        feature_name: Flat feature key matching what
            ``ConditionSynthesizer._flatten`` would emit.
        importance: Normalised importance in ``[0.0, 1.0]``.
        sample_count: Number of DecisionCases the analyser saw for
            this ``(property_id, scenario)`` pair when computing the
            importance.  Consumers should fall back to the global
            default surface when this is too low.
        computed_at: When the row was last refreshed.  Used to
            decide whether to honour the foundation or fall back to
            the static whitelist via ``BRAIN_FOUNDATION_REFRESH_DAYS``.
    """

    property_id: str
    scenario: str
    feature_name: str
    importance: float
    sample_count: int
    computed_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError(
                "importance must be in [0.0, 1.0]",
            )
        if self.sample_count < 0:
            raise ValueError("sample_count must be >= 0")
        if not self.property_id:
            raise ValueError("property_id must be non-empty")
        if not self.scenario:
            raise ValueError("scenario must be non-empty")
        if not self.feature_name:
            raise ValueError("feature_name must be non-empty")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FoundationStore(Protocol):
    """Read/write surface every foundation backend must expose."""

    async def get(
        self,
        *,
        property_id: str,
        scenario: str,
    ) -> tuple[ScenarioFoundation, ...]:
        """Return all importance rows for ``(property_id, scenario)``.

        Order is descending by importance; consumers typically take
        the top N.  Empty tuple when no rows have been learned yet —
        callers should fall back to the static whitelist.
        """

    async def upsert_many(
        self,
        foundations: Iterable[ScenarioFoundation],
    ) -> int:
        """Insert or refresh a batch of importance rows.

        Implementations replace any existing row matching
        ``(property_id, scenario, feature_name)`` so a re-train
        overwrites stale importance without leaving orphan rows.

        Returns:
            The number of rows written.
        """


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryFoundationStore:
    """Process-local FoundationStore for tests and dev environments.

    Thread-unsafe by design — the production path is async-only and
    wraps a Postgres-backed implementation.  Tests should reuse one
    instance per scenario under test.
    """

    def __init__(self) -> None:
        # Key: (property_id, scenario, feature_name).  Storing the
        # full row (not just importance) keeps :meth:`get` cheap
        # without an extra lookup.
        self._rows: dict[tuple[str, str, str], ScenarioFoundation] = {}

    async def get(
        self,
        *,
        property_id: str,
        scenario: str,
    ) -> tuple[ScenarioFoundation, ...]:
        matches = [
            row
            for (pid, scn, _), row in self._rows.items()
            if pid == property_id and scn == scenario
        ]
        matches.sort(key=lambda r: r.importance, reverse=True)
        return tuple(matches)

    async def upsert_many(
        self,
        foundations: Iterable[ScenarioFoundation],
    ) -> int:
        written = 0
        for row in foundations:
            self._rows[
                (row.property_id, row.scenario, row.feature_name)
            ] = row
            written += 1
        return written

    async def snapshot(self) -> Mapping[
        tuple[str, str, str], ScenarioFoundation,
    ]:
        """Return an immutable copy of the current state for tests."""
        return dict(self._rows)


__all__ = [
    "FoundationStore",
    "InMemoryFoundationStore",
    "ScenarioFoundation",
]
