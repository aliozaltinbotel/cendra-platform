"""Sprint 6 W4 wiring tests — FL-05 learn gate on PatternMiner.

Pins:

* :func:`foundation_learn_gate_enabled` reads
  ``BRAIN_FOUNDATION_LEARN_GATE_ENABLED`` and returns ``False`` by
  default — operators must opt in explicitly.
* :func:`compute_forbidden_foundation_ids` returns an empty
  frozenset when the flag is off (so the wiring path is a no-op
  by default) and the full set of ``Should AI Learn Pattern: No``
  slugs when the flag is on.
* :class:`PatternMiner` filters cases whose
  ``foundation_scenario_id`` lands in the forbidden set before
  bucketing.  When the set is empty (default) every existing test
  still passes — bit-for-bit pre-W4 behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from brain_engine.patterns.foundation_catalog_store import (
    InMemoryFoundationCatalogStore,
    compute_forbidden_foundation_ids,
    foundation_learn_gate_enabled,
)
from brain_engine.patterns.foundation_registry import FoundationScenario
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

# ── fixtures ──────────────────────────────────────────────── #


def _approve_case(
    *,
    case_id: str,
    foundation_scenario_id: str,
    last_seen: datetime,
) -> DecisionCase:
    """Approve case suitable for miner positives."""
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


def _scenario_row(
    *,
    scenario_id: str,
    should_learn_pattern: str,
) -> FoundationScenario:
    """Foundation catalog row carrying the learn-pattern flag under test."""
    return FoundationScenario(
        scenario_id=scenario_id,
        title="test",
        stage_number=1,
        stage_label="Pre-Booking",
        trigger="trigger",
        should_learn_pattern=should_learn_pattern,
    )


# ── flag helper ───────────────────────────────────────────── #


def test_flag_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the env var set the flag returns ``False``."""
    monkeypatch.delenv(
        "BRAIN_FOUNDATION_LEARN_GATE_ENABLED",
        raising=False,
    )
    assert foundation_learn_gate_enabled() is False


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Common truthy strings enable the gate."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_LEARN_GATE_ENABLED",
        value,
    )
    assert foundation_learn_gate_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["", "0", "false", "no", "off"],
)
def test_flag_falsy_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Standard falsy strings keep the gate disabled."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_LEARN_GATE_ENABLED",
        value,
    )
    assert foundation_learn_gate_enabled() is False


# ── compute_forbidden_foundation_ids ─────────────────────── #


@pytest.mark.asyncio
async def test_compute_returns_empty_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off ⇒ helper short-circuits to empty frozenset.

    The store is never touched in this path, so an unconfigured /
    unreachable catalog cannot break the call site.
    """
    monkeypatch.delenv(
        "BRAIN_FOUNDATION_LEARN_GATE_ENABLED",
        raising=False,
    )
    store = InMemoryFoundationCatalogStore()
    await store.upsert_many(
        (_scenario_row(
            scenario_id="s4_209_gas",
            should_learn_pattern="No",
        ),),
        doc_hash="hash1",
    )
    result = await compute_forbidden_foundation_ids(store)
    assert result == frozenset()


@pytest.mark.asyncio
async def test_compute_returns_no_learn_slugs_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on ⇒ helper returns every ``No`` slug from the catalog."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_LEARN_GATE_ENABLED",
        "1",
    )
    store = InMemoryFoundationCatalogStore()
    await store.upsert_many(
        (
            _scenario_row(
                scenario_id="s4_209_gas",
                should_learn_pattern="No",
            ),
            _scenario_row(
                scenario_id="s1_16_early",
                should_learn_pattern="Yes",
            ),
            _scenario_row(
                scenario_id="s5_241_medical",
                should_learn_pattern="no",  # case-insensitive
            ),
            _scenario_row(
                scenario_id="s2_orphan",
                should_learn_pattern="Conditional",
            ),
        ),
        doc_hash="hash1",
    )
    result = await compute_forbidden_foundation_ids(store)
    assert result == frozenset({"s4_209_gas", "s5_241_medical"})


# ── PatternMiner integration ─────────────────────────────── #


def test_miner_with_empty_forbidden_set_unchanged() -> None:
    """Default empty set ⇒ miner mines every case as before W4."""
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"c{i}",
            foundation_scenario_id="s4_209_gas",
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    # The gas-smell slug is still mined when the gate is empty —
    # the runtime caller decides whether to engage the gate.
    assert "s4_209_gas" in rules[0].origin.foundation_scenario_ids


def test_miner_drops_cases_whose_slug_is_forbidden() -> None:
    """Cases tagged with a forbidden slug never enter the buckets.

    Mirrors the production wiring: when the operator opts in via
    the env flag and ``compute_forbidden_foundation_ids`` populates
    the set, the miner refuses to learn from gas-smell / medical /
    broken-glass cases regardless of how many overrides accumulate.
    """
    miner = PatternMiner(
        forbidden_foundation_ids=frozenset({"s4_209_gas"}),
    )
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"c{i}",
            foundation_scenario_id="s4_209_gas",
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    # The miner produced no rules because every case carried a
    # forbidden slug.
    assert rules == []


def test_miner_keeps_allowed_cases_when_forbidden_set_partial() -> None:
    """Allowed slugs still mine, forbidden ones are skipped."""
    miner = PatternMiner(
        forbidden_foundation_ids=frozenset({"s4_209_gas"}),
    )
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"gas-{i}",
            foundation_scenario_id="s4_209_gas",
            last_seen=base + timedelta(hours=i),
        )
        for i in range(3)
    ] + [
        _approve_case(
            case_id=f"checkin-{i}",
            foundation_scenario_id="s1_16_early",
            last_seen=base + timedelta(hours=10 + i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    # Every emitted rule traces back to the allowed slug — never
    # to the gas-smell slug.
    for rule in rules:
        assert "s4_209_gas" not in rule.origin.foundation_scenario_ids


def test_miner_keeps_legacy_cases_with_no_foundation_slug() -> None:
    """Cases without a foundation slug never match the forbidden set.

    Legacy bootstrap cases that pre-date FL-16 carry
    ``foundation_scenario_id=None``; they are never tagged as
    forbidden so the gate cannot wipe out the legacy mining path.
    """
    miner = PatternMiner(
        forbidden_foundation_ids=frozenset({"s4_209_gas"}),
    )
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"legacy-{i}",
            foundation_scenario_id="",  # legacy, untagged
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    assert rules[0].origin.foundation_scenario_ids == ()
