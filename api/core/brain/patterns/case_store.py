"""SQLAlchemy-backed persistence for DecisionCases (tenant-scoped).

Production implementation of the :class:`DecisionCaseStore` Protocol in
:mod:`core.brain.patterns.store` — the sync, Dify-convention rewrite of
the reference's asyncpg ``PostgresDecisionCaseStore``
(``patterns/postgres_store.py`` @a761e29).  Semantics preserved:

- ``store`` is an idempotent append (``ON CONFLICT (case_id) DO
  NOTHING`` in the reference): re-ingesting the same ``case_id`` is a
  no-op — cases are immutable episodic evidence.
- ``search`` / ``count`` AND-combine filters, exclude soft-archived
  rows by default, sort newest-first, and paginate via
  ``(limit, offset)``.
- ``archive`` flips ``archived_at`` exactly once (idempotent).
- ``select_archive_candidates`` returns cases older than the cutoff
  that no active PatternRule references via ``source_case_ids``.
- ``source_event_id`` drill-down filters on the origin trail.  On
  PostgreSQL it uses the same JSONB containment predicate as the
  reference (``origin @> '{"source_event_ids": ["<id>"]}'``, GIN-
  indexable); on other dialects (unit tests run SQLite) it falls back
  to filtering in Python after the SQL filters.

Dify-convention changes: injected sync ``sessionmaker``; every query
scoped to the ``tenant_id`` bound at construction; naive-UTC storage
with tz-aware conversion at the boundary.  ``decision_at`` is not
persisted (matches the reference schema).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from core.brain.patterns.models import (
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    ResolutionType,
)
from libs.datetime_utils import naive_utc_now
from models.brain_decision import BrainDecisionCase
from models.brain_rules import BrainPatternRule

__all__ = ["SQLAlchemyDecisionCaseStore"]


logger = logging.getLogger(__name__)


def _to_naive(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _encode_decision(action: DecisionAction) -> dict:
    return {"action_type": action.action_type.value, "params": dict(action.params)}


def _encode_outcome(outcome: CaseOutcome) -> dict:
    raw = asdict(outcome)
    resolution = outcome.resolution_type
    raw["resolution_type"] = resolution.value if resolution is not None else None
    return raw


def _decode_decision(raw: dict | None) -> DecisionAction:
    payload = raw or {}
    action_type = DecisionType(payload.get("action_type", DecisionType.INFORM.value))
    return DecisionAction(action_type=action_type, params=payload.get("params") or {})


def _decode_outcome(raw: dict | None) -> CaseOutcome:
    payload = raw or {}
    resolution_raw = payload.get("resolution_type")
    resolution = ResolutionType(resolution_raw) if resolution_raw is not None else None
    return CaseOutcome(
        guest_replied=bool(payload.get("guest_replied", False)),
        human_overrode=bool(payload.get("human_overrode", False)),
        approval_required=bool(payload.get("approval_required", False)),
        approved=payload.get("approved"),
        successful=payload.get("successful"),
        resolution_type=resolution,
        revenue_impact=payload.get("revenue_impact"),
    )


def _row_to_case(row: BrainDecisionCase) -> DecisionCase:
    created_at = _to_aware(row.created_at)
    if created_at is None:
        raise ValueError(f"case row {row.case_id!r} has NULL created_at")
    return DecisionCase(
        case_id=row.case_id,
        stage=row.stage,
        scenario=row.scenario,
        property_id=row.property_id,
        owner_id=row.owner_id,
        reservation_id=row.reservation_id,
        guest_id=row.guest_id,
        message_text=row.message_text or "",
        message_language=row.message_language or "en",
        response_text=row.response_text or "",
        extracted_entities=dict(row.extracted_entities or {}),
        pms_snapshot=dict(row.pms_snapshot or {}),
        calendar_snapshot=dict(row.calendar_snapshot or {}),
        ops_snapshot=dict(row.ops_snapshot or {}),
        guest_snapshot=dict(row.guest_snapshot or {}),
        decision=_decode_decision(row.decision),
        executed_actions=tuple(row.executed_actions or ()),
        outcome=_decode_outcome(row.outcome),
        evidence_source_ids=tuple(row.evidence_source_ids or ()),
        created_at=created_at,
        source=CaseSource(row.source or CaseSource.LIVE.value),
        orchestrator_verdict=dict(row.orchestrator_verdict or {}),
        archived_at=_to_aware(row.archived_at),
        foundation_scenario_id=row.foundation_scenario_id,
        origin=PatternOrigin.from_jsonable(row.origin),
    )


class SQLAlchemyDecisionCaseStore:
    """Tenant-scoped :class:`DecisionCaseStore` over Dify's SQLAlchemy stack."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    # ── write side ──────────────────────────────────────────────── #

    def store(self, case: DecisionCase) -> str:
        """Idempotently append a case; re-storing an existing id is a no-op."""
        with self._session_maker() as session:
            exists = session.execute(
                select(BrainDecisionCase.id).where(
                    BrainDecisionCase.tenant_id == self._tenant_id,
                    BrainDecisionCase.case_id == case.case_id,
                )
            ).first()
            if exists is not None:
                return case.case_id
            row = BrainDecisionCase(
                tenant_id=self._tenant_id,
                case_id=case.case_id,
                stage=case.stage,
                scenario=case.scenario,
                decision_type=case.decision.action_type.value,
                property_id=case.property_id,
                owner_id=case.owner_id,
                reservation_id=case.reservation_id,
                guest_id=case.guest_id,
                message_text=case.message_text,
                message_language=case.message_language,
                response_text=case.response_text,
                extracted_entities=dict(case.extracted_entities),
                pms_snapshot=dict(case.pms_snapshot),
                calendar_snapshot=dict(case.calendar_snapshot),
                ops_snapshot=dict(case.ops_snapshot),
                guest_snapshot=dict(case.guest_snapshot),
                decision=_encode_decision(case.decision),
                executed_actions=list(case.executed_actions),
                outcome=_encode_outcome(case.outcome),
                evidence_source_ids=list(case.evidence_source_ids),
                source=case.source.value,
                orchestrator_verdict=dict(case.orchestrator_verdict),
                archived_at=_to_naive(case.archived_at),
                foundation_scenario_id=case.foundation_scenario_id,
                origin=case.origin.to_jsonable(),
            )
            row.created_at = _to_naive(case.created_at) or naive_utc_now()
            session.add(row)
            session.commit()
        logger.debug(
            "case_stored case_id=%s scenario=%s",
            case.case_id[:8],
            case.scenario,
        )
        return case.case_id

    def archive(self, case_id: str) -> bool:
        """Soft-archive once; ``True`` only on the active → archived transition."""
        with self._session_maker() as session:
            row = session.execute(
                select(BrainDecisionCase).where(
                    BrainDecisionCase.tenant_id == self._tenant_id,
                    BrainDecisionCase.case_id == case_id,
                    BrainDecisionCase.archived_at.is_(None),
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            row.archived_at = naive_utc_now()
            session.commit()
        logger.debug("case_archived case_id=%s", case_id[:8])
        return True

    # ── read side ───────────────────────────────────────────────── #

    def get(self, case_id: str) -> DecisionCase | None:
        with self._session_maker() as session:
            row = session.execute(
                select(BrainDecisionCase).where(
                    BrainDecisionCase.tenant_id == self._tenant_id,
                    BrainDecisionCase.case_id == case_id,
                )
            ).scalar_one_or_none()
            return None if row is None else _row_to_case(row)

    def _filtered(
        self,
        stmt,
        *,
        scenario: str | None,
        property_id: str | None,
        owner_id: str | None,
        stage: str | None,
        include_archived: bool = False,
    ):
        stmt = stmt.where(BrainDecisionCase.tenant_id == self._tenant_id)
        if not include_archived:
            stmt = stmt.where(BrainDecisionCase.archived_at.is_(None))
        if scenario is not None:
            stmt = stmt.where(BrainDecisionCase.scenario == scenario)
        if property_id is not None:
            stmt = stmt.where(BrainDecisionCase.property_id == property_id)
        if owner_id is not None:
            stmt = stmt.where(BrainDecisionCase.owner_id == owner_id)
        if stage is not None:
            stmt = stmt.where(BrainDecisionCase.stage == stage)
        return stmt

    @staticmethod
    def _origin_predicate(session, source_event_id: str):
        """PostgreSQL-only JSONB containment predicate (GIN-indexable)."""
        payload = json.dumps({"source_event_ids": [source_event_id]})
        return text("origin @> :origin_payload ::jsonb").bindparams(origin_payload=payload)

    def search(
        self,
        *,
        scenario: str | None = None,
        property_id: str | None = None,
        owner_id: str | None = None,
        stage: str | None = None,
        source_event_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list[DecisionCase]:
        """Search with AND-combined filters, newest first, paginated."""
        with self._session_maker() as session:
            stmt = self._filtered(
                select(BrainDecisionCase),
                scenario=scenario,
                property_id=property_id,
                owner_id=owner_id,
                stage=stage,
                include_archived=include_archived,
            ).order_by(BrainDecisionCase.created_at.desc())
            pg = session.get_bind().dialect.name == "postgresql"
            if source_event_id is not None and pg:
                stmt = stmt.where(self._origin_predicate(session, source_event_id))
            if source_event_id is None or pg:
                stmt = stmt.limit(limit).offset(offset)
                rows = session.execute(stmt).scalars().all()
                return [_row_to_case(row) for row in rows]
            # non-pg fallback: filter the origin trail in Python, then window
            rows = session.execute(stmt).scalars().all()
            cases = [
                _row_to_case(row) for row in rows if source_event_id in (row.origin or {}).get("source_event_ids", [])
            ]
            return cases[offset : offset + limit]

    def get_by_reservation(self, reservation_id: str) -> list[DecisionCase]:
        with self._session_maker() as session:
            stmt = (
                select(BrainDecisionCase)
                .where(
                    BrainDecisionCase.tenant_id == self._tenant_id,
                    BrainDecisionCase.reservation_id == reservation_id,
                )
                .order_by(BrainDecisionCase.created_at.asc())
            )
            rows = session.execute(stmt).scalars().all()
            return [_row_to_case(row) for row in rows]

    def count(
        self,
        *,
        scenario: str | None = None,
        property_id: str | None = None,
        owner_id: str | None = None,
        stage: str | None = None,
        source_event_id: str | None = None,
    ) -> int:
        """Count matching active cases (always excludes archived rows)."""
        with self._session_maker() as session:
            pg = session.get_bind().dialect.name == "postgresql"
            if source_event_id is not None and not pg:
                return len(
                    self.search(
                        scenario=scenario,
                        property_id=property_id,
                        owner_id=owner_id,
                        stage=stage,
                        source_event_id=source_event_id,
                        limit=2**31 - 1,
                    )
                )
            stmt = self._filtered(
                select(func.count(BrainDecisionCase.id)),
                scenario=scenario,
                property_id=property_id,
                owner_id=owner_id,
                stage=stage,
            )
            if source_event_id is not None:
                stmt = stmt.where(self._origin_predicate(session, source_event_id))
            return int(session.execute(stmt).scalar_one())

    def select_archive_candidates(
        self,
        *,
        cutoff: datetime,
        limit: int = 1000,
    ) -> list[str]:
        """Cases older than ``cutoff`` not referenced by any active rule.

        The reference runs ``NOT EXISTS (… case_id = ANY(pr.source_case_ids))``
        against Postgres text arrays.  ``source_case_ids`` is a JSON column
        here, so the reference set is assembled in Python — active rule
        counts are small (bounded by mined rules per tenant), the candidate
        scan stays a single indexed query.
        """
        with self._session_maker() as session:
            referenced: set[str] = set()
            rule_rows = session.execute(
                select(BrainPatternRule.source_case_ids).where(
                    BrainPatternRule.tenant_id == self._tenant_id,
                    BrainPatternRule.active.is_(True),
                )
            ).all()
            for (ids,) in rule_rows:
                referenced.update(ids or ())
            stmt = (
                select(BrainDecisionCase.case_id)
                .where(
                    BrainDecisionCase.tenant_id == self._tenant_id,
                    BrainDecisionCase.archived_at.is_(None),
                    BrainDecisionCase.created_at < (_to_naive(cutoff) or naive_utc_now()),
                )
                .order_by(BrainDecisionCase.created_at.asc())
            )
            case_ids = session.execute(stmt).scalars().all()
            return [cid for cid in case_ids if cid not in referenced][:limit]
