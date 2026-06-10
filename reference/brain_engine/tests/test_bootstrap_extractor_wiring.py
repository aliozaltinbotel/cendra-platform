"""Tests for the bootstrap-side ``PatternExtractor`` wiring.

Mümin round-4 follow-up (2026-05-11): the legacy bootstrap pipeline
relied solely on :class:`PatternMiner`, which synthesises weak
conditional rules at support 2-3.  The validator (
``MIN_SUPPORT_AUTO = 5``) rejected them, so ``rules_emitted`` came
back as zero even when ``/patterns/extract`` on the same data
produced a valid whole-group rule (support 9 on 323133's
``access_code_release`` scenario).  The fix adds
:meth:`OnboardingBootstrapPipeline._extract_per_scenario_and_store`
that runs the API-grade :class:`PatternExtractor` per distinct
scenario as a second mining pass.

These tests pin the wiring contract:

* Extractor invoked once per distinct non-``GENERAL`` scenario.
* Extractor-emitted rules go through the same validator + store
  path as miner-emitted rules.
* ``rules_emitted`` counts both miner and extractor outputs.
* No extractor wired ⇒ method is a no-op, returns 0.
* ``GENERAL`` scenario cases are skipped (matches extractor's own
  filter).
* Dry-run paths increment the count but do not persist.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
)
from brain_engine.patterns.extractor import (
    ExtractionResult,
    PatternExtractor,
)
from brain_engine.patterns.models import (
    DecisionCase,
    PatternRule,
    PatternScope,
    Scenario,
)
from brain_engine.patterns.pattern_miner import PatternMiningReport
from brain_engine.patterns.validator import (
    PatternValidator,
    ValidationResult,
)

from tests._builders import make_rule


class _RecordingRuleStore:
    """Captures every successful ``store()`` call."""

    def __init__(self) -> None:
        self.stored: list[PatternRule] = []

    async def store(self, rule: PatternRule) -> str:
        self.stored.append(rule)
        return rule.pattern_id

    async def get_active_rules(
        self,
        *,
        scenario: Scenario,
        scope: PatternScope,
        scope_id: str,
    ) -> list[PatternRule]:
        return []

    async def deactivate(self, *_: Any, **__: Any) -> None:
        return None


class _FixedExtractor:
    """Stand-in that emits a preset rule list per scenario."""

    def __init__(
        self,
        per_scenario: dict[Scenario, list[PatternRule]],
    ) -> None:
        self._rules = per_scenario
        self.calls: list[tuple[Scenario, str, str]] = []

    async def extract_patterns(
        self,
        *,
        scenario: Scenario,
        property_id: str,
        owner_id: str,
    ) -> ExtractionResult:
        self.calls.append((scenario, property_id, owner_id))
        return ExtractionResult(
            rules=tuple(self._rules.get(scenario, ())),
            total_cases=0,
            positive_cases=0,
            negative_cases=0,
        )


class _AcceptValidator:
    """Validator that accepts every rule it sees."""

    def validate(self, rule: PatternRule) -> ValidationResult:
        return ValidationResult(valid=True, reasons=())


class _RejectValidator:
    """Validator that rejects every rule with a fixed reason."""

    def validate(self, rule: PatternRule) -> ValidationResult:
        return ValidationResult(
            valid=False, reasons=("test_reject",),
        )


def _case(
    *,
    scenario: Scenario,
    property_id: str = "p1",
) -> DecisionCase:
    """Build a minimal :class:`DecisionCase` for routing tests."""
    return cast(
        DecisionCase,
        make_rule(
            scenario=scenario,
            property_id=property_id,
        )._as_case_proxy()
        if hasattr(make_rule(scenario=scenario), "_as_case_proxy")
        else None,
    )


def _build_pipeline(
    *,
    extractor: _FixedExtractor | None,
    rule_store: _RecordingRuleStore | None,
    validator: PatternValidator | None = None,
) -> OnboardingBootstrapPipeline:
    """Build a pipeline with only the wiring we need to test."""
    return OnboardingBootstrapPipeline(
        archive_loader=cast(Any, object()),
        episode_builder=cast(Any, object()),
        case_extractor=cast(Any, object()),
        case_store=cast(Any, object()),
        pattern_extractor=cast(Any, extractor),
        rule_store=cast(Any, rule_store),
        pattern_validator=cast(Any, validator or _AcceptValidator()),
    )


def _make_case(
    *,
    scenario: Scenario,
    property_id: str = "p1",
    owner_id: str = "",
) -> DecisionCase:
    """Build a learnable :class:`DecisionCase` with a non-empty outcome."""
    from brain_engine.patterns.models import (
        BookingStage,
        CaseOutcome,
        CaseSource,
        DecisionAction,
        DecisionType,
        ResolutionType,
    )

    return DecisionCase(
        stage=BookingStage.PRE_ARRIVAL,
        scenario=scenario,
        property_id=property_id,
        owner_id=owner_id,
        reservation_id=None,
        guest_id=None,
        message_text="hi",
        decision=DecisionAction(action_type=DecisionType.INFORM),
        response_text="ok",
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
    )


# ── core behaviour ─────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_extractor_invoked_once_per_distinct_scenario() -> None:
    """The extractor is called for each distinct non-GENERAL scenario."""
    extractor = _FixedExtractor(per_scenario={})
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=extractor, rule_store=rule_store,
    )
    cases = [
        _make_case(scenario=Scenario.ACCESS_CODE_RELEASE),
        _make_case(scenario=Scenario.ACCESS_CODE_RELEASE),
        _make_case(scenario=Scenario.LATE_CHECKOUT),
        _make_case(scenario=Scenario.GENERAL),  # filtered
    ]
    emitted = await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=cases,
        dry_run=False,
    )
    assert emitted == 0  # no rules in stub
    scenarios_called = {call[0] for call in extractor.calls}
    assert scenarios_called == {
        Scenario.ACCESS_CODE_RELEASE,
        Scenario.LATE_CHECKOUT,
    }


@pytest.mark.asyncio
async def test_extractor_rules_pass_validator_and_persist() -> None:
    """Valid extractor rules land in the rule store."""
    rule = make_rule(scenario=Scenario.ACCESS_CODE_RELEASE)
    extractor = _FixedExtractor(
        per_scenario={Scenario.ACCESS_CODE_RELEASE: [rule]},
    )
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=extractor, rule_store=rule_store,
    )
    emitted = await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=[_make_case(scenario=Scenario.ACCESS_CODE_RELEASE)],
        dry_run=False,
    )
    assert emitted == 1
    assert len(rule_store.stored) == 1
    assert rule_store.stored[0] is rule


@pytest.mark.asyncio
async def test_rules_rejected_by_validator_are_not_persisted() -> None:
    """Validator-rejected rules are dropped from emit count + store."""
    rule = make_rule(scenario=Scenario.ACCESS_CODE_RELEASE)
    extractor = _FixedExtractor(
        per_scenario={Scenario.ACCESS_CODE_RELEASE: [rule]},
    )
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=extractor,
        rule_store=rule_store,
        validator=cast(Any, _RejectValidator()),
    )
    emitted = await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=[_make_case(scenario=Scenario.ACCESS_CODE_RELEASE)],
        dry_run=False,
    )
    assert emitted == 0
    assert rule_store.stored == []


@pytest.mark.asyncio
async def test_dry_run_counts_but_does_not_persist() -> None:
    """Dry-run path increments the count without touching the store."""
    rule = make_rule(scenario=Scenario.ACCESS_CODE_RELEASE)
    extractor = _FixedExtractor(
        per_scenario={Scenario.ACCESS_CODE_RELEASE: [rule]},
    )
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=extractor, rule_store=rule_store,
    )
    emitted = await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=[_make_case(scenario=Scenario.ACCESS_CODE_RELEASE)],
        dry_run=True,
    )
    assert emitted == 1
    assert rule_store.stored == []


@pytest.mark.asyncio
async def test_no_extractor_returns_zero() -> None:
    """Missing extractor short-circuits the helper to zero."""
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=None, rule_store=rule_store,
    )
    emitted = await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=[_make_case(scenario=Scenario.ACCESS_CODE_RELEASE)],
        dry_run=False,
    )
    assert emitted == 0


@pytest.mark.asyncio
async def test_no_rule_store_returns_zero() -> None:
    """Missing rule store short-circuits the helper to zero."""
    extractor = _FixedExtractor(per_scenario={})
    pipeline = _build_pipeline(
        extractor=extractor, rule_store=None,
    )
    emitted = await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=[_make_case(scenario=Scenario.ACCESS_CODE_RELEASE)],
        dry_run=False,
    )
    assert emitted == 0
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_general_scenario_is_skipped() -> None:
    """GENERAL cases never trigger an extractor call."""
    extractor = _FixedExtractor(per_scenario={})
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=extractor, rule_store=rule_store,
    )
    await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=[
            _make_case(scenario=Scenario.GENERAL),
            _make_case(scenario=Scenario.GENERAL),
        ],
        dry_run=False,
    )
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_extractor_exception_logs_and_continues() -> None:
    """A failing scenario does not abort the remaining scenarios."""

    class _FlakyExtractor:
        def __init__(self) -> None:
            self.calls = 0

        async def extract_patterns(
            self,
            *,
            scenario: Scenario,
            property_id: str,
            owner_id: str,
        ) -> ExtractionResult:
            self.calls += 1
            if scenario is Scenario.ACCESS_CODE_RELEASE:
                raise RuntimeError("boom")
            return ExtractionResult(
                rules=(make_rule(scenario=scenario),),
            )

    flaky = _FlakyExtractor()
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=cast(Any, flaky), rule_store=rule_store,
    )
    emitted = await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="",
        cases=[
            _make_case(scenario=Scenario.ACCESS_CODE_RELEASE),
            _make_case(scenario=Scenario.LATE_CHECKOUT),
        ],
        dry_run=False,
    )
    # ACCESS_CODE_RELEASE failed; LATE_CHECKOUT emitted one rule.
    assert emitted == 1
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_extractor_owner_id_threaded_through() -> None:
    """The owner_id from the first case is forwarded to the extractor."""
    extractor = _FixedExtractor(per_scenario={})
    rule_store = _RecordingRuleStore()
    pipeline = _build_pipeline(
        extractor=extractor, rule_store=rule_store,
    )
    cases = [
        _make_case(
            scenario=Scenario.ACCESS_CODE_RELEASE,
            owner_id="owner-007",
        ),
    ]
    await pipeline._extract_per_scenario_and_store(
        property_id="p1",
        owner_id="owner-007",
        cases=cases,
        dry_run=False,
    )
    assert extractor.calls == [
        (Scenario.ACCESS_CODE_RELEASE, "p1", "owner-007"),
    ]
