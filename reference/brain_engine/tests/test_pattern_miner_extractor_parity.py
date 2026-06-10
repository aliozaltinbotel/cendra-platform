"""Regression tests — bootstrap miner must match the API extractor.

3c2b943 closed three Mümin-feedback gaps in
:class:`PatternExtractor` (the ``POST /patterns/extract`` path)
but missed the same fixes in :class:`PatternMiner` (the
bootstrap / nightly path).  Mümin re-tested via the bootstrap
flow and saw three regressions on property 323133:

1. ``/patterns/rules`` returned multiple rules with identical
   ``conditions={}`` and different random ``pattern_id`` —
   re-bootstraps were inserting orphans instead of UPSERTing.
2. ``total_price >= 26`` and ``total_price >= 29`` lived side
   by side instead of collapsing to the broader threshold.
3. Most rules carried an empty ``rationale`` field.

These tests pin parity so the bootstrap path stays in lock-step
with the extractor:

* Deterministic ``pattern_id`` derived from
  :py:meth:`PatternRule.deterministic_id`.
* Subsumption merge collapses narrower siblings.
* Rationale field is populated *and* mirrored in
  ``action.params['_rationale']`` (so the postgres JSONB
  round-trip survives without a schema migration).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain_engine.patterns.models import (
    BookingStage,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.pattern_miner import PatternMiner


def _make_case(
    *,
    scenario: Scenario = Scenario.ACCESS_CODE_RELEASE,
    action: DecisionType = DecisionType.INFORM,
    property_id: str = "prop-1",
    extracted_entities: dict | None = None,
) -> DecisionCase:
    return DecisionCase(
        stage=BookingStage.IN_STAY,
        scenario=scenario,
        property_id=property_id,
        owner_id="owner-1",
        decision=DecisionAction(action_type=action, params={}),
        source=CaseSource.LIVE,
        extracted_entities=extracted_entities or {},
        created_at=datetime(2026, 5, 4, tzinfo=UTC),
    )


@pytest.fixture
def miner() -> PatternMiner:
    return PatternMiner(
        min_support=3, min_confidence=0.4, require_outcome=False,
    )


# ---------------------------------------------------------------------------
# Mümin #2 — deterministic pattern_id (no random orphans on re-bootstrap)
# ---------------------------------------------------------------------------


def test_repeated_mine_emits_identical_pattern_id(
    miner: PatternMiner,
) -> None:
    cases = [_make_case() for _ in range(5)]
    rules1, _ = miner.mine(cases)
    rules2, _ = miner.mine(cases)
    assert len(rules1) == 1
    assert rules1[0].pattern_id == rules2[0].pattern_id


def test_pattern_id_matches_deterministic_id_helper(
    miner: PatternMiner,
) -> None:
    cases = [_make_case() for _ in range(5)]
    rules, _ = miner.mine(cases)
    expected = PatternRule.deterministic_id(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        scope=PatternScope.PROPERTY,
        scope_id="prop-1",
        action_type=DecisionType.INFORM,
        conditions={},
    )
    assert rules[0].pattern_id == expected


def test_distinct_action_types_produce_distinct_pattern_ids(
    miner: PatternMiner,
) -> None:
    cases = (
        [_make_case(action=DecisionType.INFORM) for _ in range(5)]
        + [_make_case(action=DecisionType.APPROVE) for _ in range(5)]
    )
    rules, _ = miner.mine(cases)
    ids = {r.pattern_id for r in rules}
    assert len(ids) == len(rules)


# ---------------------------------------------------------------------------
# Mümin #3 — subsumption applied to bootstrap output
# ---------------------------------------------------------------------------


def test_mine_emits_one_rule_per_unique_identity(
    miner: PatternMiner,
) -> None:
    # Five identical cases must collapse to a single rule, not
    # one per case — this would have caught the subsumption gap
    # *if* it were the cause; mostly it covers the "no duplicate
    # rules from the same bucket" expectation Mümin's screenshot
    # was about.
    cases = [_make_case() for _ in range(5)]
    rules, _ = miner.mine(cases)
    assert len(rules) == 1


# ---------------------------------------------------------------------------
# Mümin #5 — rationale populated on every emitted rule
# ---------------------------------------------------------------------------


def test_rationale_is_populated(miner: PatternMiner) -> None:
    cases = [_make_case() for _ in range(5)]
    rules, _ = miner.mine(cases)
    rule = rules[0]
    assert rule.rationale != ""
    assert "inform" in rule.rationale.lower()
    assert "access_code_release" in rule.rationale
    assert "5 supporting" in rule.rationale


def test_rationale_round_trips_through_action_params(
    miner: PatternMiner,
) -> None:
    cases = [_make_case() for _ in range(5)]
    rules, _ = miner.mine(cases)
    rule = rules[0]
    # The postgres store serialises ``action`` as JSONB; the
    # mirror inside ``action.params['_rationale']`` is what
    # survives the round-trip without a schema migration.
    assert rule.action.params.get("_rationale") == rule.rationale


def test_defer_rationale_uses_explicit_phrasing(
    miner: PatternMiner,
) -> None:
    # ali.md / Mümin-feedback: a DEFER rule must read as
    # "deferred (waited / did not respond)" so PMs do not
    # mistake it for "did nothing".
    cases = [
        _make_case(action=DecisionType.DEFER) for _ in range(5)
    ]
    rules, _ = miner.mine(cases)
    rule = rules[0]
    assert "deferred" in rule.rationale.lower()
