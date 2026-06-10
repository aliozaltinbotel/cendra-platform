"""Tests for the validator gate in :class:`MemorySmokeRunner`.

The smoke harness mirrors the gate added to the bootstrap pipeline:
mined rules are validated before they reach the rule store, so a
diagnostic smoke run on a live dev pod cannot leak blacklisted
scenarios or action categories into the same store the production
endpoints serve.

These tests pin the :meth:`MemorySmokeRunner._persist_rules`
contract: blacklisted rules are skipped (and the store stays
clean), the returned :class:`StageOutcome` ``count`` reflects only
successful writes, and the stage transitions to ``SKIPPED`` with a
descriptive ``detail`` when every mined rule is rejected.
"""

from __future__ import annotations

from typing import Any, cast

from brain_engine.memory.smoke import (
    MemorySmokeRunner,
    SmokeStageStatus,
)
from brain_engine.patterns.models import PatternRule, Scenario

from tests._builders import make_rule


class _RecordingRuleStore:
    """In-memory rule store stand-in that records every store() call."""

    def __init__(self) -> None:
        self.stored: list[PatternRule] = []

    async def store(self, rule: PatternRule) -> str:
        self.stored.append(rule)
        return rule.pattern_id


def _make_runner(rule_store: _RecordingRuleStore) -> MemorySmokeRunner:
    """Build a runner whose only live wiring is the rule store.

    Every other constructor dependency is unused by
    :meth:`_persist_rules` and is filled with a sentinel object so
    the runner instantiates without touching GraphQL, the case
    extractor or the episodic memory tier.
    """
    sentinel = cast(Any, object())
    return MemorySmokeRunner(
        archive_loader=sentinel,
        episode_builder=sentinel,
        case_extractor=sentinel,
        case_store=sentinel,
        episodic_memory=sentinel,
        pattern_miner=sentinel,
        rule_store=cast(Any, rule_store),
    )


async def test_blacklisted_rule_is_not_persisted() -> None:
    blacklisted = make_rule(
        scenario=Scenario.CANCELLATION_REQUEST,
        pattern_id="blacklisted-cancel",
    )
    store = _RecordingRuleStore()
    runner = _make_runner(store)

    outcome = await runner._persist_rules(rules=[blacklisted])

    assert store.stored == []
    assert outcome.status is SmokeStageStatus.SKIPPED
    assert outcome.count == 0
    assert "rejected by validator" in outcome.detail


async def test_valid_rule_is_persisted_and_stage_passes() -> None:
    valid = make_rule(pattern_id="valid-rule")
    store = _RecordingRuleStore()
    runner = _make_runner(store)

    outcome = await runner._persist_rules(rules=[valid])

    assert [r.pattern_id for r in store.stored] == ["valid-rule"]
    assert outcome.status is SmokeStageStatus.PASS
    assert outcome.count == 1


async def test_only_valid_rules_are_persisted_in_mixed_batch() -> None:
    valid = make_rule(pattern_id="valid-rule")
    blacklisted = make_rule(
        scenario=Scenario.DAMAGE_REPORT,
        pattern_id="blacklisted-damage",
    )
    store = _RecordingRuleStore()
    runner = _make_runner(store)

    outcome = await runner._persist_rules(
        rules=[valid, blacklisted],
    )

    assert [r.pattern_id for r in store.stored] == ["valid-rule"]
    assert outcome.status is SmokeStageStatus.PASS
    assert outcome.count == 1


async def test_empty_rule_iterable_reports_no_rules_to_store() -> None:
    store = _RecordingRuleStore()
    runner = _make_runner(store)

    outcome = await runner._persist_rules(rules=[])

    assert store.stored == []
    assert outcome.status is SmokeStageStatus.SKIPPED
    assert outcome.count == 0
    assert outcome.detail == "no rules emitted to store"
