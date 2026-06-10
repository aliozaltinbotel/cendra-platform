"""SQLAlchemy-backed persistence for PatternRules (tenant-scoped).

Production implementation of the :class:`PatternRuleStore` Protocol
defined in :mod:`core.brain.patterns.store` — the sync, Dify-convention
rewrite of the reference's asyncpg ``PostgresPatternRuleStore``
(``patterns/postgres_rule_store.py`` @a761e29).  Semantics preserved:

- UPSERT on the extractor-assigned deterministic ``pattern_id`` —
  a :class:`PatternRule` is mutable evidence (``support_count``,
  ``confidence``, ``last_seen_at``, ``active`` evolve), so re-storing
  overwrites every non-key column.
- ``deactivate`` returns ``True`` only on the active → inactive
  transition (the reference's ``UPDATE … RETURNING`` contract) and
  stamps ``deactivated_at`` exactly once (``COALESCE`` semantics).
- ``get_active_rules`` filters ``active AND (valid_to IS NULL OR
  valid_to > now)`` and orders by confidence descending.

Dify-convention changes: a sync ``sessionmaker`` is injected (no pool
ownership); every query is scoped to the ``tenant_id`` the store is
bound to at construction; datetimes are stored naive-UTC and converted
back to the kernel's tz-aware contract on read.

``rationale`` round-trips through ``action.params["_rationale"]`` as in
the reference, so no extra column is needed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.patterns.models import (
    DecisionAction,
    DecisionType,
    ExecutionMode,
    PatternOrigin,
    PatternRule,
    PatternScope,
    RiskLevel,
)
from libs.datetime_utils import naive_utc_now
from models.brain_rules import BrainPatternRule

__all__ = ["SQLAlchemyPatternRuleStore"]


logger = logging.getLogger(__name__)


def _to_naive(moment: datetime | None) -> datetime | None:
    """Convert a tz-aware kernel datetime to the naive-UTC DB convention."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_aware(moment: datetime | None) -> datetime | None:
    """Convert a naive-UTC DB datetime to the kernel's tz-aware contract."""
    if moment is None:
        return None
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _encode_action(action: DecisionAction) -> dict:
    return {"action_type": action.action_type.value, "params": dict(action.params)}


def _decode_action(raw: dict | None) -> DecisionAction:
    payload = raw or {}
    action_type = DecisionType(payload.get("action_type", DecisionType.INFORM.value))
    return DecisionAction(action_type=action_type, params=payload.get("params") or {})


def _row_to_rule(row: BrainPatternRule) -> PatternRule:
    action = _decode_action(row.action)
    rationale = ""
    if isinstance(action.params, dict):
        rationale = str(action.params.get("_rationale", "") or "")
    valid_from = _to_aware(row.valid_from)
    last_seen_at = _to_aware(row.last_seen_at)
    created_at = _to_aware(row.created_at)
    if valid_from is None or last_seen_at is None or created_at is None:
        raise ValueError(f"rule row {row.pattern_id!r} has NULL in a NOT NULL datetime column")
    return PatternRule(
        pattern_id=row.pattern_id,
        scenario=row.scenario,
        scope=PatternScope(row.scope),
        scope_id=row.scope_id,
        conditions=dict(row.conditions or {}),
        action=action,
        blocker_types=tuple(row.blocker_types or ()),
        support_count=int(row.support_count or 0),
        counterexample_count=int(row.counterexample_count or 0),
        confidence=float(row.confidence or 0.0),
        risk_level=RiskLevel(row.risk_level),
        execution_mode=ExecutionMode(row.execution_mode),
        valid_from=valid_from,
        valid_to=_to_aware(row.valid_to),
        invalid_at=_to_aware(row.invalid_at),
        deactivated_at=_to_aware(row.deactivated_at),
        last_seen_at=last_seen_at,
        source_case_ids=tuple(row.source_case_ids or ()),
        created_at=created_at,
        active=bool(row.active),
        rationale=rationale,
        foundation_scenario_id=row.foundation_scenario_id,
        origin=PatternOrigin.from_jsonable(row.origin),
    )


