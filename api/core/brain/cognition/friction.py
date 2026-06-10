"""Friction tracker for the Memory-R1 policy (Sprint 5).

Friction is the numeric memory of repeated low-reward states.
It is the second half of the MAR (Memory-Augmented Reflexion)
moat — the first half being the verbal
:class:`~core.brain.cognition.critic.Critic`.

Where the Critic emits a free-form verbal reflection (Reflexion
arXiv:2303.11366), the :class:`FrictionTracker` translates that
reflection — plus the raw reward history — into a per-state,
per-:class:`MemoryOpKind` multiplier in ``[0, 1]``.  A friction
of ``1.0`` means "no penalty"; a friction of ``0.0`` means "the
state has been punishing every time we tried this kind, cancel
the reward signal".

The tracker is consumed by :class:`FrictionRewardSimulator`,
which wraps any :class:`RewardSimulator` and multiplies the realised reward by the
friction value before handing it to the GRPO / SGD trainer.
This is the patent-defensible bridge: the Critic's *verbal*
output becomes a *scalar* knob on future reward shaping.

Honest scope
------------

  * Pure-Python; no torch / NumPy.
  * Friction tracking is an exponential moving average with a
    log-scaled count multiplier — chosen so the response curve
    matches the intuitive "repeated punishment compounds" the
    MAR paper argues for, without introducing hyperparameter
    sprawl.
  * State keys are caller-supplied opaque strings; the tracker
    does not opinion on how the upstream maps features → keys.
    A default canonicaliser is provided
    (:func:`canonical_state_key`) for convenience.

Reference: MAR / "Memory-Augmented Reflexion" — arXiv:2512.20845.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from core.brain.cognition.critic import CritiqueReport
from core.brain.cognition.models import MemoryOpKind

__all__ = [
    "DEFAULT_FRICTION_ALPHA",
    "DEFAULT_FRICTION_EMA_DECAY",
    "FrictionRewardSimulator",
    "FrictionState",
    "FrictionTracker",
    "RewardSimulator",
    "canonical_state_key",
]


DEFAULT_FRICTION_ALPHA: Final[float] = 0.8
DEFAULT_FRICTION_EMA_DECAY: Final[float] = 0.3


logger = logging.getLogger(__name__)


class RewardSimulator(Protocol):
    """Per-(features, op_kind) reward source.

    Definition inlined verbatim from the reference's
    ``cognition_loops/grpo.py`` (Batch 6): the friction wrapper is the
    consumer of this seam and must not wait on the GRPO trainer port.
    When ``grpo.py`` lands it imports this Protocol rather than
    redefining it.

    Implementations may:

    - read from a replay buffer keyed off ``(features, op_kind)``;
    - call out to a Property Twin (M13 / M17) for an imagined
      rollout reward;
    - hit a production env directly during shadow-mode training.

    The Protocol intentionally hides which path; the trainer
    just asks for a finite reward.
    """

    def simulate(
        self,
        *,
        features: Mapping[str, float],
        op_kind: MemoryOpKind,
    ) -> float:
        """Return the realised reward of ``op_kind`` under ``features``."""
        ...


def canonical_state_key(features: Mapping[str, float]) -> str:
    """Return a deterministic string key for ``features``.

    Features are sorted lexicographically and rendered as
    ``name=value`` joined by ``|``.  The format is stable across
    Python versions (no hash randomisation) and human-readable
    in audit logs.
    """
    if not features:
        return ""
    parts = [f"{name}={value!r}" for name, value in sorted(features.items())]
    return "|".join(parts)


@dataclass(slots=True)
class FrictionState:
    """Per-``(state_key, op_kind)`` accumulator.

    Attributes:
        ema_reward: Exponential moving average of realised
            reward; updated by :meth:`FrictionTracker.record`.
        count: Number of observations the EMA was built from.
            Used by the friction kernel to scale the response
            curve so a single negative observation does not
            immediately collapse the multiplier.

    The class is mutable by design — the tracker updates the
    state in place.  Callers wanting an immutable snapshot can
    construct a fresh dataclass copy.
    """

    ema_reward: float = 0.0
    count: int = 0


class FrictionTracker:
    """Tracks per-``(state_key, op_kind)`` reward EMA + count.

    Two ways to update the tracker:

    * :meth:`record` — observed reward from a real or simulated
      step.  Updates the EMA via standard exponential smoothing
      and increments the count.
    * :meth:`absorb_critique` — verbal Critic output converted
      into a virtual negative reward.  Each non-zero entry in
      the report's ``avoidance_hints`` translates into a
      synthetic reward of ``-hint`` weighted by the configured
      ``critique_weight``; the count is incremented by the
      smallest integer that bounds the weight from below so the
      kernel responds proportionally without runaway.

    Read side:

    * :meth:`friction` — the multiplier in ``[0, 1]`` to apply
      on the next reward for ``(state_key, op_kind)``.  Default
      formula: ``exp(-alpha · max(0, -ema_reward) ·
      log1p(count))`` so positive EMAs always read 1.0 and
      compounding negative observations decay multiplicatively.
    """

    def __init__(
        self,
        *,
        alpha: float = DEFAULT_FRICTION_ALPHA,
        ema_decay: float = DEFAULT_FRICTION_EMA_DECAY,
        critique_weight: float = 1.0,
    ) -> None:
        if alpha <= 0.0:
            raise ValueError("alpha must be positive")
        if not 0.0 < ema_decay <= 1.0:
            raise ValueError("ema_decay must be in (0, 1]")
        if critique_weight < 0.0:
            raise ValueError("critique_weight must be non-negative")
        self._alpha = alpha
        self._decay = ema_decay
        self._critique_weight = critique_weight
        self._states: dict[tuple[str, MemoryOpKind], FrictionState] = {}

    # ── write side ────────────────────────────────────────── #

    def record(
        self,
        *,
        state_key: str,
        op_kind: MemoryOpKind,
        reward: float,
    ) -> None:
        """Update the EMA + count for ``(state_key, op_kind)``."""
        if reward != reward or reward in (
            float("inf"),
            float("-inf"),
        ):
            raise ValueError("reward must be finite")
        state = self._states.setdefault((state_key, op_kind), FrictionState())
        if state.count == 0:
            state.ema_reward = reward
        else:
            state.ema_reward = self._decay * reward + (1.0 - self._decay) * state.ema_reward
        state.count += 1

    def absorb_critique(
        self,
        *,
        state_key: str,
        report: CritiqueReport,
    ) -> None:
        """Translate ``report.avoidance_hints`` into virtual punishment.

        Each non-zero hint ``h`` for kind ``k`` is fed back into
        :meth:`record` as ``reward = -h · critique_weight``.  A
        zero-hint entry is a no-op.  Empty reports are no-ops.
        """
        if not report.avoidance_hints:
            return
        for kind, hint in report.avoidance_hints.items():
            if hint == 0.0:
                continue
            synthetic = -hint * self._critique_weight
            self.record(
                state_key=state_key,
                op_kind=kind,
                reward=synthetic,
            )

    # ── read side ─────────────────────────────────────────── #

    def friction(
        self,
        *,
        state_key: str,
        op_kind: MemoryOpKind,
    ) -> float:
        """Return the friction multiplier in ``[0, 1]``."""
        state = self._states.get((state_key, op_kind))
        if state is None or state.count == 0:
            return 1.0
        penalty = max(0.0, -state.ema_reward)
        if penalty == 0.0:
            return 1.0
        magnitude = penalty * math.log1p(state.count)
        return math.exp(-self._alpha * magnitude)

    def snapshot(
        self,
    ) -> Mapping[tuple[str, MemoryOpKind], FrictionState]:
        """Return a fresh dict copy of the internal table."""
        return {
            key: FrictionState(
                ema_reward=state.ema_reward,
                count=state.count,
            )
            for key, state in self._states.items()
        }

    def reset(
        self,
        *,
        state_key: str | None = None,
    ) -> None:
        """Drop tracked state.

        - ``state_key is None`` clears everything.
        - Otherwise drops every entry whose state matches.
        """
        if state_key is None:
            self._states.clear()
            return
        for key in list(self._states.keys()):
            if key[0] == state_key:
                del self._states[key]


class FrictionRewardSimulator:
    """Wraps a :class:`RewardSimulator` with friction multiplier.

    The wrapper computes the inner reward, multiplies it by the
    friction value for the same ``(state_key, op_kind)``, and
    records the *frictioned* outcome back into the tracker so
    the EMA reflects what the downstream learner actually saw.

    Callers supply a ``state_key_fn`` that maps the feature dict
    to the hashable key the tracker indexes by.  The default is
    :func:`canonical_state_key`, which gives a stable lossless
    string representation; replace it with a coarser bucketing
    function (e.g. binned reservation lead-time) when the
    feature space is too sparse for per-tuple memory to
    generalise.
    """

    def __init__(
        self,
        *,
        inner: RewardSimulator,
        tracker: FrictionTracker,
        state_key_fn: Callable[[Mapping[str, float]], str] = canonical_state_key,
    ) -> None:
        self._inner = inner
        self._tracker = tracker
        self._state_key_fn = state_key_fn

    def simulate(
        self,
        *,
        features: Mapping[str, float],
        op_kind: MemoryOpKind,
    ) -> float:
        """Return ``inner.simulate(...) * friction(...)``."""
        state_key = self._state_key_fn(features)
        raw = self._inner.simulate(
            features=features,
            op_kind=op_kind,
        )
        multiplier = self._tracker.friction(
            state_key=state_key,
            op_kind=op_kind,
        )
        shaped = raw * multiplier
        self._tracker.record(
            state_key=state_key,
            op_kind=op_kind,
            reward=shaped,
        )
        return shaped
