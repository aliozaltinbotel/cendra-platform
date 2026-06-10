"""Belief-promotion gate.

Promotion is the only path from observation history to belief
state.  The gate combines two bounds:

1. **Sample size** — refuse to promote on a thin window.  Default
   ``min_samples = 30`` mirrors the abstention layer (Moat #1) so
   regulators read a single threshold across both subsystems.
2. **Wilson lower bound** — refuse to promote when empirical
   ``success`` rate (caller-supplied predicate) does not clear the
   policy threshold under the configured confidence level.

The supplied :class:`SuccessPredicate` decides whether each
observation counts as a *success*.  The default predicate
:func:`predicate_truthy` treats any boolean / non-zero numeric /
non-empty value as success — useful for binary observation
streams (smoke detected, noise quiet hour, lock unlocked).

Promotion produces an immutable :class:`Belief`.  Persisting it is
the caller's job (typically into a :class:`BeliefStore`).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any, Final

from brain_engine.epistemic.models import (
    Belief,
    Observation,
)
from brain_engine.patterns.wilson import (
    Z_95,
    wilson_lower_bound,
)


__all__ = [
    "BeliefPromotionGate",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_WILSON_THRESHOLD",
    "PromotionRefusal",
    "SuccessPredicate",
    "predicate_truthy",
]


DEFAULT_MIN_SAMPLES: Final[int] = 30
DEFAULT_WILSON_THRESHOLD: Final[float] = 0.6


SuccessPredicate = Callable[[Observation], bool]
"""Callable mapping an observation to a binary success flag."""


def predicate_truthy(observation: Observation) -> bool:
    """Default predicate — truthy values count as successes."""
    return bool(observation.value)


class PromotionRefusal(RuntimeError):
    """Raised by :meth:`BeliefPromotionGate.promote` on refusal.

    The exception carries the gate's diagnostic data so the audit
    log records *why* promotion was refused.
    """

    def __init__(
        self,
        *,
        subject: str,
        sample_size: int,
        wilson_lb: float,
        reason: str,
    ) -> None:
        message = (
            f"refused to promote {subject!r}: {reason}; "
            f"n={sample_size} wilson={wilson_lb:.3f}"
        )
        super().__init__(message)
        self.subject = subject
        self.sample_size = sample_size
        self.wilson_lb = wilson_lb
        self.reason = reason


class BeliefPromotionGate:
    """Guarded promotion of observations into a :class:`Belief`.

    The gate is stateless — it accepts the observation window
    (callers fetch it from the :class:`ObservationStore`) and the
    success predicate, then either returns a :class:`Belief` or
    raises :class:`PromotionRefusal`.
    """

    def __init__(
        self,
        *,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        wilson_threshold: float = DEFAULT_WILSON_THRESHOLD,
        z: float = Z_95,
    ) -> None:
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if not 0.0 <= wilson_threshold <= 1.0:
            raise ValueError(
                "wilson_threshold must be in [0.0, 1.0]"
            )
        if z <= 0.0:
            raise ValueError("z must be positive")
        self._min_samples = min_samples
        self._wilson_threshold = wilson_threshold
        self._z = z

    def evaluate(
        self,
        observations: Sequence[Observation],
        *,
        predicate: SuccessPredicate = predicate_truthy,
    ) -> tuple[int, float]:
        """Return ``(sample_size, wilson_lb)`` for the window."""
        successes = sum(
            1 for obs in observations if predicate(obs)
        )
        return len(observations), wilson_lower_bound(
            successes=successes,
            trials=len(observations),
            z=self._z,
        )

    def promote(
        self,
        *,
        subject: str,
        observations: Sequence[Observation],
        promoted_value: Any,
        promoted_by: str = "system",
        predicate: SuccessPredicate = predicate_truthy,
        belief_id: str | None = None,
        promoted_at: datetime | None = None,
    ) -> Belief:
        """Build a :class:`Belief` for ``subject`` or refuse.

        Args:
            subject: Subject the belief is about.  Must match the
                ``subject`` of every observation in ``observations``.
            observations: Window of supporting evidence; typically
                fetched via :meth:`ObservationStore.observations_for`.
            promoted_value: The inferred value the belief carries
                (a mean, a class label, a tag).
            promoted_by: Actor identifier.
            predicate: Maps each observation to a success flag.
            belief_id: Override identifier; defaults to a
                URL-safe random token.
            promoted_at: Override instant; defaults to now (UTC).

        Returns:
            A frozen :class:`Belief`.

        Raises:
            PromotionRefusal: When the gate denies promotion.
            ValueError: When ``observations`` contains a subject
                that does not match ``subject``.
        """
        for obs in observations:
            if obs.subject != subject:
                raise ValueError(
                    f"observation subject {obs.subject!r} != "
                    f"requested subject {subject!r}"
                )
        sample_size, wilson_lb = self.evaluate(
            observations, predicate=predicate,
        )
        if sample_size < self._min_samples:
            raise PromotionRefusal(
                subject=subject,
                sample_size=sample_size,
                wilson_lb=wilson_lb,
                reason=(
                    f"sample_size < min_samples="
                    f"{self._min_samples}"
                ),
            )
        if wilson_lb < self._wilson_threshold:
            raise PromotionRefusal(
                subject=subject,
                sample_size=sample_size,
                wilson_lb=wilson_lb,
                reason=(
                    f"wilson_lb < threshold="
                    f"{self._wilson_threshold:.2f}"
                ),
            )
        identifier = belief_id or secrets.token_urlsafe(16)
        instant = promoted_at or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("promoted_at must be tz-aware")
        return Belief(
            belief_id=identifier,
            subject=subject,
            promoted_value=promoted_value,
            wilson_lb=wilson_lb,
            sample_size=sample_size,
            supporting_observation_ids=tuple(
                obs.observation_id for obs in observations
            ),
            promoted_at=instant,
            promoted_by=promoted_by,
        )
