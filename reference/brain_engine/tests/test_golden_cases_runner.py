"""Unit tests for :mod:`brain_engine.evaluation.golden_cases_runner`.

Covers the contract that step 8 of the nightly cycle relies on:
- empty case set → zero-row report, no judge calls
- mixed verdicts → pm_match_rate / hallucination_rate counted correctly
- judge raising → failed_cases incremented, run does not crash
- env flag toggles ``golden_cases_enabled`` deterministically
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from brain_engine.evaluation.golden_cases_runner import (
    GoldenCasesReport,
    GoldenCasesRunner,
    InMemoryEvaluationResultStore,
    golden_cases_enabled,
)
from brain_engine.evaluation.protocol import EvalResult
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionCase,
    DecisionType,
    Scenario,
)


def _make_case(
    *,
    case_id: str = "case-1",
    minutes_ago: int = 1,
    message: str = "I'd like late checkout",
    response: str = "Sure, 14:00 works",
) -> DecisionCase:
    """Construct a minimal DecisionCase rooted in the last 24h."""
    return DecisionCase(
        stage=BookingStage.IN_STAY,
        scenario=Scenario.LATE_CHECKOUT,
        property_id="prop-1",
        owner_id="owner-1",
        decision=DecisionAction(action_type=DecisionType.APPROVE),
        case_id=case_id,
        message_text=message,
        response_text=response,
        executed_actions=("send_message",),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


class _FakeCaseStore:
    """Minimal stand-in for :class:`DecisionCaseStore`.

    Honours only the ``search`` method that
    :meth:`GoldenCasesRunner._sample_recent` actually calls — keeps
    tests independent of the Postgres / in-memory production stores.
    """

    def __init__(self, cases: list[DecisionCase]) -> None:
        self._cases = cases

    async def search(self, **_: object) -> list[DecisionCase]:
        return list(self._cases)


@pytest.mark.asyncio
async def test_run_daily_returns_zero_report_when_no_cases() -> None:
    runner = GoldenCasesRunner(
        case_store=_FakeCaseStore([]),
        judge=AsyncMock(),
        result_store=InMemoryEvaluationResultStore(),
    )

    report = await runner.run_daily()

    assert report == GoldenCasesReport(0, 0.0, 0.0, 0.0, 0, 0.0)


@pytest.mark.asyncio
async def test_run_daily_aggregates_pm_match_and_hallucination() -> None:
    judge = AsyncMock()
    judge.evaluate.side_effect = [
        EvalResult(
            score=0.9,
            value="Y",
            reasoning="solid",
            metadata={"would_pm_agree": True, "hallucination_detected": False},
        ),
        EvalResult(
            score=0.4,
            value="N",
            reasoning="off-policy",
            metadata={"would_pm_agree": False, "hallucination_detected": True},
        ),
        EvalResult(
            score=0.7,
            value="Y",
            reasoning="ok",
            metadata={"would_pm_agree": True, "hallucination_detected": False},
        ),
    ]
    store = InMemoryEvaluationResultStore()
    cases = [_make_case(case_id=f"case-{i}") for i in range(3)]

    runner = GoldenCasesRunner(
        case_store=_FakeCaseStore(cases),
        judge=judge,
        result_store=store,
    )

    report = await runner.run_daily()

    assert report.sample_size == 3
    assert report.pm_match_rate == pytest.approx(2 / 3)
    assert report.hallucination_rate == pytest.approx(1 / 3)
    assert report.avg_score == pytest.approx((0.9 + 0.4 + 0.7) / 3)
    assert report.failed_cases == 0
    assert len(store.runs) == 1
    assert len(store.verdicts) == 3
    assert {v[0] for v in store.verdicts} == {"case-0", "case-1", "case-2"}


@pytest.mark.asyncio
async def test_run_daily_counts_judge_failures_without_crashing() -> None:
    judge = AsyncMock()
    judge.evaluate.side_effect = [
        Exception("Azure OpenAI 429"),
        EvalResult(
            score=0.8,
            value="Y",
            metadata={"would_pm_agree": True},
        ),
    ]
    runner = GoldenCasesRunner(
        case_store=_FakeCaseStore(
            [_make_case(case_id="bad"), _make_case(case_id="good")],
        ),
        judge=judge,
        result_store=InMemoryEvaluationResultStore(),
    )

    report = await runner.run_daily()

    assert report.sample_size == 2
    assert report.failed_cases == 1
    assert report.pm_match_rate == pytest.approx(1 / 2)


@pytest.mark.asyncio
async def test_run_daily_filters_out_cases_older_than_24h() -> None:
    fresh = _make_case(case_id="fresh", minutes_ago=30)
    stale = _make_case(case_id="stale", minutes_ago=60 * 25)
    judge = AsyncMock()
    judge.evaluate.return_value = EvalResult(score=0.5, value="N")

    runner = GoldenCasesRunner(
        case_store=_FakeCaseStore([fresh, stale]),
        judge=judge,
        result_store=InMemoryEvaluationResultStore(),
    )

    report = await runner.run_daily()

    assert report.sample_size == 1
    assert judge.evaluate.await_count == 1


def test_golden_cases_enabled_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAIN_GOLDEN_CASES_ENABLED", raising=False)
    assert golden_cases_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_golden_cases_enabled_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("BRAIN_GOLDEN_CASES_ENABLED", value)
    assert golden_cases_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_golden_cases_enabled_falsy_values(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("BRAIN_GOLDEN_CASES_ENABLED", value)
    assert golden_cases_enabled() is False
