# Module docstring + comments quote Ali's Turkish requirement
# verbatim; the Turkish letters are intentional, not typos.
"""Foundation update feedback loop (FL-13).

Closes Ali's Turkish requirement #2 — *"Eğer o pattern o müşteri
için yanlış oluşmuşsa burada ilgili foundation yeni gelen sektörel
bilgiyle update edilebilir."*  When a foundation scenario repeatedly
mis-fits a customer (e.g. PM overrides the engine's foundation-
derived response three or more times in comparable cases), Brain
Engine produces a :class:`FoundationUpdateCandidate` and stores it
in a backlog table for human review.

Hard rule — *we never auto-rewrite the foundation markdown.*  The
foundation document is curated knowledge; mechanical overwrites
would let a single customer's idiosyncrasies bleed into the sector
baseline.  Instead this module emits a backlog row that a human
reviewer triages.  When the reviewer accepts the change, the
markdown gets a manual edit and re-parsing the catalog via
:mod:`core.brain.patterns.foundation_registry` plus an upsert
through :class:`FoundationCatalogStore` propagates it to live
deployments.

Module layout:

* :class:`UpdateSeverity` — coarse triage label (``low`` / ``medium``
  / ``high`` based on override count and risk class).
* :class:`FoundationUpdateCandidate` — the backlog row itself.
* :class:`FoundationUpdateStore` Protocol + in-memory and Postgres
  implementations.
* :func:`detect_foundation_drift` — pure helper that scans a batch
  of :class:`DecisionCase` rows and emits candidates whenever an
  ``(foundation_scenario_id, scope_id)`` pair accumulates the
  configured number of PM overrides.

The detector is deliberately I/O-free so nightly_consolidator can
drive it with whatever case set it pulls from the store.  Wiring
the detector into the nightly job is the FL-13b follow-up.

Honest scope:

* Sprint 5 ships the data layer + the pure detector.  No
  conversation/service.py or nightly_consolidator changes.
* The "proposed change" payload is free-form text — the reviewer
  uses it as a starting prompt, not as machine-applied edit.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from core.brain.patterns.models import DecisionCase

__all__ = [
    "DEFAULT_DRIFT_THRESHOLD",
    "FoundationUpdateCandidate",
    "FoundationUpdateStore",
    "InMemoryFoundationUpdateStore",
    "PgFoundationUpdateStore",
    "UpdateSeverity",
    "detect_foundation_drift",
]


logger = logging.getLogger(__name__)


# Default threshold mirrors the Hospitality MD guidance "propose a
# scoped rule after 3+ comparable cases" — three overrides is the
# smallest sample that survives the noise of a single PM mood swing
# while still surfacing real drift early enough for the operator to
# react.
DEFAULT_DRIFT_THRESHOLD: Final[int] = 3


class UpdateSeverity(StrEnum):
    """Triage severity for a :class:`FoundationUpdateCandidate`.

    The severity is derived from the override count alone in the
    Sprint 5 detector — three overrides land at ``LOW``, five+ at
    ``MEDIUM``, ten+ at ``HIGH``.  Risk-class-aware promotion
    (e.g. any override on a ``Critical`` foundation scenario goes
    straight to ``HIGH``) lands with the FL-13b wiring PR that
    feeds the detector real PMS context.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_SEVERITY_MEDIUM_OVERRIDE_FLOOR: Final[int] = 5
_SEVERITY_HIGH_OVERRIDE_FLOOR: Final[int] = 10


def _severity_for(override_count: int) -> UpdateSeverity:
    """Map ``override_count`` to a :class:`UpdateSeverity` triage tier."""
    if override_count >= _SEVERITY_HIGH_OVERRIDE_FLOOR:
        return UpdateSeverity.HIGH
    if override_count >= _SEVERITY_MEDIUM_OVERRIDE_FLOOR:
        return UpdateSeverity.MEDIUM
    return UpdateSeverity.LOW


def _utc_now() -> datetime:
    """Return current UTC datetime — extracted for testability."""
    return datetime.now(UTC)


