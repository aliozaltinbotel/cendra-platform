"""Tests for the multi-factor confidence formula (P6).

ali.md §10 specifies::

    confidence = (
        success_count / total_matching_cases
        - counterexample_penalty
        - staleness_penalty
        - low_support_penalty
        - conflict_penalty
    ) * hidden_variable_penalty

These tests pin the *contracts* the formula offers callers:

1. **Backward compatibility** — an empty
   :class:`ConfidenceContext` collapses the formula to
   ``success / total``.  This guarantees the pre-P6 confidence
   surfaces unchanged when callers do not yet pass signals.
2. **Each penalty in isolation** — wiggle one signal at a time
   and verify the expected drag.  Combined runs are covered
   too so the additive interaction is locked.
3. **Bounds + edge cases** — clamp to ``[0.0, 1.0]``, zero
   total, full support.
4. **Heuristic registry** — register / get / remove /
   snapshot deterministic ordering.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from brain_engine.patterns.confidence import (
    CONTRADICTION_UNRESOLVED_CAP,
    DEFAULT_SIGNAL_WEIGHTS,
    ConfidenceContext,
    HeuristicRegistry,
    PenaltyConfig,
    SignalWeights,
    StatisticalHeuristic,
    compute_confidence,
)

# ---------------------------------------------------------------------------
# Backward-compatibility — empty ctx mirrors success / total
# ---------------------------------------------------------------------------


def test_empty_context_returns_base_ratio() -> None:
    assert compute_confidence(8, 10) == 0.8
    assert compute_confidence(8, 10, ConfidenceContext()) == 0.8


def test_zero_total_returns_zero() -> None:
    assert compute_confidence(0, 0) == 0.0
    assert compute_confidence(5, 0) == 0.0


def test_full_support_caps_at_one() -> None:
    assert compute_confidence(10, 10) == 1.0


def test_negative_inputs_raise() -> None:
    with pytest.raises(ValueError):
        compute_confidence(-1, 10)
    with pytest.raises(ValueError):
        compute_confidence(11, 10)


# ---------------------------------------------------------------------------
# Counterexample penalty
# ---------------------------------------------------------------------------


def test_counterexample_penalty_per_unit() -> None:
    # 8/10 = 0.80; 2 counter * 0.02 = 0.04 → 0.76
    actual = compute_confidence(
        8, 10, ConfidenceContext(counterexample_count=2),
    )
    assert actual == pytest.approx(0.76)


def test_counterexample_penalty_capped() -> None:
    # 100 counters would imply 2.0 drag; cap is 0.15
    cfg = PenaltyConfig()
    actual = compute_confidence(
        50, 100,
        ConfidenceContext(counterexample_count=999),
        cfg,
    )
    assert actual == pytest.approx(0.50 - cfg.counterexample_cap)


def test_counterexample_zero_no_penalty() -> None:
    actual = compute_confidence(
        8, 10, ConfidenceContext(counterexample_count=0),
    )
    assert actual == 0.8


# ---------------------------------------------------------------------------
# Staleness penalty
# ---------------------------------------------------------------------------


def test_staleness_full_halflife_hits_cap() -> None:
    cfg = PenaltyConfig()
    now = datetime(2026, 5, 4, tzinfo=UTC)
    old = now - timedelta(days=cfg.staleness_halflife_days)
    actual = compute_confidence(
        8, 10,
        ConfidenceContext(last_seen_at=old, now=now),
        cfg,
    )
    assert actual == pytest.approx(0.8 - cfg.staleness_cap)


def test_staleness_fresh_no_penalty() -> None:
    now = datetime(2026, 5, 4, tzinfo=UTC)
    actual = compute_confidence(
        8, 10,
        ConfidenceContext(last_seen_at=now, now=now),
    )
    assert actual == 0.8


def test_staleness_naive_datetime_treated_as_utc() -> None:
    cfg = PenaltyConfig()
    now_naive = datetime(2026, 5, 4)
    old_naive = now_naive - timedelta(days=cfg.staleness_halflife_days)
    actual = compute_confidence(
        8, 10,
        ConfidenceContext(last_seen_at=old_naive, now=now_naive),
        cfg,
    )
    assert actual == pytest.approx(0.8 - cfg.staleness_cap)


def test_staleness_future_timestamp_no_penalty() -> None:
    now = datetime(2026, 5, 4, tzinfo=UTC)
    future = now + timedelta(days=30)
    actual = compute_confidence(
        8, 10,
        ConfidenceContext(last_seen_at=future, now=now),
    )
    assert actual == 0.8


# ---------------------------------------------------------------------------
# Low-support penalty
# ---------------------------------------------------------------------------


def test_low_support_penalty_below_floor() -> None:
    cfg = PenaltyConfig()
    # success=3, deficit=5, penalty = min(0.20, 5*0.05) = 0.20
    actual = compute_confidence(3, 4, ConfidenceContext(), cfg)
    assert actual == pytest.approx(0.75 - cfg.low_support_cap)


def test_low_support_above_floor_no_penalty() -> None:
    cfg = PenaltyConfig()
    actual = compute_confidence(
        cfg.low_support_floor, 10, ConfidenceContext(),
    )
    assert actual == pytest.approx(0.8)


def test_low_support_penalty_partial() -> None:
    cfg = PenaltyConfig(low_support_floor=10, low_support_per_unit=0.05)
    # success=8, deficit=2, penalty=0.10
    actual = compute_confidence(8, 10, ConfidenceContext(), cfg)
    assert actual == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# Conflict penalty
# ---------------------------------------------------------------------------


def test_conflict_penalty_per_unit() -> None:
    cfg = PenaltyConfig()
    actual = compute_confidence(
        8, 10, ConfidenceContext(conflict_count=1), cfg,
    )
    assert actual == pytest.approx(0.8 - cfg.conflict_per_unit)


def test_conflict_penalty_capped() -> None:
    cfg = PenaltyConfig()
    actual = compute_confidence(
        8, 10, ConfidenceContext(conflict_count=99), cfg,
    )
    assert actual == pytest.approx(0.8 - cfg.conflict_cap)


# ---------------------------------------------------------------------------
# Hidden-variable multiplier
# ---------------------------------------------------------------------------


def test_hidden_variable_acts_as_multiplier() -> None:
    cfg = PenaltyConfig()
    actual = compute_confidence(
        8, 10,
        ConfidenceContext(has_hidden_variable=True),
        cfg,
    )
    assert actual == pytest.approx(0.8 * cfg.hidden_variable_mult)


def test_hidden_variable_compounds_with_penalty() -> None:
    cfg = PenaltyConfig()
    ctx = ConfidenceContext(
        counterexample_count=2,
        has_hidden_variable=True,
    )
    expected = (0.8 - 2 * cfg.counterexample_per_unit) * (
        cfg.hidden_variable_mult
    )
    assert compute_confidence(8, 10, ctx, cfg) == pytest.approx(
        expected,
    )


# ---------------------------------------------------------------------------
# Combined penalties + clamping
# ---------------------------------------------------------------------------


def test_penalties_clamp_at_zero() -> None:
    # base 0.5 - very large staleness + counter + conflict
    cfg = PenaltyConfig()
    now = datetime(2026, 5, 4, tzinfo=UTC)
    very_old = now - timedelta(days=10_000)
    ctx = ConfidenceContext(
        counterexample_count=999,
        conflict_count=999,
        last_seen_at=very_old,
        now=now,
    )
    actual = compute_confidence(5, 10, ctx, cfg)
    assert 0.0 <= actual <= 1.0
    assert actual == 0.0


def test_penalties_never_lift_above_base() -> None:
    # Sanity: every signal applied reduces (or holds) base.
    base = compute_confidence(8, 10)
    with_signals = compute_confidence(
        8, 10,
        ConfidenceContext(
            counterexample_count=1,
            conflict_count=1,
            has_hidden_variable=True,
        ),
    )
    assert with_signals <= base


def test_round_to_four_decimals() -> None:
    cfg = PenaltyConfig(counterexample_per_unit=0.0123456)
    actual = compute_confidence(
        8, 10,
        ConfidenceContext(counterexample_count=1),
        cfg,
    )
    # 0.8 - 0.0123456 = 0.7876544 → rounded to 0.7877
    assert actual == 0.7877


# ---------------------------------------------------------------------------
# Statistical heuristics registry (ali.md §E)
# ---------------------------------------------------------------------------


def test_heuristic_value_bounds() -> None:
    with pytest.raises(ValueError):
        StatisticalHeuristic(name="x", value=1.5)
    with pytest.raises(ValueError):
        StatisticalHeuristic(name="x", value=-0.1)
    with pytest.raises(ValueError):
        StatisticalHeuristic(name="x", value=0.5, sample_size=-1)


def test_registry_register_get_remove() -> None:
    reg = HeuristicRegistry()
    h = StatisticalHeuristic(
        name="door_code_static",
        value=0.86,
        sample_size=42,
        description="ali.md §E example",
    )
    reg.register(h)
    assert reg.get("door_code_static") is h
    assert "door_code_static" in reg
    assert len(reg) == 1
    assert reg.remove("door_code_static") is True
    assert reg.get("door_code_static") is None
    assert reg.remove("door_code_static") is False


def test_registry_replace_on_register() -> None:
    reg = HeuristicRegistry()
    reg.register(StatisticalHeuristic(name="x", value=0.5))
    reg.register(StatisticalHeuristic(name="x", value=0.7))
    item = reg.get("x")
    assert item is not None
    assert item.value == 0.7
    assert len(reg) == 1


def test_registry_snapshot_sorted_by_name() -> None:
    reg = HeuristicRegistry()
    reg.register(StatisticalHeuristic(name="zebra", value=0.1))
    reg.register(StatisticalHeuristic(name="alpha", value=0.2))
    reg.register(StatisticalHeuristic(name="middle", value=0.3))
    snap = reg.snapshot()
    assert [h.name for h in snap] == ["alpha", "middle", "zebra"]


def test_registry_initial_payload() -> None:
    seed = {
        "p_owner_approves_discount": StatisticalHeuristic(
            name="p_owner_approves_discount",
            value=0.72,
        ),
    }
    reg = HeuristicRegistry(initial=seed)
    assert len(reg) == 1
    item = reg.get("p_owner_approves_discount")
    assert item is not None
    assert item.value == 0.72


def test_registry_contains_only_strings() -> None:
    reg = HeuristicRegistry()
    reg.register(StatisticalHeuristic(name="x", value=0.5))
    assert "x" in reg
    assert 42 not in reg
    assert None not in reg


# ---------------------------------------------------------------------------
# FL-06 — Proactive §5 signal-weight ontology
# ---------------------------------------------------------------------------


def test_default_signal_weights_match_proactive_section_five() -> None:
    """The §5 recommendation must round-trip through ``DEFAULT_SIGNAL_WEIGHTS``.

    Pins the verbatim weights from
    ``Cendra_Brain_Engine_Proactive_Guest_Journey_OperationsFoundation.md``
    §5.  If anyone tweaks the defaults without updating the MD,
    this test surfaces the drift immediately.
    """
    assert DEFAULT_SIGNAL_WEIGHTS.pm_explicit_rule == 0.30
    assert DEFAULT_SIGNAL_WEIGHTS.pm_repeated_edit == 0.15
    assert DEFAULT_SIGNAL_WEIGHTS.pm_approval == 0.10
    assert DEFAULT_SIGNAL_WEIGHTS.guest_complaint == 0.07
    assert DEFAULT_SIGNAL_WEIGHTS.task_reopen == 0.10
    assert DEFAULT_SIGNAL_WEIGHTS.vendor_sla_breach == 0.10
    assert DEFAULT_SIGNAL_WEIGHTS.review_mention == 0.20
    assert CONTRADICTION_UNRESOLVED_CAP == 0.70


# Helper: disables the low-support penalty so signal-boost
# assertions can use exact base values (default floor is 8 with
# 0.05 per-unit drag — would mask the boost under small counts).
_NO_FLOOR = PenaltyConfig(low_support_floor=0)


def test_pm_explicit_rule_boosts_confidence_by_thirty_basis_points() -> None:
    """One PM explicit rule lifts the base ratio by 0.30."""
    ctx = ConfidenceContext(pm_explicit_rule_count=1)
    # base = 5 / 10 = 0.5; boost = +0.30 -> 0.80.
    assert compute_confidence(5, 10, ctx=ctx, cfg=_NO_FLOOR) == 0.80


def test_review_mention_boost_uses_default_weight() -> None:
    """One review mention adds +0.20 to the base ratio."""
    ctx = ConfidenceContext(review_mention_count=1)
    # base = 4 / 10 = 0.4; boost = +0.20 -> 0.60.
    assert compute_confidence(4, 10, ctx=ctx, cfg=_NO_FLOOR) == 0.60


def test_multiple_signals_stack_additively() -> None:
    """Different signals add up — saturates only at the 1.0 ceiling."""
    ctx = ConfidenceContext(
        pm_explicit_rule_count=1,  # +0.30
        pm_approval_count=2,  # +0.20
        review_mention_count=1,  # +0.20
    )
    # base = 3 / 10 = 0.3; boosts sum = +0.70 -> 1.00 (capped).
    assert compute_confidence(3, 10, ctx=ctx, cfg=_NO_FLOOR) == 1.00


def test_signal_boost_capped_at_one() -> None:
    """The combined boost never lifts confidence above 1.0."""
    ctx = ConfidenceContext(
        pm_explicit_rule_count=5,  # would add 1.50 alone
    )
    assert compute_confidence(9, 10, ctx=ctx, cfg=_NO_FLOOR) == 1.00


def test_negative_signal_count_is_clamped_to_zero() -> None:
    """A buggy upstream cannot drag confidence with a negative count."""
    ctx = ConfidenceContext(pm_explicit_rule_count=-3)
    # Negative count treated as 0; base = 5/10 = 0.5 untouched.
    assert compute_confidence(5, 10, ctx=ctx, cfg=_NO_FLOOR) == 0.50


def test_contradiction_unresolved_caps_at_seventy() -> None:
    """An unresolved contradiction enforces the 0.70 ceiling."""
    ctx = ConfidenceContext(
        pm_explicit_rule_count=2,  # would push to 0.5 + 0.6 = 1.0
        contradiction_unresolved=True,
    )
    assert (
        compute_confidence(5, 10, ctx=ctx, cfg=_NO_FLOOR)
        == CONTRADICTION_UNRESOLVED_CAP
    )


def test_contradiction_cap_does_not_raise_below_threshold() -> None:
    """The cap is a ceiling, not a floor — low confidence stays low."""
    ctx = ConfidenceContext(contradiction_unresolved=True)
    # base = 3 / 10 = 0.3; no boost; cap (0.70) does not apply.
    assert compute_confidence(3, 10, ctx=ctx, cfg=_NO_FLOOR) == 0.30


def test_signal_weights_override_replaces_defaults() -> None:
    """Custom :class:`SignalWeights` overrides take precedence."""
    weights = SignalWeights(pm_explicit_rule=0.05)  # heavily reduced
    ctx = ConfidenceContext(pm_explicit_rule_count=2)
    # base = 5 / 10 = 0.5; boost = 2 * 0.05 = 0.10 -> 0.60.
    assert (
        compute_confidence(5, 10, ctx=ctx, cfg=_NO_FLOOR, weights=weights)
        == 0.60
    )


def test_signal_boost_combines_with_penalty() -> None:
    """Boosts apply after penalties so a single boost cannot mask drag."""
    ctx = ConfidenceContext(
        counterexample_count=4,  # drags by counterexample penalty
        pm_explicit_rule_count=1,  # +0.30 boost
    )
    cfg = PenaltyConfig(
        low_support_floor=0,
        counterexample_per_unit=0.05,
        counterexample_cap=0.25,
    )
    # base = 8/10 = 0.80; penalty = 4*0.05 = 0.20 -> 0.60; boost +0.30 -> 0.90.
    assert compute_confidence(8, 10, ctx=ctx, cfg=cfg) == 0.90


def test_signal_boost_keeps_legacy_empty_ctx_at_base() -> None:
    """An empty ``ConfidenceContext`` still collapses to ``success / total``."""
    ctx = ConfidenceContext()
    assert compute_confidence(7, 10, ctx=ctx, cfg=_NO_FLOOR) == 0.70
