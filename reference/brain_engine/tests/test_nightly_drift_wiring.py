"""Sprint 6 W8 wiring tests — drift detector in nightly_consolidator.

Pins:

* Skip path — when either ``case_store`` or
  ``foundation_update_store`` is missing, the step is a tagged
  no-op and emits no backlog rows.
* End-to-end happy path — overrides crossing the threshold
  produce candidates that land in the store; counts surface on
  the stats dict.
* Failure isolation — a store error on one upsert does not break
  the rest of the step or the wider nightly cycle.
"""

from __future__ import annotations

import pytest

from brain_engine.continual_learning.nightly_consolidator import (
    NightlyConsolidator,
)
from brain_engine.patterns.foundation_update import (
    FoundationUpdateCandidate,
    InMemoryFoundationUpdateStore,
    UpdateSeverity,
)
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.store import InMemoryDecisionCaseStore

# ── fixtures ──────────────────────────────────────────────── #


def _override_case(
    *,
    case_id: str,
    foundation_scenario_id: str,
    property_id: str = "prop-1",
) -> DecisionCase:
    """Case marked as PM-overridden — counts toward drift threshold."""
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.IN_STAY,
        scenario=Scenario.EARLY_CHECKIN,
        property_id=property_id,
        owner_id="owner-1",
        decision=DecisionAction(
            action_type=DecisionType.APPROVE,
            params={},
        ),
        outcome=CaseOutcome(
            human_overrode=True,
            resolution_type=ResolutionType.PM_DENIED,
        ),
        foundation_scenario_id=foundation_scenario_id,
    )


def _make_consolidator(
    *,
    case_store: InMemoryDecisionCaseStore | None,
    foundation_update_store: InMemoryFoundationUpdateStore | None,
) -> NightlyConsolidator:
    """Build a minimal :class:`NightlyConsolidator` with stub collaborators.

    The other dependencies (memory, skills, recorder, grader) are
    placeholders — the step under test only uses ``case_store`` and
    ``foundation_update_store`` so the rest can stay ``None`` /
    sentinel objects.
    """
    return NightlyConsolidator(
        memory=object(),
        skills=object(),
        recorder=object(),
        grader=object(),
        case_store=case_store,
        foundation_update_store=foundation_update_store,
    )


# ── skip paths ────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_skips_when_case_store_missing() -> None:
    """No case_store ⇒ step is a tagged no-op."""
    store = InMemoryFoundationUpdateStore()
    consolidator = _make_consolidator(
        case_store=None,
        foundation_update_store=store,
    )
    result = await consolidator._step9_detect_foundation_drift()
    assert result == {
        "skipped": True,
        "reason": "case_store or foundation_update_store not configured",
    }
    assert await store.list_pending() == ()


@pytest.mark.asyncio
async def test_skips_when_update_store_missing() -> None:
    """No foundation_update_store ⇒ step is a tagged no-op."""
    case_store = InMemoryDecisionCaseStore()
    consolidator = _make_consolidator(
        case_store=case_store,
        foundation_update_store=None,
    )
    result = await consolidator._step9_detect_foundation_drift()
    assert result["skipped"] is True


# ── happy path ────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_threshold_crossed_emits_candidate() -> None:
    """Three overrides on the same scenario produce one backlog row."""
    case_store = InMemoryDecisionCaseStore()
    for i in range(3):
        await case_store.store(
            _override_case(
                case_id=f"override-{i}",
                foundation_scenario_id="s4_209_gas",
            ),
        )
    update_store = InMemoryFoundationUpdateStore()
    consolidator = _make_consolidator(
        case_store=case_store,
        foundation_update_store=update_store,
    )
    stats = await consolidator._step9_detect_foundation_drift()
    assert stats["cases_scanned"] == 3
    assert stats["candidates_emitted"] == 1
    assert stats["errors"] == 0
    pending = await update_store.list_pending()
    assert len(pending) == 1
    candidate = pending[0]
    assert candidate.foundation_scenario_id == "s4_209_gas"
    assert candidate.override_count == 3
    assert candidate.severity == UpdateSeverity.LOW


@pytest.mark.asyncio
async def test_below_threshold_emits_nothing() -> None:
    """Two overrides do not cross the default threshold."""
    case_store = InMemoryDecisionCaseStore()
    for i in range(2):
        await case_store.store(
            _override_case(
                case_id=f"override-{i}",
                foundation_scenario_id="s4_209_gas",
            ),
        )
    update_store = InMemoryFoundationUpdateStore()
    consolidator = _make_consolidator(
        case_store=case_store,
        foundation_update_store=update_store,
    )
    stats = await consolidator._step9_detect_foundation_drift()
    assert stats["cases_scanned"] == 2
    assert stats["candidates_emitted"] == 0
    assert await update_store.list_pending() == ()


@pytest.mark.asyncio
async def test_multi_scenario_emits_separate_candidates() -> None:
    """Different scenarios each produce their own backlog row."""
    case_store = InMemoryDecisionCaseStore()
    for i in range(3):
        await case_store.store(
            _override_case(
                case_id=f"gas-{i}",
                foundation_scenario_id="s4_209_gas",
            ),
        )
    for i in range(3):
        await case_store.store(
            _override_case(
                case_id=f"checkin-{i}",
                foundation_scenario_id="s1_16_early_checkin",
            ),
        )
    update_store = InMemoryFoundationUpdateStore()
    consolidator = _make_consolidator(
        case_store=case_store,
        foundation_update_store=update_store,
    )
    stats = await consolidator._step9_detect_foundation_drift()
    assert stats["candidates_emitted"] == 2
    scenario_ids = {
        candidate.foundation_scenario_id
        for candidate in await update_store.list_pending()
    }
    assert scenario_ids == {"s4_209_gas", "s1_16_early_checkin"}


# ── failure isolation ────────────────────────────────────── #


class _BadStore(InMemoryFoundationUpdateStore):
    """Store that fails the first upsert and succeeds on the rest."""

    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    async def upsert(
        self,
        candidate: FoundationUpdateCandidate,
    ) -> None:
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated upsert failure")
        await super().upsert(candidate)


@pytest.mark.asyncio
async def test_upsert_failure_does_not_break_the_step() -> None:
    """A failed upsert is logged + counted; other candidates still land."""
    case_store = InMemoryDecisionCaseStore()
    for i in range(3):
        await case_store.store(
            _override_case(
                case_id=f"gas-{i}",
                foundation_scenario_id="s4_209_gas",
            ),
        )
    for i in range(3):
        await case_store.store(
            _override_case(
                case_id=f"checkin-{i}",
                foundation_scenario_id="s1_16_early_checkin",
            ),
        )
    bad_store = _BadStore()
    consolidator = _make_consolidator(
        case_store=case_store,
        foundation_update_store=bad_store,
    )
    stats = await consolidator._step9_detect_foundation_drift()
    assert stats["errors"] == 1
    # One of the two candidates failed, the other still landed.
    assert stats["candidates_emitted"] == 1
    pending = await bad_store.list_pending()
    assert len(pending) == 1
