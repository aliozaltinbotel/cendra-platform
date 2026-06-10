"""Tests for NEVER_AUTO_SCENARIOS blacklist coverage.

Pins the 2026-05-18 expansion of the validator's safety
blacklist.  The expansion added the six new Foundation
coverage scenarios that carry enough downside (fraud, safety,
financial / legal) that an auto-fired rule is the wrong
default — they belong on the manual-review path regardless of
how high the miner's Wilson lower bound climbs.
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.models import Scenario
from brain_engine.patterns.validator import NEVER_AUTO_SCENARIOS


@pytest.mark.parametrize(
    "scenario",
    [
        # Pre-existing entries — pin so a future refactor never
        # accidentally drops them.
        Scenario.DAMAGE_REPORT,
        Scenario.CANCELLATION_REQUEST,
        # 2026-05-18 Foundation coverage expansion additions —
        # each closes a documented high-downside surface.
        Scenario.PROXY_BOOKING_RISK,
        Scenario.SAFETY_EMERGENCY,
        Scenario.SAFETY_SECURITY_CONCERN,
        Scenario.CHARGEBACK_DISPUTE,
        Scenario.PRIVACY_CONCERN,
        Scenario.OFF_PLATFORM_CONTACT,
    ],
)
def test_blacklist_includes_safety_critical_scenarios(
    scenario: Scenario,
) -> None:
    """Each high-downside scenario must be in NEVER_AUTO_SCENARIOS."""
    assert scenario in NEVER_AUTO_SCENARIOS


def test_blacklist_size_matches_expectation() -> None:
    """Total count guards against accidental membership churn.

    A future PR that adds another high-downside enum should
    update this count deliberately; a silent drop is the
    failure mode this test is designed to catch.
    """
    assert len(NEVER_AUTO_SCENARIOS) == 8


def test_learnable_scenarios_stay_out_of_blacklist() -> None:
    """Common learnable scenarios must NOT be in the blacklist.

    Sanity check that we did not over-expand the blacklist —
    these are the scenarios the pattern miner actively learns
    from (early check-in, access codes, etc.).  Any of them in
    NEVER_AUTO_SCENARIOS would silently disable a meaningful
    chunk of learned rules.
    """
    for scenario in (
        Scenario.EARLY_CHECKIN,
        Scenario.LATE_CHECKOUT,
        Scenario.ACCESS_CODE_RELEASE,
        Scenario.MAINTENANCE_REQUEST,
        Scenario.AMENITY_EXCEPTION,
        Scenario.GUEST_COUNT_MISMATCH,
        Scenario.PARKING_REQUEST,
        Scenario.PET_POLICY_EXCEPTION,
        Scenario.CLEANER_DISPATCH,
        Scenario.BOOKING_EXTENSION,
    ):
        assert scenario not in NEVER_AUTO_SCENARIOS
