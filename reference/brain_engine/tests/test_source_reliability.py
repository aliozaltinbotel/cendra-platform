"""Tests for the §5 source-reliability hierarchy (FL-07).

The proactive foundation MD §5 ranks fact sources from most to
least reliable in a fixed nine-step ladder.  These tests pin the
ranking, the ``more_reliable`` tie-breaker, and the
``should_freeze_workflow`` threshold so any drift away from §5
surfaces immediately.
"""

from __future__ import annotations

import math

import pytest

from brain_engine.memory.contradiction_detector import (
    WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD,
    SourceReliability,
    more_reliable,
    reliability_rank,
    should_freeze_workflow,
)

# ── enum values ───────────────────────────────────────────── #


def test_source_reliability_has_exactly_nine_tiers() -> None:
    """Proactive §5 lists nine source-reliability tiers."""
    assert len(SourceReliability) == 9


@pytest.mark.parametrize(
    ("source", "expected_rank"),
    [
        (SourceReliability.PM_EXPLICIT_HARD_RULE, 1),
        (SourceReliability.CONFIRMED_SOP, 2),
        (SourceReliability.PMS_STRUCTURED_FIELD, 3),
        (SourceReliability.STRUCTURED_EVENT, 4),
        (SourceReliability.OTA_LISTING_STRUCTURED, 5),
        (SourceReliability.RECENT_PM_MESSAGE_CORRECTION, 6),
        (SourceReliability.GUEST_CLAIM, 7),
        (SourceReliability.OLD_FREE_TEXT_NOTE, 8),
        (SourceReliability.AI_GENERATED_INFERENCE, 9),
    ],
)
def test_reliability_rank_matches_section_five_ladder(
    source: SourceReliability,
    expected_rank: int,
) -> None:
    """Every tier's rank matches the verbatim §5 ladder."""
    assert reliability_rank(source) == expected_rank


def test_reliability_ranks_are_unique() -> None:
    """No two tiers share the same rank."""
    ranks = [reliability_rank(s) for s in SourceReliability]
    assert len(ranks) == len(set(ranks))


# ── more_reliable resolver ────────────────────────────────── #


def test_more_reliable_prefers_pm_hard_rule_over_pms() -> None:
    """PM explicit hard rule beats PMS structured field."""
    winner = more_reliable(
        SourceReliability.PM_EXPLICIT_HARD_RULE,
        SourceReliability.PMS_STRUCTURED_FIELD,
    )
    assert winner == SourceReliability.PM_EXPLICIT_HARD_RULE


def test_more_reliable_prefers_pms_over_guest_claim() -> None:
    """A PMS structured field beats a guest claim."""
    winner = more_reliable(
        SourceReliability.GUEST_CLAIM,
        SourceReliability.PMS_STRUCTURED_FIELD,
    )
    # The incumbent (PMS) is more reliable here.
    assert winner == SourceReliability.PMS_STRUCTURED_FIELD


def test_more_reliable_keeps_incumbent_on_tie() -> None:
    """Equal sources resolve to the incumbent — fewer cache misses."""
    same = SourceReliability.PMS_STRUCTURED_FIELD
    assert more_reliable(same, same) is same


def test_more_reliable_orders_full_ladder() -> None:
    """Sliding a candidate down the ladder against every other tier
    must always pick the higher tier."""
    candidate = SourceReliability.GUEST_CLAIM  # rank 7
    higher_tiers = (
        SourceReliability.PM_EXPLICIT_HARD_RULE,
        SourceReliability.CONFIRMED_SOP,
        SourceReliability.PMS_STRUCTURED_FIELD,
        SourceReliability.STRUCTURED_EVENT,
        SourceReliability.OTA_LISTING_STRUCTURED,
        SourceReliability.RECENT_PM_MESSAGE_CORRECTION,
    )
    lower_tiers = (
        SourceReliability.OLD_FREE_TEXT_NOTE,
        SourceReliability.AI_GENERATED_INFERENCE,
    )
    for higher in higher_tiers:
        assert more_reliable(candidate, higher) is higher
    for lower in lower_tiers:
        assert more_reliable(candidate, lower) is candidate


def test_ai_inference_is_least_reliable() -> None:
    """AI-generated inference must lose against every other tier."""
    inference = SourceReliability.AI_GENERATED_INFERENCE
    for other in SourceReliability:
        if other is inference:
            continue
        assert more_reliable(inference, other) is other


# ── workflow freeze threshold ─────────────────────────────── #


def test_workflow_freeze_threshold_matches_section_five() -> None:
    """The §5 ceiling is exactly 0.60."""
    assert WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD == 0.60


def test_should_freeze_workflow_below_threshold() -> None:
    """Confidence under 0.60 demands a freeze."""
    assert should_freeze_workflow(0.59) is True
    assert should_freeze_workflow(0.0) is True


def test_should_freeze_workflow_at_threshold_does_not_freeze() -> None:
    """Exactly 0.60 keeps the workflow live (strict-less-than)."""
    assert should_freeze_workflow(0.60) is False


def test_should_freeze_workflow_above_threshold() -> None:
    """High confidence keeps the workflow live."""
    assert should_freeze_workflow(0.85) is False
    assert should_freeze_workflow(1.0) is False


def test_should_freeze_workflow_treats_nan_as_freeze() -> None:
    """NaN confidence is unparseable — freeze defensively."""
    assert should_freeze_workflow(math.nan) is True
