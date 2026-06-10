"""Test builders for :class:`PatternRule` and friends.

Centralised here so the validator-gate tests under ``tests/`` can
share one well-typed factory instead of duplicating the wiring.

The defaults in :func:`make_rule` produce a rule that passes every
:class:`PatternValidator` check — concrete tests override only the
field(s) they care about (``scenario`` for blacklist tests,
``support_count`` for support-gate tests, etc.).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from brain_engine.patterns.models import (
    DecisionAction,
    DecisionType,
    ExecutionMode,
    PatternRule,
    PatternScope,
    RiskLevel,
    Scenario,
)


def _recent() -> datetime:
    """Return a UTC timestamp safely inside the staleness window."""
    return datetime.now(timezone.utc) - timedelta(days=1)


def make_rule(
    *,
    scenario: Scenario = Scenario.ACCESS_CODE_RELEASE,
    scope: PatternScope = PatternScope.PROPERTY,
    scope_id: str = "test-property-001",
    action_type: DecisionType = DecisionType.RELEASE,
    action_params: dict[str, Any] | None = None,
    conditions: dict[str, Any] | None = None,
    support_count: int = 20,
    counterexample_count: int = 0,
    confidence: float = 1.0,
    pattern_id: str = "rule-fixture-id",
    source_case_ids: tuple[str, ...] = ("c1", "c2", "c3", "c4", "c5"),
    last_seen_at: datetime | None = None,
    risk_level: RiskLevel = RiskLevel.LOW,
    execution_mode: ExecutionMode = ExecutionMode.ASK,
) -> PatternRule:
    """Build a :class:`PatternRule` with validator-friendly defaults.

    The returned rule clears every standard
    :class:`PatternValidator` gate (support, counterexample ratio,
    Wilson lower bound, staleness, non-empty conditions, not
    blacklisted, more than one source case).  Override only the
    fields a given test cares about.
    """
    return PatternRule(
        scenario=scenario,
        scope=scope,
        scope_id=scope_id,
        conditions=(
            conditions
            if conditions is not None
            else {
                "lead_time_hours": {"operator": "gte", "value": 24.0},
                "stay_nights": {"operator": "gte", "value": 2},
            }
        ),
        action=DecisionAction(
            action_type=action_type,
            params=action_params if action_params is not None else {},
        ),
        pattern_id=pattern_id,
        support_count=support_count,
        counterexample_count=counterexample_count,
        confidence=confidence,
        risk_level=risk_level,
        execution_mode=execution_mode,
        last_seen_at=last_seen_at if last_seen_at is not None else _recent(),
        source_case_ids=source_case_ids,
    )
