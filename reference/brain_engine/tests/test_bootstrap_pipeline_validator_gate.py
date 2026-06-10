"""Tests for the validator gate in :class:`OnboardingBootstrapPipeline`.

Pre-fix the bootstrap pipeline persisted every mined PatternRule
without running :class:`PatternValidator`, so blacklisted scenarios
(``NEVER_AUTO_SCENARIOS``) and blacklisted action categories
(``NEVER_AUTO_LEARN``) leaked into the rule store and surfaced via
``GET /patterns/rules`` even though ``POST /patterns/extract`` flagged
them as ``valid: false``.

These tests pin the gate's behaviour so the regression cannot
re-emerge: every rule that fails validation must be skipped, the
returned count must reflect *validated* rules (not raw miner output),
and ``dry_run=True`` must still skip blacklisted rules from the count.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
)
from brain_engine.patterns.models import (
    DecisionCase,
    PatternRule,
    Scenario,
)
from brain_engine.patterns.pattern_miner import PatternMiningReport

from tests._builders import make_rule


class _RecordingRuleStore:
    """In-memory rule store stand-in that records every store() call."""

    def __init__(self) -> None:
        self.stored: list[PatternRule] = []

    async def store(self, rule: PatternRule) -> str:
        self.stored.append(rule)
        return rule.pattern_id


class _FixedMiner:
    """Pattern miner stand-in that returns a preset rule list."""

    def __init__(self, rules: list[PatternRule]) -> None:
        self._rules = rules

    def mine(
        self,
        cases: Iterable[DecisionCase],
    ) -> tuple[list[PatternRule], PatternMiningReport]:
        return list(self._rules), PatternMiningReport()


def _make_pipeline(
    *,
    miner: _FixedMiner,
    rule_store: _RecordingRuleStore,
) -> OnboardingBootstrapPipeline:
    """Build a pipeline whose only live wiring is the miner + store.

    The other constructor dependencies are unused by
    :meth:`_mine_and_store` and are filled with sentinel objects so
    the pipeline instantiates without touching the network or any
    database.
    """
    sentinel = cast(Any, object())
    return OnboardingBootstrapPipeline(
        archive_loader=sentinel,
        episode_builder=sentinel,
        case_extractor=sentinel,
        case_store=sentinel,
        pattern_miner=cast(Any, miner),
        rule_store=cast(Any, rule_store),
    )


async def test_blacklisted_scenario_rule_is_not_persisted() -> None:
    blacklisted = make_rule(
        scenario=Scenario.CANCELLATION_REQUEST,
        pattern_id="blacklisted-cancel",
    )
    miner = _FixedMiner([blacklisted])
    store = _RecordingRuleStore()
    pipeline = _make_pipeline(miner=miner, rule_store=store)

    stored_count, _ = await pipeline._mine_and_store(
        property_id="prop-1",
        cases=[],
        dry_run=False,
    )

    assert store.stored == []
    assert stored_count == 0


async def test_blacklisted_action_category_rule_is_not_persisted() -> None:
    blacklisted = make_rule(
        action_params={"category": "tax_filing"},
        pattern_id="blacklisted-tax",
    )
    miner = _FixedMiner([blacklisted])
    store = _RecordingRuleStore()
    pipeline = _make_pipeline(miner=miner, rule_store=store)

    stored_count, _ = await pipeline._mine_and_store(
        property_id="prop-1",
        cases=[],
        dry_run=False,
    )

    assert store.stored == []
    assert stored_count == 0


async def test_valid_rule_is_persisted() -> None:
    valid = make_rule(pattern_id="valid-rule")
    miner = _FixedMiner([valid])
    store = _RecordingRuleStore()
    pipeline = _make_pipeline(miner=miner, rule_store=store)

    stored_count, _ = await pipeline._mine_and_store(
        property_id="prop-1",
        cases=[],
        dry_run=False,
    )

    assert [r.pattern_id for r in store.stored] == ["valid-rule"]
    assert stored_count == 1


async def test_only_valid_rules_are_persisted_in_mixed_batch() -> None:
    valid = make_rule(pattern_id="valid-rule")
    blacklisted = make_rule(
        scenario=Scenario.DAMAGE_REPORT,
        pattern_id="blacklisted-damage",
    )
    miner = _FixedMiner([valid, blacklisted])
    store = _RecordingRuleStore()
    pipeline = _make_pipeline(miner=miner, rule_store=store)

    stored_count, _ = await pipeline._mine_and_store(
        property_id="prop-1",
        cases=[],
        dry_run=False,
    )

    assert [r.pattern_id for r in store.stored] == ["valid-rule"]
    assert stored_count == 1


async def test_dry_run_does_not_persist_but_still_filters() -> None:
    valid = make_rule(pattern_id="valid-rule")
    blacklisted = make_rule(
        scenario=Scenario.CANCELLATION_REQUEST,
        pattern_id="blacklisted-cancel",
    )
    miner = _FixedMiner([valid, blacklisted])
    store = _RecordingRuleStore()
    pipeline = _make_pipeline(miner=miner, rule_store=store)

    stored_count, _ = await pipeline._mine_and_store(
        property_id="prop-1",
        cases=[],
        dry_run=True,
    )

    assert store.stored == []
    assert stored_count == 1
