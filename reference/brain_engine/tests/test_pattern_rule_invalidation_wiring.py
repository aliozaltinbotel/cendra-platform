"""Tests for the bi-temporal wiring (Sprint 1.6 / 1.7).

These pin the *integration* contracts the resolver enables:

1. **Bootstrap pipeline** — when a new rule contradicts an existing
   active one, ``_mine_and_store`` persists BOTH the new rule and
   the closed candidate (with ``invalid_at`` / ``deactivated_at``
   set) through the same UPSERT path.
2. **conversation/service.py:_parse_iso_timestamp** — best-effort
   parser that powers the router's point-in-time anchor.  Empty /
   garbage input → ``None`` → router falls back to the legacy
   "active rules only" path, preserving live behaviour.

The Prometheus counter (Sprint 1.8) is exercised end-to-end by
verifying the exporter helper registers the metric without raising
— labels round-trip through the registry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from brain_engine.conversation.service import _parse_iso_timestamp
from brain_engine.observability.exporters.prometheus_exporter import (
    build_default_exporter,
)
from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
)
from brain_engine.patterns.models import (
    DecisionAction,
    DecisionType,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.pattern_miner import PatternMiningReport

_NOW = datetime(2026, 5, 5, tzinfo=UTC)


def _rule(
    *,
    pattern_id: str,
    action: DecisionType,
    valid_from: datetime,
) -> PatternRule:
    return PatternRule(
        pattern_id=pattern_id,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        scope=PatternScope.PROPERTY,
        scope_id="prop-1",
        conditions={},
        action=DecisionAction(action_type=action, params={}),
        valid_from=valid_from,
    )


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _RecordingRuleStore:
    """Records every store() call + serves get_active_rules from memory."""

    def __init__(self, seed: list[PatternRule] | None = None) -> None:
        self._rules: dict[str, PatternRule] = {}
        for rule in seed or ():
            self._rules[rule.pattern_id] = rule
        self.store_calls: list[PatternRule] = []

    async def store(self, rule: PatternRule) -> str:
        self.store_calls.append(rule)
        self._rules[rule.pattern_id] = rule
        return rule.pattern_id

    async def get_active_rules(
        self,
        *,
        scenario: Scenario | None = None,
        scope: PatternScope | None = None,
        scope_id: str | None = None,
    ) -> list[PatternRule]:
        return [
            r for r in self._rules.values()
            if r.deactivated_at is None
            and (scenario is None or r.scenario is scenario)
            and (scope is None or r.scope is scope)
            and (scope_id is None or r.scope_id == scope_id)
        ]


class _FixedMiner:
    """Pattern-miner stand-in returning a preset rule list."""

    def __init__(self, rules: list[PatternRule]) -> None:
        self._rules = rules

    def mine(
        self, cases: Any,
    ) -> tuple[list[PatternRule], PatternMiningReport]:
        return list(self._rules), PatternMiningReport()


class _AlwaysValidValidator:
    """PatternValidator stub that approves every rule."""

    def validate(self, rule: PatternRule) -> Any:
        class _OK:
            valid = True
            reasons: tuple[str, ...] = ()

        return _OK()


def _make_pipeline(
    *,
    miner: _FixedMiner,
    rule_store: _RecordingRuleStore,
) -> OnboardingBootstrapPipeline:
    pipeline = object.__new__(OnboardingBootstrapPipeline)
    pipeline._miner = miner
    pipeline._rule_store = rule_store
    pipeline._validator = _AlwaysValidValidator()
    import structlog
    pipeline._log = structlog.get_logger("test").bind(
        component="bootstrap_pipeline",
    )
    return pipeline


# ---------------------------------------------------------------------------
# Bootstrap wiring (Sprint 1.6 — bootstrap path; mirrors the extract
# endpoint wiring so the same resolver runs in both)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_persists_invalidated_candidate() -> None:
    old = _rule(
        pattern_id="OLD",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    store = _RecordingRuleStore(seed=[old])
    pipeline = _make_pipeline(
        miner=_FixedMiner([new]), rule_store=store,
    )
    persisted_count, _ = await pipeline._mine_and_store(
        property_id="prop-1", cases=[], dry_run=False,
    )
    assert persisted_count == 1
    # Both NEW (the freshly mined rule) and the invalidated copy of
    # OLD must hit the store — the latter carries invalid_at set to
    # NEW.valid_from and active=False.
    by_id = {call.pattern_id: call for call in store.store_calls}
    assert "NEW" in by_id
    assert "OLD" in by_id
    assert by_id["OLD"].invalid_at == new.valid_from
    assert by_id["OLD"].deactivated_at is not None
    assert by_id["OLD"].active is False


@pytest.mark.asyncio
async def test_bootstrap_dry_run_skips_invalidation_writes() -> None:
    old = _rule(
        pattern_id="OLD",
        action=DecisionType.APPROVE,
        valid_from=_NOW - timedelta(days=30),
    )
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    store = _RecordingRuleStore(seed=[old])
    pipeline = _make_pipeline(
        miner=_FixedMiner([new]), rule_store=store,
    )
    await pipeline._mine_and_store(
        property_id="prop-1", cases=[], dry_run=True,
    )
    assert store.store_calls == []


@pytest.mark.asyncio
async def test_bootstrap_no_existing_rules_no_invalidation() -> None:
    new = _rule(
        pattern_id="NEW",
        action=DecisionType.DENY,
        valid_from=_NOW,
    )
    store = _RecordingRuleStore(seed=[])
    pipeline = _make_pipeline(
        miner=_FixedMiner([new]), rule_store=store,
    )
    persisted_count, _ = await pipeline._mine_and_store(
        property_id="prop-1", cases=[], dry_run=False,
    )
    assert persisted_count == 1
    assert [c.pattern_id for c in store.store_calls] == ["NEW"]


# ---------------------------------------------------------------------------
# _parse_iso_timestamp (Sprint 1.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_iso"),
    [
        ("2026-05-04T12:34:56Z", "2026-05-04T12:34:56+00:00"),
        ("2026-05-04T12:34:56+00:00", "2026-05-04T12:34:56+00:00"),
        ("2026-05-04T12:34:56", "2026-05-04T12:34:56+00:00"),
        ("2026-05-04T12:34:56+02:00", "2026-05-04T10:34:56+00:00"),
        ("2026-05-04", "2026-05-04T00:00:00+00:00"),
    ],
)
def test_parse_iso_timestamp_accepts_valid_inputs(
    raw: str, expected_iso: str,
) -> None:
    parsed = _parse_iso_timestamp(raw)
    assert parsed is not None
    assert parsed.isoformat() == expected_iso


@pytest.mark.parametrize("raw", ["", "   ", "garbage", "not-a-date"])
def test_parse_iso_timestamp_rejects_invalid_inputs(raw: str) -> None:
    assert _parse_iso_timestamp(raw) is None


# ---------------------------------------------------------------------------
# Prometheus counter (Sprint 1.8)
# ---------------------------------------------------------------------------


def test_pattern_rule_invalidated_counter_records() -> None:
    exporter = build_default_exporter()
    # Record a few ticks across two scopes so the registry contains
    # distinct labelled series the dashboard can split on.
    exporter.record_pattern_rule_invalidated(
        scenario="access_code_release", scope="property",
    )
    exporter.record_pattern_rule_invalidated(
        scenario="access_code_release", scope="property",
    )
    exporter.record_pattern_rule_invalidated(
        scenario="cancellation_request", scope="owner",
    )

    from prometheus_client import generate_latest

    text = generate_latest(exporter._registry).decode()
    assert "brain_pattern_rules_invalidated_total" in text
    # Both scope dimensions show up — labels are wired correctly.
    assert "scope=\"property\"" in text
    assert "scope=\"owner\"" in text
