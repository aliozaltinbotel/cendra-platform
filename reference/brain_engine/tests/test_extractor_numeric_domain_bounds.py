"""Tests for the Mümin round-4 #5b domain-bounds + support guard fix.

Background
----------
Round-4 verdict matrix
(``project_mumi_feedback_round4.md`` in the memory store) closed
#5b with status 🟡 partial: PR 204 brought the
``hours_before_checkin`` threshold down from ``-1185.65`` to
``-333.73``, but
:meth:`brain_engine.patterns.extractor.PatternExtractor._infer_numeric_condition`
still produced a ``defer`` rule with threshold ``-4197.65`` on a
6-supporting-case positive pool — clearly degenerate.

The fix adds two guards to ``_infer_numeric_condition``:

1. **Domain bounds** — features registered in
   :data:`brain_engine.patterns.extractor._NUMERIC_DOMAIN_BOUNDS`
   reject thresholds that fall outside the bound.
2. **One-sided support floor** — when ``neg_values`` is empty, the
   positive pool must satisfy
   :data:`_MIN_NUMERIC_ONE_SIDED_SUPPORT` (= the existing
   ``_MIN_SUPPORT_DEFAULT`` floor).

Unknown features keep the prior behaviour.  These tests pin both
the rejected-degenerate cases and the no-regression cases.
"""

from __future__ import annotations

from brain_engine.patterns.extractor import (
    PatternExtractor,
    _MIN_NUMERIC_ONE_SIDED_SUPPORT,
    _NUMERIC_DOMAIN_BOUNDS,
    _threshold_within_domain,
)


def _make_extractor() -> PatternExtractor:
    """Build an extractor whose store is unused by these tests."""
    return PatternExtractor.__new__(PatternExtractor)  # bypass init


# ─── domain-bound helper ──────────────────────────────────────── #


def test_unknown_feature_is_unbounded() -> None:
    """Features without a registered bound admit any finite value."""
    assert _threshold_within_domain("unknown_feature", 9_999.0)
    assert _threshold_within_domain("unknown_feature", -9_999.0)


def test_known_feature_within_bound_admitted() -> None:
    """A threshold inside the registered bound is admitted."""
    assert _threshold_within_domain("hours_before_checkin", 24.0)
    assert _threshold_within_domain("hours_before_checkin", -24.0)


def test_known_feature_outside_bound_rejected() -> None:
    """Mümin's actual degenerate thresholds are rejected."""
    # Original round-4 complaint.
    assert not _threshold_within_domain(
        "hours_before_checkin", -1185.65,
    )
    # PR 204 still left this one through.
    assert not _threshold_within_domain(
        "hours_before_checkin", -4197.65,
    )


def test_non_finite_threshold_rejected() -> None:
    """NaN / ±inf are unconditionally rejected."""
    assert not _threshold_within_domain(
        "hours_before_checkin", float("nan"),
    )
    assert not _threshold_within_domain(
        "hours_before_checkin", float("inf"),
    )
    assert not _threshold_within_domain(
        "hours_before_checkin", float("-inf"),
    )


def test_registry_contains_mumin_round4_features() -> None:
    """Spot-check that the registry covers Mümin's reported axes."""
    assert "hours_before_checkin" in _NUMERIC_DOMAIN_BOUNDS
    assert "lead_time_hours" in _NUMERIC_DOMAIN_BOUNDS


# ─── _infer_numeric_condition: one-sided support guard ──────── #


def test_one_sided_below_support_floor_returns_none() -> None:
    """Empty neg pool + few positives ⇒ no rule emitted."""
    extractor = _make_extractor()
    pos = [10.0, 12.0]  # 2 < floor of 3
    assert extractor._infer_numeric_condition(
        "lead_time_hours", pos, [],
    ) is None


def test_one_sided_at_support_floor_emits_rule() -> None:
    """At-or-above the floor the rule is emitted."""
    extractor = _make_extractor()
    pos = [10.0, 12.0, 14.0]  # == floor
    result = extractor._infer_numeric_condition(
        "lead_time_hours", pos, [],
    )
    assert result == {"operator": "gte", "value": 12.0}


def test_support_floor_matches_module_constant() -> None:
    """The guard uses the documented module constant."""
    assert _MIN_NUMERIC_ONE_SIDED_SUPPORT >= 3


