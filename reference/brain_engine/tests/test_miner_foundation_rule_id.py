"""Tests for PatternMiner / PatternExtractor foundation_scenario_id wiring.

Closes the last gap in the foundation provenance chain (PR #288 +
this PR): cases that already carry ``foundation_scenario_id`` now
propagate it onto the emitted :class:`PatternRule.foundation_scenario_id`
field plus surface every unique slug in ``rule.origin.foundation_scenario_ids``.

Three layers under test:

1. :func:`_dominant_foundation_id` — the pure helper.  Plurality
   wins, ties resolved deterministically, no slugs ⇒ ``None``.
2. :class:`PatternMiner.mine` — both emission code paths (the
   condition-aware ``_emit_rule`` and the legacy ``_build_rule``).
3. :class:`PatternExtractor.extract` — the API extract path that
   mirrors miner semantics for parity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from brain_engine.patterns.extractor import _dominant_foundation_id
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.pattern_miner import PatternMiner

# ── _dominant_foundation_id helper ────────────────────────── #


def _case(
    *,
    case_id: str,
    foundation_scenario_id: str | None,
    last_seen: datetime | None = None,
) -> DecisionCase:
    """Build a minimal :class:`DecisionCase` for the helper tests."""
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.IN_STAY,
        scenario=Scenario.EARLY_CHECKIN,
        property_id="prop-1",
        owner_id="owner-1",
        decision=DecisionAction(
            action_type=DecisionType.APPROVE,
            params={},
        ),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        created_at=last_seen or datetime(2026, 5, 13, tzinfo=UTC),
        foundation_scenario_id=foundation_scenario_id,
    )


def test_dominant_id_returns_none_for_empty_iterable() -> None:
    """Empty input ⇒ ``None``."""
    assert _dominant_foundation_id([]) is None


def test_dominant_id_returns_none_when_all_legacy() -> None:
    """Every case without a slug ⇒ ``None``."""
    cases = [
        _case(case_id=f"c{i}", foundation_scenario_id=None)
        for i in range(5)
    ]
    assert _dominant_foundation_id(cases) is None


def test_dominant_id_returns_unique_slug() -> None:
    """One non-empty slug across N cases ⇒ that slug."""
    cases = [
        _case(case_id=f"c{i}", foundation_scenario_id="s1_16_early")
        for i in range(3)
    ]
    assert _dominant_foundation_id(cases) == "s1_16_early"


def test_dominant_id_picks_strict_plurality() -> None:
    """When one slug strictly outnumbers others, it wins."""
    cases = [
        _case(case_id="a", foundation_scenario_id="s1_16_early"),
        _case(case_id="b", foundation_scenario_id="s1_16_early"),
        _case(case_id="c", foundation_scenario_id="s1_16_early"),
        _case(case_id="d", foundation_scenario_id="s3_112_check"),
    ]
    assert _dominant_foundation_id(cases) == "s1_16_early"


def test_dominant_id_tie_breaks_on_first_occurrence() -> None:
    """Equal counts ⇒ first-seen slug wins (deterministic)."""
    cases = [
        _case(case_id="a", foundation_scenario_id="s3_112_check"),
        _case(case_id="b", foundation_scenario_id="s1_16_early"),
        _case(case_id="c", foundation_scenario_id="s1_16_early"),
        _case(case_id="d", foundation_scenario_id="s3_112_check"),
    ]
    assert _dominant_foundation_id(cases) == "s3_112_check"


def test_dominant_id_skips_empty_strings() -> None:
    """Empty string is treated as missing — skipped like ``None``."""
    cases = [
        _case(case_id="a", foundation_scenario_id=""),
        _case(case_id="b", foundation_scenario_id="s1_16_early"),
    ]
    assert _dominant_foundation_id(cases) == "s1_16_early"


# ── PatternMiner emission path ────────────────────────────── #


def _approve_case(
    *,
    case_id: str,
    foundation_scenario_id: str | None,
    last_seen: datetime,
) -> DecisionCase:
    """Approve case shaped for miner positive bucket."""
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.IN_STAY,
        scenario=Scenario.EARLY_CHECKIN,
        property_id="prop-1",
        owner_id="owner-1",
        decision=DecisionAction(
            action_type=DecisionType.APPROVE,
            params={},
        ),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        created_at=last_seen,
        foundation_scenario_id=foundation_scenario_id,
    )


def test_miner_writes_foundation_scenario_id_on_rule() -> None:
    """Cases with a slug ⇒ rule.foundation_scenario_id populated."""
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"c{i}",
            foundation_scenario_id="s1_16_early",
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    rule = rules[0]
    assert rule.foundation_scenario_id == "s1_16_early"
    assert "s1_16_early" in rule.origin.foundation_scenario_ids


def test_miner_legacy_cases_keep_rule_foundation_none() -> None:
    """Cases without slug ⇒ rule.foundation_scenario_id stays ``None``."""
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"c{i}",
            foundation_scenario_id=None,
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    rule = rules[0]
    assert rule.foundation_scenario_id is None
    assert rule.origin.foundation_scenario_ids == ()


def test_miner_picks_dominant_when_cases_mixed() -> None:
    """Mixed slugs ⇒ rule carries the strict-plurality slug."""
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"checkin-{i}",
            foundation_scenario_id="s1_16_early",
            last_seen=base + timedelta(hours=i),
        )
        for i in range(4)
    ] + [
        _approve_case(
            case_id="x",
            foundation_scenario_id="s3_112_check",
            last_seen=base + timedelta(hours=10),
        ),
    ]
    rules, _report = miner.mine(cases)
    assert rules
    rule = rules[0]
    assert rule.foundation_scenario_id == "s1_16_early"
    # Origin trail surfaces *every* unique slug — for cross-scenario
    # audit downstream.
    assert set(rule.origin.foundation_scenario_ids) == {
        "s1_16_early",
        "s3_112_check",
    }
