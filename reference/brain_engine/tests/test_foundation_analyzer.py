"""Tests for the Sprint I foundation analyser.

Covers:

* Flag plumbing for ``BRAIN_FOUNDATION_ANALYZER_ENABLED`` and
  ``BRAIN_FOUNDATION_REFRESH_DAYS``.
* End-to-end ``analyze`` flow against an in-memory store using a
  stub :class:`MlSynthesizer` — exercises the analyser's logic
  without scikit-learn.
* Short-circuit paths (empty bucket, no features above gate).
* Persistence shape — sample_count, computed_at, ordering.

The :class:`brain_engine.patterns.condition_synthesizer._flatten`
function is reused on real DecisionCase fixtures so the analyser's
feature surface stays aligned with the synthesiser, which is the
whole point of Sprint I.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone

import pytest

from brain_engine.patterns.foundation_analyzer import (
    DEFAULT_REFRESH_DAYS,
    FoundationAnalysisOutcome,
    FoundationAnalyzer,
    configured_refresh_days,
    foundation_analyzer_enabled,
)
from brain_engine.patterns.foundation_store import (
    InMemoryFoundationStore,
)
from brain_engine.patterns.ml_synthesizer import (
    FeatureImportance,
    MlSynthesisResult,
)
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionCase,
    DecisionType,
    Scenario,
)


# ---------------------------------------------------------------------------
# Stubs and fixtures
# ---------------------------------------------------------------------------


class _StubMlSynthesizer:
    """Returns a pre-baked :class:`MlSynthesisResult`."""

    def __init__(self, result: MlSynthesisResult) -> None:
        self._result = result
        self.calls: list[
            tuple[
                Sequence[dict[str, object]],
                Sequence[dict[str, object]],
            ]
        ] = []

    def synthesize(
        self,
        *,
        target_features: Sequence[dict[str, object]],
        other_features: Sequence[dict[str, object]],
        max_depth: int | None = None,  # noqa: ARG002
    ) -> MlSynthesisResult:
        self.calls.append((list(target_features), list(other_features)))
        # Echo the actual counts so analyser arithmetic stays honest.
        return MlSynthesisResult(
            feature_importance=self._result.feature_importance,
            target_count=len(target_features),
            other_count=len(other_features),
            max_depth_used=self._result.max_depth_used,
        )


def _make_case(
    *,
    case_id: str,
    decision_type: DecisionType,
    pms_snapshot: dict[str, object] | None = None,
) -> DecisionCase:
    """Minimal DecisionCase fixture for the analyser tests."""
    return DecisionCase(
        case_id=case_id,
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        message_text="msg",
        response_text="resp",
        decision=DecisionAction(action_type=decision_type),
        pms_snapshot=pms_snapshot or {},
    )


@pytest.fixture(autouse=True)
def _reset_foundation_env() -> Iterator[None]:
    """Strip Sprint I env vars before each test to avoid leakage."""
    snapshot = {
        key: os.environ.pop(key, None)
        for key in (
            "BRAIN_FOUNDATION_ANALYZER_ENABLED",
            "BRAIN_FOUNDATION_REFRESH_DAYS",
        )
    }
    try:
        yield
    finally:
        for key in (
            "BRAIN_FOUNDATION_ANALYZER_ENABLED",
            "BRAIN_FOUNDATION_REFRESH_DAYS",
        ):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_flag_off_by_default() -> None:
    assert foundation_analyzer_enabled() is False


@pytest.mark.parametrize(
    "raw", ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(raw: str) -> None:
    os.environ["BRAIN_FOUNDATION_ANALYZER_ENABLED"] = raw
    assert foundation_analyzer_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "garbage"])
def test_flag_falsy_values(raw: str) -> None:
    os.environ["BRAIN_FOUNDATION_ANALYZER_ENABLED"] = raw
    assert foundation_analyzer_enabled() is False


def test_default_refresh_days_when_env_unset() -> None:
    assert configured_refresh_days() == DEFAULT_REFRESH_DAYS


def test_refresh_days_env_override() -> None:
    os.environ["BRAIN_FOUNDATION_REFRESH_DAYS"] = "30"
    assert configured_refresh_days() == 30


@pytest.mark.parametrize("raw", ["abc", "1.5"])
def test_malformed_refresh_days_raises(raw: str) -> None:
    os.environ["BRAIN_FOUNDATION_REFRESH_DAYS"] = raw
    with pytest.raises(ValueError, match="positive integer"):
        configured_refresh_days()


@pytest.mark.parametrize("raw", ["0", "-7"])
def test_non_positive_refresh_days_raises(raw: str) -> None:
    os.environ["BRAIN_FOUNDATION_REFRESH_DAYS"] = raw
    with pytest.raises(ValueError, match="positive integer"):
        configured_refresh_days()


# ---------------------------------------------------------------------------
# analyze() — short circuits
# ---------------------------------------------------------------------------


async def test_analyze_no_target_cases_skips_with_reason() -> None:
    store = InMemoryFoundationStore()
    synth = _StubMlSynthesizer(MlSynthesisResult())
    analyzer = FoundationAnalyzer(store=store, synthesizer=synth)

    cases = [
        _make_case(case_id="c1", decision_type=DecisionType.DEFER),
        _make_case(case_id="c2", decision_type=DecisionType.ASK),
    ]

    outcome = await analyzer.analyze(
        property_id="p1",
        scenario="access_code_release",
        cases=cases,
        target_action=DecisionType.APPROVE,
    )
    assert outcome.skipped_reason == "empty_bucket"
    assert outcome.rows_written == 0
    assert synth.calls == []  # never invoked


async def test_analyze_no_other_cases_skips_with_reason() -> None:
    store = InMemoryFoundationStore()
    synth = _StubMlSynthesizer(MlSynthesisResult())
    analyzer = FoundationAnalyzer(store=store, synthesizer=synth)

    cases = [
        _make_case(case_id="c1", decision_type=DecisionType.APPROVE),
    ]
    outcome = await analyzer.analyze(
        property_id="p1",
        scenario="access_code_release",
        cases=cases,
        target_action=DecisionType.APPROVE,
    )
    assert outcome.skipped_reason == "empty_bucket"
    assert outcome.rows_written == 0


async def test_analyze_no_features_above_gate_skips_with_reason() -> None:
    """Synthesizer returned no importances above the threshold."""
    store = InMemoryFoundationStore()
    synth = _StubMlSynthesizer(MlSynthesisResult(feature_importance=()))
    analyzer = FoundationAnalyzer(store=store, synthesizer=synth)

    cases = [
        _make_case(
            case_id="t1",
            decision_type=DecisionType.APPROVE,
            pms_snapshot={"stage": "in_stay"},
        ),
        _make_case(
            case_id="o1",
            decision_type=DecisionType.DEFER,
            pms_snapshot={"stage": "pre_arrival"},
        ),
    ]
    outcome = await analyzer.analyze(
        property_id="p1",
        scenario="access_code_release",
        cases=cases,
        target_action=DecisionType.APPROVE,
    )
    assert outcome.skipped_reason == "no_features_above_gate"
    assert outcome.rows_written == 0


# ---------------------------------------------------------------------------
# analyze() — happy path
# ---------------------------------------------------------------------------


async def test_analyze_persists_importances_with_clock_value() -> None:
    """Successful analysis writes one row per importance tuple."""
    fixed_clock = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    store = InMemoryFoundationStore()
    synth = _StubMlSynthesizer(
        MlSynthesisResult(
            feature_importance=(
                FeatureImportance("hours_before_checkin", 0.6),
                FeatureImportance("stage", 0.3),
            ),
        ),
    )
    analyzer = FoundationAnalyzer(
        store=store,
        synthesizer=synth,
        clock=lambda: fixed_clock,
    )

    cases = [
        _make_case(
            case_id="t1",
            decision_type=DecisionType.APPROVE,
            pms_snapshot={"stage": "in_stay", "adults": 2},
        ),
        _make_case(
            case_id="o1",
            decision_type=DecisionType.DEFER,
            pms_snapshot={"stage": "pre_arrival", "adults": 4},
        ),
    ]
    outcome = await analyzer.analyze(
        property_id="p1",
        scenario="access_code_release",
        cases=cases,
        target_action=DecisionType.APPROVE,
    )

    assert outcome.rows_written == 2
    assert outcome.skipped_reason is None
    assert outcome.target_count == 1
    assert outcome.other_count == 1

    rows = await store.get(
        property_id="p1", scenario="access_code_release",
    )
    assert [r.feature_name for r in rows] == [
        "hours_before_checkin",
        "stage",
    ]
    assert all(r.computed_at == fixed_clock for r in rows)
    # sample_count = target_count + other_count = 2.
    assert all(r.sample_count == 2 for r in rows)


async def test_analyze_returns_outcome_dataclass() -> None:
    fixed_clock = datetime(2026, 5, 7, tzinfo=timezone.utc)
    store = InMemoryFoundationStore()
    synth = _StubMlSynthesizer(
        MlSynthesisResult(
            feature_importance=(
                FeatureImportance("stage", 1.0),
            ),
        ),
    )
    analyzer = FoundationAnalyzer(
        store=store,
        synthesizer=synth,
        clock=lambda: fixed_clock,
    )
    outcome = await analyzer.analyze(
        property_id="p1",
        scenario="access_code_release",
        cases=[
            _make_case(
                case_id="t1", decision_type=DecisionType.APPROVE,
            ),
            _make_case(
                case_id="o1", decision_type=DecisionType.DEFER,
            ),
        ],
        target_action=DecisionType.APPROVE,
    )
    assert isinstance(outcome, FoundationAnalysisOutcome)
    assert outcome.property_id == "p1"
    assert outcome.scenario == "access_code_release"
