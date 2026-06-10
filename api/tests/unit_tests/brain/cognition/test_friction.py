"""Behaviour of :class:`FrictionTracker` and the wrapper sim."""

from __future__ import annotations

import math

import pytest

from core.brain.cognition.critic import (
    CritiqueReport,
)
from core.brain.cognition.friction import (
    FrictionRewardSimulator,
    FrictionState,
    FrictionTracker,
    canonical_state_key,
)
from core.brain.cognition.models import MemoryOpKind


class LookupRewardSimulator:
    """Local stand-in for the reference's ``grpo.LookupRewardSimulator``.

    ``grpo.py`` is a Batch 6 port (PORTING_MAP.md); this stub replicates
    the table-driven lookup behaviour the friction tests need —
    frozenset-keyed table, configurable default on miss.
    """

    def __init__(self, *, table, default_reward: float = 0.0) -> None:
        self._table = dict(table)
        self._default = default_reward

    def simulate(self, *, features, op_kind) -> float:
        key = (frozenset(features.items()), op_kind)
        return self._table.get(key, self._default)


def test_unknown_state_returns_unit_friction() -> None:
    """Fresh state ⇒ multiplier is 1.0 (no friction applied)."""
    tracker = FrictionTracker()
    assert (
        tracker.friction(
            state_key="any",
            op_kind=MemoryOpKind.ADD,
        )
        == 1.0
    )


def test_positive_rewards_keep_friction_at_one() -> None:
    """Positive EMA ⇒ friction never penalises."""
    tracker = FrictionTracker()
    for reward in (0.5, 0.6, 0.7):
        tracker.record(
            state_key="warm",
            op_kind=MemoryOpKind.ADD,
            reward=reward,
        )
    assert (
        tracker.friction(
            state_key="warm",
            op_kind=MemoryOpKind.ADD,
        )
        == 1.0
    )


def test_repeated_negative_rewards_compound() -> None:
    """Successive negative observations push friction toward 0."""
    tracker = FrictionTracker()
    seen: list[float] = []
    for _ in range(8):
        tracker.record(
            state_key="cold",
            op_kind=MemoryOpKind.ADD,
            reward=-1.0,
        )
        seen.append(
            tracker.friction(
                state_key="cold",
                op_kind=MemoryOpKind.ADD,
            )
        )
    # Strictly decreasing — more punishment ⇒ lower multiplier.
    for prev, curr in zip(seen, seen[1:]):
        assert curr <= prev
    assert seen[0] > seen[-1]
    assert seen[-1] < 0.5


def test_single_negative_observation_drops_friction() -> None:
    """One punishing observation already pulls below 1.0."""
    tracker = FrictionTracker()
    tracker.record(
        state_key="s",
        op_kind=MemoryOpKind.ADD,
        reward=-1.0,
    )
    multiplier = tracker.friction(
        state_key="s",
        op_kind=MemoryOpKind.ADD,
    )
    assert 0.0 < multiplier < 1.0


def test_recovery_via_positive_rewards() -> None:
    """Positive rewards restore friction toward 1 over time."""
    tracker = FrictionTracker()
    for _ in range(3):
        tracker.record(
            state_key="recover",
            op_kind=MemoryOpKind.ADD,
            reward=-1.0,
        )
    bad = tracker.friction(
        state_key="recover",
        op_kind=MemoryOpKind.ADD,
    )
    for _ in range(50):
        tracker.record(
            state_key="recover",
            op_kind=MemoryOpKind.ADD,
            reward=1.0,
        )
    recovered = tracker.friction(
        state_key="recover",
        op_kind=MemoryOpKind.ADD,
    )
    assert recovered > bad
    # EMA fully overshoots the initial penalty.
    assert recovered == 1.0


def test_reset_clears_specific_state_key() -> None:
    """``reset(state_key=...)`` only clears matching entries."""
    tracker = FrictionTracker()
    tracker.record(
        state_key="a",
        op_kind=MemoryOpKind.ADD,
        reward=-1.0,
    )
    tracker.record(
        state_key="b",
        op_kind=MemoryOpKind.ADD,
        reward=-1.0,
    )
    tracker.reset(state_key="a")
    assert (
        tracker.friction(
            state_key="a",
            op_kind=MemoryOpKind.ADD,
        )
        == 1.0
    )
    assert (
        tracker.friction(
            state_key="b",
            op_kind=MemoryOpKind.ADD,
        )
        < 1.0
    )


def test_reset_clears_all_when_state_key_is_none() -> None:
    """``reset()`` drops every tracked state."""
    tracker = FrictionTracker()
    tracker.record(
        state_key="a",
        op_kind=MemoryOpKind.ADD,
        reward=-1.0,
    )
    tracker.record(
        state_key="b",
        op_kind=MemoryOpKind.NOOP,
        reward=-1.0,
    )
    tracker.reset()
    assert tracker.snapshot() == {}


def test_absorb_critique_applies_avoidance_hints() -> None:
    """A critique's avoidance hints become synthetic punishment."""
    tracker = FrictionTracker()
    report = CritiqueReport(
        reflection="",
        dissatisfaction=0.6,
        worst_features=(),
        avoidance_hints={MemoryOpKind.ADD: 0.5},
        sample_size=3,
    )
    tracker.absorb_critique(
        state_key="hot",
        report=report,
    )
    multiplier = tracker.friction(
        state_key="hot",
        op_kind=MemoryOpKind.ADD,
    )
    assert multiplier < 1.0