def _new_id() -> str:
    """Generate a unique identifier for backlog rows."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class FoundationUpdateCandidate:
    """One backlog row suggesting a foundation scenario revision.

    Created by :func:`detect_foundation_drift` (or any future
    pipeline) when a foundation scenario keeps producing engine
    responses the PM overrides.  The candidate is *never*
    auto-applied to the foundation markdown — a human reviewer
    triages it via the future ``/foundation/updates`` admin
    surface.

    Attributes:
        candidate_id: Unique identifier (auto-generated UUID hex).
        foundation_scenario_id: Slug of the foundation row that
            mis-fit, matching
            :data:`FoundationScenario.scenario_id`.
        scope: Where the drift was observed (e.g. ``"property"``,
            ``"owner"``).  Free-form so callers can fold in
            portfolio-level findings without an enum change.
        scope_id: Identifier for the scope above (property_id,
            owner_id, …).
        override_count: How many PM overrides drove the detector
            to surface this candidate.  Always ``>= 1``.
        severity: Triage tier (:class:`UpdateSeverity`).
        deviation_evidence: Free-form explanation summarising why
            the foundation row mis-fit.  The reviewer reads this
            verbatim — keep it sentence-style, not log spaghetti.
        proposed_change: Optional suggested edit to the foundation
            markdown.  Empty string when the detector cannot
            propose a concrete change (the reviewer authors one).
        source_case_ids: ``DecisionCase.case_id`` values that
            triggered the candidate.  The reviewer follows the
            link back to the raw evidence.
        created_at: When the candidate was emitted.

    Raises:
        ValueError: When ``foundation_scenario_id`` is empty or
            ``override_count`` is non-positive.
    """

    foundation_scenario_id: str
    scope: str
    scope_id: str
    override_count: int
    severity: UpdateSeverity
    deviation_evidence: str
    proposed_change: str = ""
    source_case_ids: tuple[str, ...] = ()
    candidate_id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.foundation_scenario_id:
            raise ValueError("foundation_scenario_id required")
        if self.override_count <= 0:
            raise ValueError("override_count must be positive")


# ── store protocol + implementations ──────────────────────── #


@runtime_checkable
class FoundationUpdateStore(Protocol):
    """Read/write façade for the foundation update backlog.

    Consumers (the nightly consolidator, the future admin API)
    depend on this Protocol rather than a concrete store —
    :class:`InMemoryFoundationUpdateStore` satisfies it for tests
    and dev environments; :class:`PgFoundationUpdateStore` is the
    production wiring.
    """

    def upsert(
        self,
        candidate: FoundationUpdateCandidate,
    ) -> None:
        """Persist a candidate.

        Idempotent on ``candidate_id`` — re-running the detector
        on the same cases produces the same id (because the
        detector keys on ``foundation_scenario_id + scope + scope_id``)
        and the upsert refreshes the severity / evidence / count
        in place.
        """
        ...

    def list_pending(
        self,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
    ) -> tuple[FoundationUpdateCandidate, ...]:
        """Return every pending candidate, optionally scoped."""
        ...

    def get(
        self,
        candidate_id: str,
    ) -> FoundationUpdateCandidate | None:
        """Return one candidate by id, or ``None`` if absent."""
        ...


class InMemoryFoundationUpdateStore:
    """Process-local store for tests and dev environments.

    Idempotent upserts via the ``(foundation_scenario_id, scope,
    scope_id)`` natural key — the second call updates the existing
    row instead of producing a duplicate.
    """

    __slots__ = ("_by_id", "_by_key")

    def __init__(self) -> None:
        self._by_id: dict[str, FoundationUpdateCandidate] = {}
        self._by_key: dict[
            tuple[str, str, str],
            str,
        ] = {}

    def upsert(
        self,
        candidate: FoundationUpdateCandidate,
    ) -> None:
        key = (
            candidate.foundation_scenario_id,
            candidate.scope,
            candidate.scope_id,
        )
        existing_id = self._by_key.get(key)
        if existing_id is not None and existing_id != candidate.candidate_id:
            self._by_id.pop(existing_id, None)
        self._by_id[candidate.candidate_id] = candidate
        self._by_key[key] = candidate.candidate_id

    def list_pending(
        self,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
    ) -> tuple[FoundationUpdateCandidate, ...]:
        rows: list[FoundationUpdateCandidate] = list(self._by_id.values())
        if scope is not None:
            rows = [r for r in rows if r.scope == scope]
        if scope_id is not None:
            rows = [r for r in rows if r.scope_id == scope_id]
        ordered = sorted(rows, key=lambda r: r.created_at)
        return tuple(ordered)

    def get(
        self,
        candidate_id: str,
    ) -> FoundationUpdateCandidate | None:
        return self._by_id.get(candidate_id)


_UPSERT_SQL: Final[str] = """
INSERT INTO foundation_update_candidates (
    candidate_id,
    foundation_scenario_id,
    scope,
    scope_id,
    override_count,
    severity,
    deviation_evidence,
    proposed_change,
    source_case_ids,
    created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (foundation_scenario_id, scope, scope_id) DO UPDATE SET
    override_count = EXCLUDED.override_count,
    severity = EXCLUDED.severity,
    deviation_evidence = EXCLUDED.deviation_evidence,
    proposed_change = EXCLUDED.proposed_change,
    source_case_ids = EXCLUDED.source_case_ids,
    updated_at = now()
"""


_SELECT_COLUMNS: Final[str] = (
    "candidate_id, foundation_scenario_id, scope, scope_id, "
    "override_count, severity, deviation_evidence, proposed_change, "
    "source_case_ids, created_at"
)


_SELECT_BY_ID_SQL: Final[str] = f"SELECT {_SELECT_COLUMNS} FROM foundation_update_candidates WHERE candidate_id = $1"


# Port note: the reference's asyncpg PgFoundationUpdateStore was dropped —
# per porting rule 7 the persistent implementation will be a Dify
# SQLAlchemy store, written when the foundation analysis pipeline is
# wired (the Protocol + InMemory variants above are the contract).


def _row_to_candidate(
    row: dict[str, object],
) -> FoundationUpdateCandidate:
    """Build a :class:`FoundationUpdateCandidate` from a Postgres row."""
    raw_cases: object = row.get("source_case_ids") or ()
    if isinstance(raw_cases, str):
        # Some test fixtures store the array as JSON text — survive
        # that variant by decoding once.
        decoded = json.loads(raw_cases)
        raw_cases = list(decoded) if isinstance(decoded, list) else []
    if not isinstance(raw_cases, list | tuple):
        raw_cases = []
    override_count_raw = row["override_count"]
    if isinstance(override_count_raw, int | float | str):
        override_count = int(override_count_raw)
    else:
        override_count = 0
    return FoundationUpdateCandidate(
        candidate_id=str(row["candidate_id"]),
        foundation_scenario_id=str(row["foundation_scenario_id"]),
        scope=str(row["scope"]),
        scope_id=str(row["scope_id"]),
        override_count=override_count,
        severity=UpdateSeverity(str(row["severity"])),
        deviation_evidence=str(row.get("deviation_evidence") or ""),
        proposed_change=str(row.get("proposed_change") or ""),
        source_case_ids=tuple(str(c) for c in raw_cases),
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


# ── drift detector ────────────────────────────────────────── #


def detect_foundation_drift(
    cases: Iterable[DecisionCase],
    *,
    scope: str = "property",
    threshold: int = DEFAULT_DRIFT_THRESHOLD,
) -> tuple[FoundationUpdateCandidate, ...]:
    """Surface foundation scenarios the PM keeps overriding (FL-13).

    Groups the supplied :class:`DecisionCase` rows by
    ``(foundation_scenario_id, scope_id)`` and emits one
    :class:`FoundationUpdateCandidate` whenever the count of cases
    with ``outcome.human_overrode == True`` meets or exceeds
    ``threshold``.  ``scope_id`` is taken from the case's
    ``property_id`` (``scope="property"``) or ``owner_id``
    (``scope="owner"``) depending on the ``scope`` argument.

    The function is pure: it neither persists candidates nor reads
    from the existing store.  The caller decides what to do with
    the returned tuple — typically the nightly consolidator will
    iterate the tuple and call ``store.upsert`` for each entry.

    Args:
        cases: A batch of :class:`DecisionCase` rows.  Cases without
            a ``foundation_scenario_id`` (legacy rows or events
            that did not pass through the FL-16 orchestrator) are
            silently skipped — they cannot drift against a
            foundation row they are not linked to.
        scope: Either ``"property"`` (default) or ``"owner"``.  Any
            other value treats every case as belonging to one
            global scope keyed by the empty string.
        threshold: Minimum override count before a candidate is
            emitted.  Defaults to :data:`DEFAULT_DRIFT_THRESHOLD`
            (``3`` — the Hospitality MD's "3+ comparable cases"
            guidance).

    Returns:
        A tuple of candidates in deterministic order
        (``(foundation_scenario_id, scope_id)`` ASC).  Empty when
        no group crosses the threshold.

    Raises:
        ValueError: When ``threshold`` is not positive.
    """
    if threshold <= 0:
        raise ValueError("threshold must be positive")

    groups: dict[
        tuple[str, str],
        list[DecisionCase],
    ] = defaultdict(list)
    for case in cases:
        scenario_id = case.foundation_scenario_id
        if not scenario_id:
            continue
        if not case.outcome.human_overrode:
            continue
        scope_id = _scope_id_for(case, scope)
        groups[(scenario_id, scope_id)].append(case)

    candidates: list[FoundationUpdateCandidate] = []
    for (scenario_id, scope_id), bucket in sorted(groups.items()):
        if len(bucket) < threshold:
            continue
        override_count = len(bucket)
        candidates.append(
            FoundationUpdateCandidate(
                foundation_scenario_id=scenario_id,
                scope=scope,
                scope_id=scope_id,
                override_count=override_count,
                severity=_severity_for(override_count),
                deviation_evidence=(
                    f"PM overrode the foundation-derived response "
                    f"in {override_count} cases under scope "
                    f"{scope}={scope_id!r}.  Foundation entry "
                    f"{scenario_id!r} may need revision."
                ),
                source_case_ids=tuple(c.case_id for c in bucket),
            ),
        )
    return tuple(candidates)


def _scope_id_for(case: DecisionCase, scope: str) -> str:
    """Pick the scope_id off a case based on the requested ``scope``."""
    if scope == "property":
        return case.property_id
    if scope == "owner":
        return case.owner_id
    return ""


# ── batch helper ──────────────────────────────────────────── #


def upsert_candidates(
    store: FoundationUpdateStore,
    candidates: Sequence[FoundationUpdateCandidate],
) -> int:
    """Upsert ``candidates`` through ``store``; return the count written.

    Convenience helper for callers (nightly consolidator, admin
    API) that already produced a candidate tuple via
    :func:`detect_foundation_drift` and want to persist the whole
    batch in one call.
    """
    written = 0
    for candidate in candidates:
        store.upsert(candidate)
        written += 1
    return written
