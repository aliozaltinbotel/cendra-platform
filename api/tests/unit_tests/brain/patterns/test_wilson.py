"""Tests for the Wilson score interval lower bound.

The reference repo has no dedicated test file for ``patterns/wilson.py``
(it is exercised indirectly through the promotion-gate tests); these tests
pin the documented examples and the error contract so the module travels
with its own coverage, per the porting rules.
"""

import math

import pytest

from core.brain.patterns.wilson import (
    AUTONOMY_WILSON_L2,
    AUTONOMY_WILSON_L3,
    AUTONOMY_WILSON_L4,
    AUTONOMY_WILSON_L4_AUTO,
    PROMOTION_MIN_SUPPORT_AUTO,
    PROMOTION_WILSON_AUTO,
    Z_90,
    Z_95,
    Z_99,
    wilson_lower_bound,
)


class TestDocumentedExamples:
    # The reference docstring claimed 0.493 / 0.7128 / 0.7748; those values do
    # not satisfy the Wilson formula at z=1.96 — fixed forward here (the exact
    # values below were verified against the formula by hand).
    def test_8_of_10(self):
        assert round(wilson_lower_bound(8, 10), 4) == 0.4902

    def test_80_of_100(self):
        assert round(wilson_lower_bound(80, 100), 4) == 0.7112

    def test_800_of_1000(self):
        assert round(wilson_lower_bound(800, 1000), 4) == 0.7741

    def test_lower_bound_grows_with_sample_size_at_same_rate(self):
        # 4/5 and 800/1000 share the same point estimate; the small sample
        # must produce a (much) weaker lower bound.
        assert wilson_lower_bound(4, 5) < wilson_lower_bound(800, 1000)


class TestEdgeCases:
    def test_zero_trials_returns_zero(self):
        assert wilson_lower_bound(0, 0) == 0.0

    def test_zero_successes(self):
        assert wilson_lower_bound(0, 50) == 0.0

    def test_all_successes_stays_below_one(self):
        result = wilson_lower_bound(50, 50)
        assert 0.0 < result < 1.0

    def test_result_always_in_unit_interval(self):
        for successes, trials in [(0, 1), (1, 1), (1, 2), (999, 1000), (1, 10_000)]:
            result = wilson_lower_bound(successes, trials)
            assert 0.0 <= result <= 1.0
            assert math.isfinite(result)

    def test_tighter_confidence_gives_lower_bound(self):
        assert (
            wilson_lower_bound(80, 100, z=Z_99)
            < wilson_lower_bound(80, 100, z=Z_95)
            < wilson_lower_bound(80, 100, z=Z_90)
        )


class TestValidation:
    def test_negative_trials_rejected(self):
        with pytest.raises(ValueError, match="trials must be non-negative"):
            wilson_lower_bound(0, -1)

    def test_negative_successes_rejected(self):
        with pytest.raises(ValueError, match="successes must be non-negative"):
            wilson_lower_bound(-1, 10)

    def test_successes_exceeding_trials_rejected(self):
        with pytest.raises(ValueError, match="successes cannot exceed trials"):
            wilson_lower_bound(11, 10)

    def test_non_positive_z_rejected(self):
        with pytest.raises(ValueError, match="z must be positive"):
            wilson_lower_bound(5, 10, z=0.0)


class TestThresholdConstants:
    def test_autonomy_thresholds_are_ordered(self):
        assert AUTONOMY_WILSON_L2 < AUTONOMY_WILSON_L3 < AUTONOMY_WILSON_L4 < AUTONOMY_WILSON_L4_AUTO

    def test_promotion_thresholds(self):
        assert PROMOTION_WILSON_AUTO == 0.75
        assert PROMOTION_MIN_SUPPORT_AUTO == 20