def test_absorb_critique_with_empty_hints_is_noop() -> None:
    """Zero-hint reports leave the tracker untouched."""
    tracker = FrictionTracker()
    report = CritiqueReport(
        reflection="",
        dissatisfaction=0.0,
        worst_features=(),
        avoidance_hints={},
        sample_size=0,
    )
    tracker.absorb_critique(
        state_key="hot",
        report=report,
    )
    assert tracker.snapshot() == {}


def test_absorb_critique_ignores_zero_value_hints() -> None:
    """A 0.0 hint for some kind is treated as no-op."""
    tracker = FrictionTracker()
    report = CritiqueReport(
        reflection="",
        dissatisfaction=0.0,
        worst_features=(),
        avoidance_hints={MemoryOpKind.ADD: 0.0},
        sample_size=1,
    )
    tracker.absorb_critique(
        state_key="hot",
        report=report,
    )
    assert tracker.snapshot() == {}


def test_canonical_state_key_is_order_invariant() -> None:
    """Feature insertion order does not change the key."""
    a = canonical_state_key({"x": 1.0, "y": 2.0})
    b = canonical_state_key({"y": 2.0, "x": 1.0})
    assert a == b


def test_canonical_state_key_handles_empty_mapping() -> None:
    """Empty features yield the empty-string key."""
    assert canonical_state_key({}) == ""


def test_friction_reward_simulator_applies_multiplier() -> None:
    """The wrapper scales inner reward by the tracker multiplier."""
    inner = LookupRewardSimulator(
        table={
            (
                frozenset({("x", 1.0)}),
                MemoryOpKind.ADD,
            ): -1.0,
        }
    )
    tracker = FrictionTracker()
    sim = FrictionRewardSimulator(
        inner=inner,
        tracker=tracker,
    )
    first = sim.simulate(
        features={"x": 1.0},
        op_kind=MemoryOpKind.ADD,
    )
    # First step has no prior history ⇒ friction is 1.0.
    assert first == -1.0
    second = sim.simulate(
        features={"x": 1.0},
        op_kind=MemoryOpKind.ADD,
    )
    # Second step sees the previous negative ⇒ friction < 1 ⇒
    # the magnitude shrinks (closer to zero).
    assert second > first
    assert second < 0.0


def test_friction_reward_simulator_records_shaped_reward() -> None:
    """The shaped reward is what gets written to the tracker."""
    inner = LookupRewardSimulator(
        table={
            (
                frozenset({("x", 1.0)}),
                MemoryOpKind.ADD,
            ): -1.0,
        }
    )
    tracker = FrictionTracker()
    sim = FrictionRewardSimulator(
        inner=inner,
        tracker=tracker,
    )
    shaped_observations: list[float] = []
    for _ in range(4):
        shaped_observations.append(
            sim.simulate(
                features={"x": 1.0},
                op_kind=MemoryOpKind.ADD,
            )
        )
    snapshot = tracker.snapshot()
    state = snapshot[(canonical_state_key({"x": 1.0}), MemoryOpKind.ADD)]
    assert state.count == 4
    # Magnitudes monotonically shrink — successive friction
    # multiplies decay toward zero.
    for prev, curr in zip(shaped_observations, shaped_observations[1:]):
        assert abs(curr) <= abs(prev)


def test_friction_tracker_rejects_invalid_knobs() -> None:
    """Construction validates ``alpha`` / ``ema_decay``."""
    with pytest.raises(ValueError, match="alpha"):
        FrictionTracker(alpha=0.0)
    with pytest.raises(ValueError, match="ema_decay"):
        FrictionTracker(ema_decay=0.0)
    with pytest.raises(ValueError, match="ema_decay"):
        FrictionTracker(ema_decay=1.5)
    with pytest.raises(ValueError, match="critique_weight"):
        FrictionTracker(critique_weight=-0.1)


def test_friction_tracker_rejects_non_finite_reward() -> None:
    """Recording NaN / inf rewards raises."""
    tracker = FrictionTracker()
    with pytest.raises(ValueError, match="reward"):
        tracker.record(
            state_key="s",
            op_kind=MemoryOpKind.ADD,
            reward=float("nan"),
        )
    with pytest.raises(ValueError, match="reward"):
        tracker.record(
            state_key="s",
            op_kind=MemoryOpKind.ADD,
            reward=float("inf"),
        )


def test_friction_state_is_copied_via_snapshot() -> None:
    """``snapshot`` returns fresh :class:`FrictionState` copies."""
    tracker = FrictionTracker()
    tracker.record(
        state_key="s",
        op_kind=MemoryOpKind.ADD,
        reward=-0.5,
    )
    snap = tracker.snapshot()
    state = snap[("s", MemoryOpKind.ADD)]
    assert isinstance(state, FrictionState)
    state.ema_reward = 99.0
    fresh = tracker.snapshot()[("s", MemoryOpKind.ADD)]
    assert fresh.ema_reward != 99.0


def test_friction_kernel_matches_documented_formula() -> None:
    """Spot-check the closed-form: exp(-α · |EMA⁻| · log1p(n))."""
    tracker = FrictionTracker(
        alpha=1.0,
        ema_decay=1.0,
    )
    # ema_decay=1.0 ⇒ ema_reward == last observed reward.
    tracker.record(
        state_key="k",
        op_kind=MemoryOpKind.ADD,
        reward=-2.0,
    )
    expected = math.exp(-1.0 * 2.0 * math.log1p(1))
    actual = tracker.friction(
        state_key="k",
        op_kind=MemoryOpKind.ADD,
    )
    assert math.isclose(actual, expected, rel_tol=1e-9)
