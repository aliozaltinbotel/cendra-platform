"""Persistent storage for DecisionCases and PatternRules.

Defines Protocol-based abstractions (DIP) with an InMemory implementation
for development/testing.  Production uses PostgresDecisionCaseStore
(not included here — requires asyncpg / SQLAlchemy).

DecisionCases are stored long-term because pattern learning requires
seasonal windows (a rule learned in summer may not apply in winter).
Redis is not suitable for this — Postgres is the target backend.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog

from brain_engine.patterns.models import (
    BookingStage,
    DecisionCase,
    PatternRule,
    PatternScope,
    Scenario,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# DecisionCase store protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DecisionCaseStore(Protocol):
    """Abstract storage for DecisionCase persistence.

    Implementations must support:
    - Single-case storage and retrieval.
    - Multi-criteria search (scenario, property, owner, stage).
    - Reservation-scoped queries (all cases for one booking).
    - Count queries for pattern extraction thresholds.
    """

    async def store(self, case: DecisionCase) -> str:
        """Persist a DecisionCase, returning its case_id."""
        ...

    async def get(self, case_id: str) -> DecisionCase | None:
        """Retrieve a single case by ID."""
        ...

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
        """Search cases by multiple criteria.

        All filters are AND-combined.  None means "any".
        Soft-archived rows (Sprint 4) are excluded by default;
        ``include_archived=True`` returns them alongside active
        rows for audit / forensics queries.

        ``source_event_id`` (Mümin 2026-05-15 round-5 #4) filters
        on :pyattr:`DecisionCase.origin.source_event_ids`.  A case
        matches when its origin's tuple contains the supplied id
        (drill-down from a rule's ``/origin.source_event_ids`` array
        back to the contributing cases).  Implementations should
        use the JSONB GIN index defined in migration 028 so the
        query stays cheap at scale.

        ``offset`` is applied after sorting by ``created_at`` DESC so
        callers can paginate through ``(limit, offset)`` windows
        without repeats.  Defaults to ``0`` for backward compatibility.
        """
        ...

    async def get_by_reservation(
        self,
        reservation_id: str,
    ) -> list[DecisionCase]:
        """Return all cases associated with a reservation."""
        ...

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

        Mirrors :meth:`search`'s filter set (including
        ``source_event_id``) so paginated callers can report a
        meaningful unfiltered total alongside the limited page.
        Always excludes soft-archived rows (Sprint 4).
        """
        ...

    async def archive(self, case_id: str) -> bool:
        """Soft-archive a single case (Sprint 4 — forgetting curve).

        Idempotent: returns ``True`` only when the row
        transitioned from active to archived; ``False`` when
        the case was already archived or does not exist.
        """
        ...

    async def select_archive_candidates(
        self,
        *,
        cutoff: datetime,
        limit: int = 1000,
    ) -> list[str]:
        """Return ``case_id``s eligible for soft-archive.

        Eligibility: ``created_at < cutoff`` AND not referenced
        by any active ``PatternRule.source_case_ids``.  Caller
        archives the returned ids one-by-one through
        :meth:`archive`.
        """
        ...


# ---------------------------------------------------------------------------
# PatternRule store protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PatternRuleStore(Protocol):
    """Abstract storage for learned PatternRules."""

    async def store(self, rule: PatternRule) -> str:
        """Persist a PatternRule, returning its pattern_id."""
        ...

    async def get(self, pattern_id: str) -> PatternRule | None:
        """Retrieve a single rule by ID."""
        ...

    async def get_active_rules(
        self,
        *,
        scenario: Scenario | None = None,
        scope: PatternScope | None = None,
        scope_id: str | None = None,
    ) -> list[PatternRule]:
        """Return active rules matching the given scope."""
        ...

    async def deactivate(self, pattern_id: str) -> bool:
        """Mark a rule as inactive."""
        ...

    async def update(self, rule: PatternRule) -> None:
        """Update a rule (e.g. after confidence refresh)."""
        ...


# ---------------------------------------------------------------------------
# In-memory DecisionCase store
# ---------------------------------------------------------------------------


class InMemoryDecisionCaseStore:
    """In-memory DecisionCase store for development and testing.

    Supports all protocol methods with O(n) scans.  Not suitable for
    production — data is lost on restart, and search is linear.

    Attributes:
        _cases: Dict mapping case_id → DecisionCase.
        _by_reservation: Index mapping reservation_id → list of case_ids.
    """

    def __init__(self) -> None:
        self._cases: dict[str, DecisionCase] = {}
        self._by_reservation: dict[str, list[str]] = defaultdict(list)
        self._log = logger.bind(component="inmemory_case_store")

    async def store(self, case: DecisionCase) -> str:
        """Store a DecisionCase.

        Args:
            case: The case to persist.

        Returns:
            The case_id of the stored case.
        """
        self._cases[case.case_id] = case
        if case.reservation_id:
            self._by_reservation[case.reservation_id].append(case.case_id)
        self._log.debug(
            "case_stored",
            case_id=case.case_id[:8],
            scenario=case.scenario.value,
            total_cases=len(self._cases),
        )
        return case.case_id

    async def get(self, case_id: str) -> DecisionCase | None:
        """Retrieve a case by ID.

        Args:
            case_id: Unique identifier.

        Returns:
            The DecisionCase or None.
        """
        return self._cases.get(case_id)

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
        """Search cases with AND-combined filters.

        Args:
            scenario: Filter by scenario.
            property_id: Filter by property.
            owner_id: Filter by owner.
            stage: Filter by booking stage.
            source_event_id: Filter by membership in
                ``case.origin.source_event_ids``.  Lets callers
                drill from a rule's ``/origin.source_event_ids`` array
                back to the cases that produced those upstream events.
                ``None`` (default) keeps the legacy behaviour.
            limit: Maximum results to return.
            offset: Number of leading rows (after newest-first sort)
                to skip.  Defaults to ``0`` for backward compatibility.
            include_archived: When ``False`` (default) excludes
                soft-archived cases — mirrors the Postgres-store
                behaviour the miner relies on.

        Returns:
            List of matching DecisionCases, newest first, after
            ``offset`` is skipped and ``limit`` is applied.
        """
        results: list[DecisionCase] = []
        for case in self._cases.values():
            if not include_archived and case.archived_at is not None:
                continue
            if scenario is not None and case.scenario != scenario:
                continue
            if property_id is not None and case.property_id != property_id:
                continue
            if owner_id is not None and case.owner_id != owner_id:
                continue
            if stage is not None and case.stage != stage:
                continue
            if (
                source_event_id is not None
                and source_event_id not in case.origin.source_event_ids
            ):
                continue
            results.append(case)

        results.sort(key=lambda c: c.created_at, reverse=True)
        return results[offset : offset + limit]

    async def get_by_reservation(
        self,
        reservation_id: str,
    ) -> list[DecisionCase]:
        """Return all cases for a reservation.

        Args:
            reservation_id: PMS reservation identifier.

        Returns:
            List of DecisionCases for this reservation.
        """
        case_ids = self._by_reservation.get(reservation_id, [])
        results = [self._cases[cid] for cid in case_ids if cid in self._cases]
        results.sort(key=lambda c: c.created_at)
        return results

    async def count(
        self,
        *,
        scenario: Scenario | None = None,
        property_id: str | None = None,
        owner_id: str | None = None,
        stage: BookingStage | None = None,
        source_event_id: str | None = None,
    ) -> int:
        """Count cases matching filters.

        Mirrors :meth:`search`'s filter set (including
        ``source_event_id``) so paginated callers can report a
        meaningful unfiltered total alongside the limited page.
        Soft-archived rows are always excluded.

        Args:
            scenario: Filter by scenario.
            property_id: Filter by property.
            owner_id: Filter by owner.
            stage: Filter by booking stage.
            source_event_id: Filter by membership in
                ``case.origin.source_event_ids``.

        Returns:
            Number of matching cases.
        """
        total = 0
        for case in self._cases.values():
            if case.archived_at is not None:
                continue
            if scenario is not None and case.scenario != scenario:
                continue
            if property_id is not None and case.property_id != property_id:
                continue
            if owner_id is not None and case.owner_id != owner_id:
                continue
            if stage is not None and case.stage != stage:
                continue
            if (
                source_event_id is not None
                and source_event_id not in case.origin.source_event_ids
            ):
                continue
            total += 1
        return total

    async def archive(self, case_id: str) -> bool:
        """Soft-archive a case in memory by replacing it with a copy.

        ``DecisionCase`` is frozen (per ``master_guide_2026``), so
        we emit a :func:`dataclasses.replace` clone with
        ``archived_at`` populated and overwrite the dict entry.
        Returns ``True`` only on the active → archived transition.
        """
        from dataclasses import replace

        case = self._cases.get(case_id)
        if case is None or case.archived_at is not None:
            return False
        self._cases[case_id] = replace(
            case,
            archived_at=datetime.now(UTC),
        )
        self._log.debug("case_archived", case_id=case_id[:8])
        return True

    async def select_archive_candidates(
        self,
        *,
        cutoff: datetime,
        limit: int = 1000,
    ) -> list[str]:
        """Mirror of the Postgres helper for unit-test parity.

        The in-memory store does not see PatternRules so the
        "not referenced by any active rule" filter is omitted —
        callers using the in-memory store for tests can layer
        their own filter atop the returned list.
        """
        candidates: list[tuple[datetime, str]] = []
        for case in self._cases.values():
            if case.archived_at is not None:
                continue
            if case.created_at >= cutoff:
                continue
            candidates.append((case.created_at, case.case_id))
        candidates.sort(key=lambda pair: pair[0])
        return [cid for _, cid in candidates[:limit]]


# ---------------------------------------------------------------------------
# In-memory PatternRule store
# ---------------------------------------------------------------------------


class InMemoryPatternRuleStore:
    """In-memory PatternRule store for development and testing.

    Attributes:
        _rules: Dict mapping pattern_id → PatternRule.
    """

    def __init__(self) -> None:
        self._rules: dict[str, PatternRule] = {}
        self._log = logger.bind(component="inmemory_rule_store")

    async def store(self, rule: PatternRule) -> str:
        """Store a PatternRule.

        Args:
            rule: The rule to persist.

        Returns:
            The pattern_id.
        """
        self._rules[rule.pattern_id] = rule
        self._log.debug(
            "rule_stored",
            pattern_id=rule.pattern_id[:8],
            scenario=rule.scenario.value,
            confidence=round(rule.confidence, 2),
        )
        return rule.pattern_id

    async def get(self, pattern_id: str) -> PatternRule | None:
        """Retrieve a rule by ID.

        Args:
            pattern_id: Unique identifier.

        Returns:
            The PatternRule or None.
        """
        return self._rules.get(pattern_id)

    async def get_active_rules(
        self,
        *,
        scenario: Scenario | None = None,
        scope: PatternScope | None = None,
        scope_id: str | None = None,
    ) -> list[PatternRule]:
        """Return active rules matching scope filters.

        Args:
            scenario: Filter by scenario.
            scope: Filter by scope level.
            scope_id: Filter by scope identifier.

        Returns:
            List of active rules, sorted by confidence descending.
        """
        results: list[PatternRule] = []
        for rule in self._rules.values():
            if not rule.active:
                continue
            if rule.is_expired:
                continue
            if scenario is not None and rule.scenario != scenario:
                continue
            if scope is not None and rule.scope != scope:
                continue
            if scope_id is not None and rule.scope_id != scope_id:
                continue
            results.append(rule)

        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    async def deactivate(self, pattern_id: str) -> bool:
        """Mark a rule as inactive.

        Args:
            pattern_id: Rule identifier.

        Returns:
            True if found and deactivated.
        """
        from dataclasses import replace

        rule = self._rules.get(pattern_id)
        if rule is None:
            return False
        self._rules[pattern_id] = replace(rule, active=False)
        self._log.info("rule_deactivated", pattern_id=pattern_id[:8])
        return True

    async def update(self, rule: PatternRule) -> None:
        """Update a rule in the store.

        Args:
            rule: Updated PatternRule instance.
        """
        self._rules[rule.pattern_id] = rule
