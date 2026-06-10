"""Five-tier autonomy ladder invariants."""

from __future__ import annotations

import pytest

from core.brain.certificates.tier import (
    TIER_RANK,
    AutonomyTier,
    tier_rank,
)


def test_five_tiers_are_defined() -> None:
    """Exactly five tiers ship."""
    assert len(AutonomyTier) == 5


def test_rank_is_monotonic_observer_to_operator() -> None:
    """Higher rank = more autonomy, OBSERVER < OPERATOR."""
    expected_order = [
        AutonomyTier.OBSERVER,
        AutonomyTier.APPROVER,
        AutonomyTier.CONSULTANT,
        AutonomyTier.COLLABORATOR,
        AutonomyTier.OPERATOR,
    ]
    ranks = [tier_rank(t) for t in expected_order]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1
    assert ranks[-1] == 5


@pytest.mark.parametrize(
    "tier",
    list(AutonomyTier),
    ids=lambda t: t.value,
)
def test_every_tier_has_rank(tier: AutonomyTier) -> None:
    """Every enum member is in the rank mapping."""
    assert tier in TIER_RANK
    assert TIER_RANK[tier] == tier_rank(tier)
