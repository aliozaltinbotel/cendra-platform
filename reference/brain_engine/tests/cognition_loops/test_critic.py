"""Behaviour of :class:`ReflexionCritic`."""

from __future__ import annotations

import pytest

from brain_engine.cognition_loops.critic import (
    CriticEvent,
    CritiqueReport,
    ReflexionCritic,
)
from brain_engine.cognition_loops.models import MemoryOpKind


def test_empty_trajectory_returns_neutral_report() -> None:
    """No events ⇒ empty reflection, zero dissatisfaction."""
    critic = ReflexionCritic()
    report = critic.critique([])
    assert report.reflection == ""
    assert report.dissatisfaction == 0.0
    assert report.worst_features == ()
    assert dict(report.avoidance_hints) == {}
    assert report.sample_size == 0


def test_single_positive_event_low_dissatisfaction() -> None:
    """A solitary positive event keeps dissatisfaction near 0."""
    critic = ReflexionCritic()
    event = CriticEvent(
        features={"x": 1.0},
        chosen_kind=MemoryOpKind.ADD,
        reward=2.0,
    )
    report = critic.critique([event])
    assert report.dissatisfaction < 0.2
    assert report.sample_size == 1
    # The trajectory mean equals the per-kind mean, so the
    # critic has nothing to single out — no avoidance hints.
    assert dict(report.avoidance_hints) == {}


def test_all_negative_events_high_dissatisfaction() -> None:
    """All-negative trajectory drives dissatisfaction toward 1."""
    critic = ReflexionCritic()
    events = [
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.5,
        ),
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-2.0,
        ),
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.2,
        ),
    ]
    report = critic.critique(events)
    assert report.dissatisfaction > 0.75
    assert report.reflection != ""
    assert "Mean reward" in report.reflection


def test_worst_features_identified_by_correlation() -> None:
    """A feature that only appears with bad rewards is flagged."""
    critic = ReflexionCritic()
    events = [
        CriticEvent(
            features={"noisy": 1.0, "calm": 0.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.0,
        ),
        CriticEvent(
            features={"noisy": 1.0, "calm": 0.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.5,
        ),
        CriticEvent(
            features={"noisy": 0.0, "calm": 1.0},
            chosen_kind=MemoryOpKind.NOOP,
            reward=0.6,
        ),
    ]
    report = critic.critique(events)
    assert "noisy" in report.worst_features


def test_avoidance_hints_target_below_mean_kinds() -> None:
    """Kinds with mean below trajectory mean earn a hint."""
    critic = ReflexionCritic()
    events = [
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.0,
        ),
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.0,
        ),
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.NOOP,
            reward=0.5,
        ),
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.NOOP,
            reward=0.5,
        ),
    ]
    report = critic.critique(events)
    assert MemoryOpKind.ADD in report.avoidance_hints
    assert MemoryOpKind.NOOP not in report.avoidance_hints
    add_hint = report.avoidance_hints[MemoryOpKind.ADD]
    assert 0.0 < add_hint <= 1.0


def test_uniform_mean_yields_no_avoidance_hints() -> None:
    """All kinds at the trajectory mean ⇒ empty avoidance hints."""
    critic = ReflexionCritic()
    events = [
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=0.5,
        ),
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.NOOP,
            reward=0.5,
        ),
    ]
    report = critic.critique(events)
    assert dict(report.avoidance_hints) == {}


def test_reflection_silent_below_verbalise_threshold() -> None:
    """Mild dissatisfaction ⇒ empty reflection sentence."""
    critic = ReflexionCritic(verbalise_threshold=0.99)
    events = [
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-0.1,
        ),
    ]
    report = critic.critique(events)
    assert report.reflection == ""


def test_critique_report_validates_bounds() -> None:
    """``CritiqueReport`` rejects out-of-range fields."""
    with pytest.raises(ValueError, match="dissatisfaction"):
        CritiqueReport(
            reflection="",
            dissatisfaction=1.5,
            worst_features=(),
            avoidance_hints={},
            sample_size=0,
        )
    with pytest.raises(ValueError, match="avoidance hint"):
        CritiqueReport(
            reflection="",
            dissatisfaction=0.5,
            worst_features=(),
            avoidance_hints={MemoryOpKind.ADD: 1.4},
            sample_size=1,
        )
    with pytest.raises(ValueError, match="sample_size"):
        CritiqueReport(
            reflection="",
            dissatisfaction=0.0,
            worst_features=(),
            avoidance_hints={},
            sample_size=-1,
        )


def test_critic_event_rejects_non_finite_reward() -> None:
    """Infinite / NaN rewards are rejected at construction."""
    with pytest.raises(ValueError, match="reward"):
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=float("nan"),
        )
    with pytest.raises(ValueError, match="reward"):
        CriticEvent(
            features={"x": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=float("inf"),
        )


def test_critic_event_rejects_non_finite_feature() -> None:
    """Infinite / NaN features are rejected at construction."""
    with pytest.raises(ValueError, match="feature"):
        CriticEvent(
            features={"x": float("nan")},
            chosen_kind=MemoryOpKind.ADD,
            reward=0.0,
        )


def test_reflexion_critic_rejects_invalid_knobs() -> None:
    """Negative / out-of-range knobs raise at construction."""
    with pytest.raises(
        ValueError, match="dissatisfaction_scale"
    ):
        ReflexionCritic(dissatisfaction_scale=0.0)
    with pytest.raises(ValueError, match="top_features"):
        ReflexionCritic(top_features=0)
    with pytest.raises(
        ValueError, match="verbalise_threshold"
    ):
        ReflexionCritic(verbalise_threshold=1.5)


def test_reflection_mentions_worst_features() -> None:
    """The verbal reflection names the worst features."""
    critic = ReflexionCritic(verbalise_threshold=0.0)
    events = [
        CriticEvent(
            features={"loud": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.0,
        ),
        CriticEvent(
            features={"loud": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-2.0,
        ),
        CriticEvent(
            features={"loud": 0.0},
            chosen_kind=MemoryOpKind.NOOP,
            reward=1.0,
        ),
    ]
    report = critic.critique(events)
    assert "loud" in report.reflection


def test_top_features_cap_is_honored() -> None:
    """``top_features=1`` returns at most one worst feature."""
    critic = ReflexionCritic(top_features=1)
    events = [
        CriticEvent(
            features={"a": 1.0, "b": 1.0, "c": 1.0},
            chosen_kind=MemoryOpKind.ADD,
            reward=-3.0,
        ),
        CriticEvent(
            features={"a": 0.0, "b": 0.0, "c": 0.0},
            chosen_kind=MemoryOpKind.NOOP,
            reward=1.0,
        ),
    ]
    report = critic.critique(events)
    assert len(report.worst_features) <= 1


def test_deterministic_output() -> None:
    """Running the same critic twice on the same events agrees."""
    critic = ReflexionCritic()
    events = [
        CriticEvent(
            features={"x": 1.0, "y": 0.5},
            chosen_kind=MemoryOpKind.ADD,
            reward=-1.0,
        ),
        CriticEvent(
            features={"x": 0.0, "y": 0.5},
            chosen_kind=MemoryOpKind.NOOP,
            reward=0.4,
        ),
    ]
    first = critic.critique(events)
    second = critic.critique(events)
    assert first.reflection == second.reflection
    assert first.dissatisfaction == second.dissatisfaction
    assert first.worst_features == second.worst_features
    assert dict(first.avoidance_hints) == dict(
        second.avoidance_hints
    )