def _apply_rule(row: BrainPatternRule, rule: PatternRule) -> None:
    """Overwrite every non-key column with the incoming rule's values."""
    row.scenario = rule.scenario
    row.scope = rule.scope.value
    row.scope_id = rule.scope_id
    row.conditions = dict(rule.conditions)
    row.action = _encode_action(rule.action)
    row.blocker_types = list(rule.blocker_types)
    row.support_count = rule.support_count
    row.counterexample_count = rule.counterexample_count
    row.confidence = rule.confidence
    row.risk_level = rule.risk_level.value
    row.execution_mode = rule.execution_mode.value
    row.valid_from = _to_naive(rule.valid_from) or naive_utc_now()
    row.valid_to = _to_naive(rule.valid_to)
    row.invalid_at = _to_naive(rule.invalid_at)
    row.deactivated_at = _to_naive(rule.deactivated_at)
    row.last_seen_at = _to_naive(rule.last_seen_at) or naive_utc_now()
    row.source_case_ids = list(rule.source_case_ids)
    row.active = rule.active
    row.foundation_scenario_id = rule.foundation_scenario_id
    row.origin = rule.origin.to_jsonable()


class SQLAlchemyPatternRuleStore:
    """Tenant-scoped :class:`PatternRuleStore` over Dify's SQLAlchemy stack.

    Satisfies the Protocol structurally — no inheritance, mirroring the
    reference's decoupling of production store from the in-memory one.
    """

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    def _get_row(self, session, pattern_id: str) -> BrainPatternRule | None:
        stmt = select(BrainPatternRule).where(
            BrainPatternRule.tenant_id == self._tenant_id,
            BrainPatternRule.pattern_id == pattern_id,
        )
        return session.execute(stmt).scalar_one_or_none()

    def store(self, rule: PatternRule) -> str:
        """Persist or refresh a rule (UPSERT on ``pattern_id``)."""
        with self._session_maker() as session:
            row = self._get_row(session, rule.pattern_id)
            if row is None:
                row = BrainPatternRule(
                    tenant_id=self._tenant_id,
                    pattern_id=rule.pattern_id,
                    scenario=rule.scenario,
                    scope=rule.scope.value,
                    scope_id=rule.scope_id,
                )
                row.created_at = _to_naive(rule.created_at) or naive_utc_now()
                session.add(row)
            _apply_rule(row, rule)
            session.commit()
        logger.debug(
            "rule_stored pattern_id=%s scenario=%s confidence=%s active=%s",
            rule.pattern_id[:8],
            rule.scenario,
            round(rule.confidence, 2),
            rule.active,
        )
        return rule.pattern_id

    def get(self, pattern_id: str) -> PatternRule | None:
        with self._session_maker() as session:
            row = self._get_row(session, pattern_id)
            return None if row is None else _row_to_rule(row)

    def get_active_rules(
        self,
        *,
        scenario: str | None = None,
        scope: PatternScope | None = None,
        scope_id: str | None = None,
    ) -> list[PatternRule]:
        """Return active, non-expired rules ordered by confidence DESC."""
        stmt = select(BrainPatternRule).where(
            BrainPatternRule.tenant_id == self._tenant_id,
            BrainPatternRule.active.is_(True),
        )
        now = naive_utc_now()
        stmt = stmt.where((BrainPatternRule.valid_to.is_(None)) | (BrainPatternRule.valid_to > now))
        if scenario is not None:
            stmt = stmt.where(BrainPatternRule.scenario == scenario)
        if scope is not None:
            stmt = stmt.where(BrainPatternRule.scope == scope.value)
        if scope_id is not None:
            stmt = stmt.where(BrainPatternRule.scope_id == scope_id)
        stmt = stmt.order_by(BrainPatternRule.confidence.desc())
        with self._session_maker() as session:
            rows = session.execute(stmt).scalars().all()
            return [_row_to_rule(row) for row in rows]

    def deactivate(self, pattern_id: str) -> bool:
        """Mark a rule inactive; ``True`` only on the active → inactive transition."""
        with self._session_maker() as session:
            row = self._get_row(session, pattern_id)
            if row is None or not row.active:
                return False
            row.active = False
            if row.deactivated_at is None:
                row.deactivated_at = naive_utc_now()
            session.commit()
        logger.info("rule_deactivated pattern_id=%s", pattern_id[:8])
        return True

    def update(self, rule: PatternRule) -> None:
        """Delegate to :meth:`store` — the statement is an UPSERT either way."""
        self.store(rule)