# ─── _infer_numeric_condition: domain bounds ────────────────── #


def test_mumin_round4_degenerate_one_sided_rejected() -> None:
    """The exact Mümin #5b case (one-sided, threshold ≪ bound)."""
    extractor = _make_extractor()
    # 6 supporting cases at -4197.65 — round-4 closing log values.
    pos = [-4197.65] * 6
    assert extractor._infer_numeric_condition(
        "hours_before_checkin", pos, [],
    ) is None


def test_two_sided_threshold_outside_bound_rejected() -> None:
    """Two-sided branch also rejects out-of-bound thresholds."""
    extractor = _make_extractor()
    # Positive median below the bound, all values legal but
    # producing an out-of-bound threshold post-median.
    pos = [-1500.0, -1200.0, -1100.0]  # median = -1200, < -720
    neg = [0.0, 5.0]
    assert extractor._infer_numeric_condition(
        "hours_before_checkin", pos, neg,
    ) is None


def test_two_sided_threshold_within_bound_emitted() -> None:
    """Sane two-sided thresholds keep working unchanged."""
    extractor = _make_extractor()
    pos = [48.0, 72.0, 96.0]  # median 72
    neg = [4.0, 8.0]  # median 6
    result = extractor._infer_numeric_condition(
        "hours_before_checkin", pos, neg,
    )
    assert result == {"operator": "gte", "value": 72.0}


def test_two_sided_lte_branch_within_bound_emitted() -> None:
    """The ``lte`` branch obeys the same bound check."""
    extractor = _make_extractor()
    pos = [4.0, 8.0, 12.0]  # median 8
    neg = [48.0, 72.0]  # median 60
    result = extractor._infer_numeric_condition(
        "hours_before_checkin", pos, neg,
    )
    assert result == {"operator": "lte", "value": 8.0}


def test_unknown_feature_keeps_prior_behaviour() -> None:
    """Features without a registered bound take the legacy path."""
    extractor = _make_extractor()
    # An unknown feature with extreme values still emits a rule —
    # downstream Wilson + conformal gates filter noise.
    pos = [9_999.0, 10_001.0, 10_000.0]
    assert extractor._infer_numeric_condition(
        "unknown_metric", pos, [],
    ) == {"operator": "gte", "value": 10_000.0}


def test_lead_time_negative_threshold_rejected() -> None:
    """``lead_time_hours`` cannot be negative by domain rule."""
    extractor = _make_extractor()
    pos = [-100.0, -200.0, -150.0]  # median -150
    neg = [10.0, 5.0]
    assert extractor._infer_numeric_condition(
        "lead_time_hours", pos, neg,
    ) is None


def test_occupancy_threshold_clamped_to_unit_interval() -> None:
    """``occupancy_7d`` bound is ``[0, 1]``."""
    extractor = _make_extractor()
    # Threshold 1.5 exceeds the bound — should reject.
    pos = [1.4, 1.5, 1.6]
    neg = [0.1, 0.2]
    assert extractor._infer_numeric_condition(
        "occupancy_7d", pos, neg,
    ) is None


def test_equal_medians_returns_none_regardless_of_bound() -> None:
    """Pre-existing not-discriminating branch keeps its semantics."""
    extractor = _make_extractor()
    pos = [10.0, 12.0, 14.0]
    neg = [10.0, 12.0, 14.0]
    assert extractor._infer_numeric_condition(
        "lead_time_hours", pos, neg,
    ) is None


def test_round4_minor_threshold_still_admitted() -> None:
    """The round-4 ``-333.73`` value remains admissible.

    PR 204 brought the threshold down from ``-1185.65`` to
    ``-333.73``; the round-4 log calls the latter "materially more
    sensible".  The new bound (``-720``) keeps ``-333.73`` inside
    its admissible range, so the fix does not over-correct.
    """
    extractor = _make_extractor()
    # 7 supporting cases clustered around -333.73, single-sided.
    pos = [
        -300.0,
        -320.0,
        -333.73,
        -333.73,
        -333.73,
        -350.0,
        -370.0,
    ]
    result = extractor._infer_numeric_condition(
        "hours_before_checkin", pos, [],
    )
    assert result is not None
    assert result["operator"] == "gte"
    assert result["value"] == -333.73
