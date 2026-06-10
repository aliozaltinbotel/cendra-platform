"""Reflexion verbal-RL Critic for the Memory-R1 policy (Sprint 5).

Brain Engine's M14 cognition loops already have ACE
(Generator → Reflector → Curator) and Memory-R1 (per-step RL
policy).  Both reason *within* a single decision step.  Reflexion
(Shinn et al. arXiv:2303.11366) closes the missing third axis:
*verbal* reinforcement learning *across* steps, in which the
critic distils a free-form natural-language reflection from a
trajectory's outcomes and feeds it back into future decisions.

This module ships the pure-Python seam for that loop:

  * :class:`Critic` — Protocol over ``(trajectory) -> report``.
  * :class:`CriticEvent` — one ``(features, chosen_kind, reward)``
    snapshot, the same triple the M18 / M20 trainers consume.
  * :class:`CritiqueReport` — value object carrying the verbal
    reflection string, a scalar dissatisfaction in ``[0, 1]``,
    the worst-correlated features, and per-kind avoidance hints
    that downstream consumers (notably :class:`~brain_engine.
    cognition_loops.friction.FrictionTracker`) can absorb as
    virtual penalty rewards.
  * :class:`ReflexionCritic` — deterministic heuristic reference
    implementation.  No LLM call required for the seam; an
    LLM-backed Critic can be added later using the existing
    ``litellm`` wiring.

Defensibility (Sprint 5, MAR moat — arXiv:2512.20845): the
patent-defensible novelty is the *combination* of a verbal
Reflexion critique with the friction tracker's operationalised
memory of past failures.  Each component has prior art in
isolation; the bridge that converts the critic's verbal output
into a numeric friction multiplier on future reward signals does
not.  See :mod:`brain_engine.cognition_loops.friction` for the
matching half.

Honest scope
------------

  * Pure-Python; no torch / NumPy / LLM call.
  * The reference :class:`ReflexionCritic` is heuristic — feature
    correlations + reward statistics.  Patent claim is on the
    interface + the friction-absorption glue, not on the
    particular heuristic.
  * Trajectories are caller-supplied; this module does not
    decide *when* to critique (that is the operator's policy —
    typically nightly, alongside :func:`~brain_engine.
    cognition_loops.sleep.summarise_decisions`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

import structlog

from brain_engine.cognition_loops.models import MemoryOpKind


__all__ = [
    "Critic",
    "CriticEvent",
    "CritiqueReport",
    "DEFAULT_DISSATISFACTION_SCALE",
    "DEFAULT_REFLECTION_TOP_FEATURES",
    "ReflexionCritic",
]


DEFAULT_DISSATISFACTION_SCALE: Final[float] = 1.0
DEFAULT_REFLECTION_TOP_FEATURES: Final[int] = 3


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CriticEvent:
    """One ``(features, chosen_kind, reward)`` snapshot.

    Attributes:
        features: Free-form numeric feature map identical to the
            shape the M18 / M20 trainers consume.
        chosen_kind: The :class:`MemoryOpKind` that actually ran.
        reward: Caller-supplied finite reward.  Positive values
            mean the trajectory step was good; negative values
            are what the critic latches onto.
        context: Optional opaque tag (e.g. ``"playbook:noise"``)
            so consumers can filter trajectories by sub-domain.
    """

    features: Mapping[str, float]
    chosen_kind: MemoryOpKind
    reward: float
    context: str = ""

    def __post_init__(self) -> None:
        if self.reward != self.reward or self.reward in (
            float("inf"),
            float("-inf"),
        ):
            raise ValueError("reward must be finite")
        for name, value in self.features.items():
            if value != value or value in (
                float("inf"),
                float("-inf"),
            ):
                raise ValueError(
                    f"feature {name!r} must be finite"
                )


@dataclass(frozen=True, slots=True)
class CritiqueReport:
    """Output of one :class:`Critic` pass over a trajectory.

    Attributes:
        reflection: Free-form natural-language summary of what
            went wrong.  Empty string when ``dissatisfaction``
            is below the critic's verbalisation threshold.
        dissatisfaction: Scalar in ``[0, 1]`` — ``0`` means the
            trajectory was uniformly rewarding, ``1`` means
            uniformly punishing.
        worst_features: Feature names ranked from
            most-punishing-correlation to least.  Length capped
            by the critic's ``top_features`` knob.
        avoidance_hints: Per-:class:`MemoryOpKind` advisory
            penalty in ``[0, 1]``.  ``0`` means "do not avoid";
            higher values mean the critic recommends pushing the
            kind down on future similar contexts.
        sample_size: Number of events the critique was computed
            on.  Audit-trail field; absent from the reflection
            string itself.
    """

    reflection: str
    dissatisfaction: float
    worst_features: tuple[str, ...]
    avoidance_hints: Mapping[MemoryOpKind, float]
    sample_size: int

    def __post_init__(self) -> None:
        if self.sample_size < 0:
            raise ValueError("sample_size must be >= 0")
        if not 0.0 <= self.dissatisfaction <= 1.0:
            raise ValueError(
                "dissatisfaction must be in [0, 1]"
            )
        for kind, value in self.avoidance_hints.items():
            if value != value or value in (
                float("inf"),
                float("-inf"),
            ):
                raise ValueError(
                    "avoidance hint must be finite for "
                    f"{kind!r}"
                )
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    "avoidance hint must be in [0, 1] for "
                    f"{kind!r}"
                )


class Critic(Protocol):
    """Verbal-RL critic over a sequence of :class:`CriticEvent`.

    Implementations decide *how* to verbalise; the Protocol just
    asks for a deterministic :class:`CritiqueReport`.  Empty
    trajectories yield an empty-reflection report with zero
    dissatisfaction — never a raised exception, so that callers
    can poll the critic on a fixed cadence without conditional
    branching.
    """

    def critique(
        self,
        events: Sequence[CriticEvent],
    ) -> CritiqueReport:
        """Return a :class:`CritiqueReport` for ``events``."""
        ...


def _sigmoid(value: float) -> float:
    """Numerically-stable logistic squash."""
    if value >= 0.0:
        ez = math.exp(-value)
        return 1.0 / (1.0 + ez)
    ez = math.exp(value)
    return ez / (1.0 + ez)


@dataclass(frozen=True, slots=True)
class _FeatureGap:
    """Internal: per-feature reward gap presence-vs-absence."""

    name: str
    gap: float
    coverage: int


class ReflexionCritic:
    """Deterministic heuristic :class:`Critic` (no LLM call).

    Reflection is built from three signals:

    1. Mean reward over the trajectory — drives dissatisfaction
       via a logistic squash.
    2. Per-feature reward gap — for each feature seen, compare
       the mean reward of events where ``feature > 0`` against
       events where ``feature <= 0``.  Negative gaps indicate
       the feature correlates with punishment.
    3. Per-kind reward share — kinds whose realised rewards are
       in the bottom half of the trajectory's reward
       distribution earn an avoidance hint proportional to how
       often they were chosen and how negative their mean was.

    The reflection is a short, audit-friendly sentence.  An
    LLM-backed Critic can be plugged via the :class:`Critic`
    Protocol later — this reference impl keeps the seam pure
    Python so callers without an LLM can still close the loop.
    """

    def __init__(
        self,
        *,
        dissatisfaction_scale: float = (
            DEFAULT_DISSATISFACTION_SCALE
        ),
        top_features: int = DEFAULT_REFLECTION_TOP_FEATURES,
        verbalise_threshold: float = 0.25,
    ) -> None:
        if dissatisfaction_scale <= 0.0:
            raise ValueError(
                "dissatisfaction_scale must be positive"
            )
        if top_features < 1:
            raise ValueError("top_features must be >= 1")
        if not 0.0 <= verbalise_threshold <= 1.0:
            raise ValueError(
                "verbalise_threshold must be in [0, 1]"
            )
        self._scale = dissatisfaction_scale
        self._top = top_features
        self._verbalise = verbalise_threshold
        self._log = logger.bind(component="reflexion_critic")

    def critique(
        self,
        events: Sequence[CriticEvent],
    ) -> CritiqueReport:
        """Return a :class:`CritiqueReport` for ``events``."""
        if not events:
            return CritiqueReport(
                reflection="",
                dissatisfaction=0.0,
                worst_features=(),
                avoidance_hints={},
                sample_size=0,
            )
        mean_reward = sum(e.reward for e in events) / len(events)
        dissatisfaction = _sigmoid(-self._scale * mean_reward)
        worst = self._rank_worst_features(events)
        hints = self._build_avoidance_hints(
            events=events,
            mean_reward=mean_reward,
        )
        reflection = self._verbalise_reflection(
            mean_reward=mean_reward,
            dissatisfaction=dissatisfaction,
            worst=worst,
            hints=hints,
            sample_size=len(events),
        )
        self._log.info(
            "critic.report",
            n=len(events),
            mean_reward=round(mean_reward, 4),
            dissatisfaction=round(dissatisfaction, 4),
        )
        return CritiqueReport(
            reflection=reflection,
            dissatisfaction=dissatisfaction,
            worst_features=tuple(w.name for w in worst),
            avoidance_hints=hints,
            sample_size=len(events),
        )

    # ── internals ─────────────────────────────────────────── #

    def _rank_worst_features(
        self,
        events: Sequence[CriticEvent],
    ) -> tuple[_FeatureGap, ...]:
        """Return features sorted by most-punishing reward gap."""
        feature_names: set[str] = set()
        for event in events:
            feature_names.update(event.features.keys())
        gaps: list[_FeatureGap] = []
        for name in sorted(feature_names):
            present = [
                e.reward
                for e in events
                if e.features.get(name, 0.0) > 0.0
            ]
            absent = [
                e.reward
                for e in events
                if e.features.get(name, 0.0) <= 0.0
            ]
            if not present:
                continue
            present_mean = sum(present) / len(present)
            absent_mean = (
                sum(absent) / len(absent) if absent else 0.0
            )
            gap = present_mean - absent_mean
            gaps.append(
                _FeatureGap(
                    name=name,
                    gap=gap,
                    coverage=len(present),
                )
            )
        gaps.sort(key=lambda g: (g.gap, -g.coverage, g.name))
        # ``gap < 0`` ⇒ feature presence correlates with worse
        # reward.  Keep only those; callers can extend top via
        # the knob.
        worst = [g for g in gaps if g.gap < 0.0]
        return tuple(worst[: self._top])

    def _build_avoidance_hints(
        self,
        *,
        events: Sequence[CriticEvent],
        mean_reward: float,
    ) -> dict[MemoryOpKind, float]:
        """Return per-kind avoidance hint in ``[0, 1]``.

        A kind earns a hint when (a) it was chosen at least
        once and (b) its mean realised reward is below the
        trajectory mean.  The hint magnitude is the logistic
        squash of the (signed) gap between the kind's mean and
        the trajectory mean, scaled by the chosen-fraction so
        rarely-tried kinds do not dominate the signal.
        """
        per_kind_rewards: dict[MemoryOpKind, list[float]] = {}
        for event in events:
            per_kind_rewards.setdefault(
                event.chosen_kind, []
            ).append(event.reward)
        hints: dict[MemoryOpKind, float] = {}
        total = len(events)
        for kind, rewards in per_kind_rewards.items():
            kind_mean = sum(rewards) / len(rewards)
            if kind_mean >= mean_reward:
                continue
            gap = mean_reward - kind_mean
            fraction = len(rewards) / total
            hints[kind] = _sigmoid(self._scale * gap) * fraction
        return hints

    def _verbalise_reflection(
        self,
        *,
        mean_reward: float,
        dissatisfaction: float,
        worst: Sequence[_FeatureGap],
        hints: Mapping[MemoryOpKind, float],
        sample_size: int,
    ) -> str:
        """Return the audit-friendly reflection sentence."""
        if dissatisfaction < self._verbalise:
            return ""
        parts = [
            f"Mean reward {mean_reward:+.3f} over "
            f"{sample_size} step(s); dissatisfaction "
            f"{dissatisfaction:.2f}."
        ]
        if worst:
            named = ", ".join(
                f"{w.name} (gap {w.gap:+.3f})"
                for w in worst
            )
            parts.append(
                f"Features correlated with punishment: {named}."
            )
        if hints:
            ranked = sorted(
                hints.items(), key=lambda kv: (-kv[1], kv[0])
            )
            named = ", ".join(
                f"{kind.value} ({weight:.2f})"
                for kind, weight in ranked
            )
            parts.append(
                f"Avoid on future similar contexts: {named}."
            )
        return " ".join(parts)
