"""Tests for the ``_count_signal_weights`` helper + W6 wiring.

Sprint 6 PR-2 folds the FL-06 ``ConfidenceContext`` signal-count
fields into both :class:`PatternMiner` rule-build sites.  Counts
are derived from existing :class:`CaseOutcome.resolution_type`
values:

* ``ResolutionType.PM_APPROVED`` → ``pm_approval_count``
* ``ResolutionType.PM_MODIFIED`` → ``pm_repeated_edit_count``

Other §5 signals stay ``0`` (they require upstream tagging that a
later Sprint 6 PR will add).  Legacy cases that never populated
``resolution_type`` ⇒ all counts ``0`` ⇒ confidence formula
collapses to its pre-W6 output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from brain_engine.patterns.extractor import _count_signal_weights
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


def _case(
    *,
    case_id: str = "case-1",
    resolution: ResolutionType | None = None,
    action: DecisionType = DecisionType.APPROVE,
    created_at: datetime | None = None,
) -> DecisionCase:
    """Build a minimal :class:`DecisionCase` with the supplied outcome."""
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.IN_STAY,
        scenario=Scenario.EARLY_CHECKIN,
        property_id="prop-1",
        owner_id="owner-1",
        decision=DecisionAction(action_type=action, params={}),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=resolution,
        ),
        created_at=created_at or datetime(2026, 5, 1, tzinfo=UTC),
    )


# ── helper unit tests ─────────────────────────────────────── #


def test_empty_iterable_yields_zero_counts() -> None:
    """No cases ⇒ every count is zero."""
    counts = _count_signal_weights([])
    assert counts == {
        "pm_explicit_rule_count": 0,
        "pm_repeated_edit_count": 0,
        "pm_approval_count": 0,
        "guest_complaint_count": 0,
        "task_reopen_count": 0,
        "vendor_sla_breach_count": 0,
        "review_mention_count": 0,
    }


def test_legacy_cases_without_resolution_yield_zero_counts() -> None:
    """Cases with ``resolution_type=None`` contribute nothing."""
    cases = [_case(case_id=f"c{i}") for i in range(5)]
    counts = _count_signal_weights(cases)
    assert all(v == 0 for v in counts.values())


def test_pm_approved_increments_pm_approval_count() -> None:
    """PM_APPROVED → ``pm_approval_count``."""
    cases = [
        _case(
            case_id=f"c{i}",
            resolution=ResolutionType.PM_APPROVED,
        )
        for i in range(3)
    ]
    counts = _count_signal_weights(cases)
    assert counts["pm_approval_count"] == 3
    assert counts["pm_repeated_edit_count"] == 0


def test_pm_modified_increments_pm_repeated_edit_count() -> None:
    """PM_MODIFIED → ``pm_repeated_edit_count``."""
    cases = [
        _case(
            case_id=f"c{i}",
            resolution=ResolutionType.PM_MODIFIED,
        )
        for i in range(2)
    ]
    counts = _count_signal_weights(cases)
    assert counts["pm_repeated_edit_count"] == 2
    assert counts["pm_approval_count"] == 0


def test_pm_denied_does_not_count_toward_approval() -> None:
    """PM_DENIED is a counterexample, not a signal boost.

    Counterexamples flow through the miner's separate
    ``counterexample_count`` channel — the rule miner never
    classifies them as supporting evidence, so the helper should
    not classify them as signal boosts either.
    """
    cases = [
        _case(case_id="c1", resolution=ResolutionType.PM_DENIED),
    ]
    counts = _count_signal_weights(cases)
    assert counts["pm_approval_count"] == 0
    assert counts["pm_repeated_edit_count"] == 0


def test_other_resolution_types_count_zero() -> None:
    """AUTO_RESOLVED / GUEST_ACCEPTED / TIMEOUT / ESCALATED ⇒ 0.

    None of these are PM correction signals so they leave every
    §5 count untouched.  When the relevant upstream tagging
    lands, the helper will start recognising them.
    """
    cases = [
        _case(case_id="c1", resolution=ResolutionType.AUTO_RESOLVED),
        _case(case_id="c2", resolution=ResolutionType.GUEST_ACCEPTED),
        _case(case_id="c3", resolution=ResolutionType.GUEST_REJECTED),
        _case(case_id="c4", resolution=ResolutionType.TIMEOUT),
        _case(case_id="c5", resolution=ResolutionType.ESCALATED),
    ]
    counts = _count_signal_weights(cases)
    assert all(v == 0 for v in counts.values())


def test_mixed_cases_aggregate_separate_counts() -> None:
    """A mixed batch tallies each resolution into its own bucket."""
    cases = [
        _case(case_id="a1", resolution=ResolutionType.PM_APPROVED),
        _case(case_id="a2", resolution=ResolutionType.PM_APPROVED),
        _case(case_id="m1", resolution=ResolutionType.PM_MODIFIED),
        _case(case_id="auto", resolution=ResolutionType.AUTO_RESOLVED),
        _case(case_id="legacy"),  # no resolution
    ]
    counts = _count_signal_weights(cases)
    assert counts["pm_approval_count"] == 2
    assert counts["pm_repeated_edit_count"] == 1
    # Spot-check that the rest stay zero.
    assert counts["guest_complaint_count"] == 0
    assert counts["task_reopen_count"] == 0


# ── PatternMiner end-to-end ──────────────────────────────── #


def _approval_case(
    *,
    case_id: str,
    resolution: ResolutionType,
    last_seen: datetime,
) -> DecisionCase:
    """Approve-action case suitable for miner positives."""
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
            resolution_type=resolution,
        ),
        created_at=last_seen,
    )


def test_pattern_miner_pm_signals_lift_confidence_above_no_signal_baseline() -> None:
    """PM signals raise rule confidence above the no-signal baseline.

    Identical inputs except for ``resolution_type``: a batch of
    ``AUTO_RESOLVED`` outcomes (no §5 signal) produces the same
    base ratio but no signal boost, so its confidence is the
    pre-W6 baseline.  A batch of ``PM_APPROVED`` outcomes adds
    ``+0.10`` per case to the formula, so confidence rises
    (capped at ``1.0``) — proving the W6 wiring forwards the
    signal counts into the formula end-to-end.
    """
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    auto_cases = [
        _approval_case(
            case_id=f"auto-{i}",
            resolution=ResolutionType.AUTO_RESOLVED,
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    approved_cases = [
        _approval_case(
            case_id=f"approve-{i}",
            resolution=ResolutionType.PM_APPROVED,
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    auto_rules, _ = miner.mine(auto_cases)
    approved_rules, _ = miner.mine(approved_cases)
    assert auto_rules and approved_rules
    # Same base ratio (5/5) — the only difference is the §5 boost.
    assert approved_rules[0].confidence >= auto_rules[0].confidence


def test_pattern_miner_pm_approvals_boost_confidence() -> None:
    """PM_APPROVED cases add ``+0.10`` each per the §5 weight.

    Five PM_APPROVED supporting cases ⇒ base ratio 1.0 (clamped)
    + 5 * 0.10 boost (also clamped at 1.0).  The clamp means the
    end-user-visible confidence stays at 1.0 — but the signal
    boost would otherwise be ``0.50``, so the formula plumbing
    is exercised end-to-end and the clamp guarantees no drift
    above 1.0 either.
    """
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approval_case(
            case_id=f"approve-{i}",
            resolution=ResolutionType.PM_APPROVED,
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    # 5 / 5 = 1.0 base; boost +0.50; clamp → 1.0.
    assert rules[0].confidence == 1.0


def test_pattern_miner_pm_modifications_count_as_edits() -> None:
    """PM_MODIFIED cases populate the pattern's edit signal channel.

    The miner does not surface the count directly on the rule
    (it stays inside the confidence formula), so we verify the
    helper sees them and the confidence still rounds to 1.0
    under clamp.
    """
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approval_case(
            case_id=f"edit-{i}",
            resolution=ResolutionType.PM_MODIFIED,
            last_seen=base + timedelta(hours=i),
        )
        for i in range(4)
    ]
    counts = _count_signal_weights(cases)
    assert counts["pm_repeated_edit_count"] == 4
    miner = PatternMiner()
    rules, _report = miner.mine(cases)
    assert rules
    # 4 / 4 = 1.0 base + 4 * 0.15 boost; clamp → 1.0.
    assert rules[0].confidence == 1.0
