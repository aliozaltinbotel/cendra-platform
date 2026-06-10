"""Promotion/demotion gate for per-workflow autonomy.

The gate is a pure policy function: given ``WorkflowMetrics`` and a
current ``AutonomyState``, it returns the state the workflow *should*
be in.  Crossing from ``OBSERVE`` to ``SEMI_AUTO`` or from
``SEMI_AUTO`` to ``AUTOPILOT`` must satisfy **all** required metrics
(conservative); a single breach demotes the workflow one step
(aggressive).

The gate never persists state — that is
:class:`AutonomyEngine`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from core.brain.autonomy.models import (
    AutonomyState,
    WorkflowMetrics,
    state_rank,
)


@dataclass(frozen=True, slots=True)
class PromotionThresholds:
    """Thresholds required to enter the next autonomy state.

    Attributes:
        min_sample_size: Minimum observed executions.
        min_success_rate: Minimum Wilson-adjusted success ratio.
        max_override_rate: Maximum PM-override frequency.
        max_incidents: Maximum post-action complaints (absolute).
        max_mean_latency_seconds: Upper bound on mean action latency.
    """

    min_sample_size: int
    min_success_rate: float
    max_override_rate: float
    max_incidents: int
    max_mean_latency_seconds: float


_DEFAULT_SEMI_AUTO: Final[PromotionThresholds] = PromotionThresholds(
    min_sample_size=20,
    min_success_rate=0.80,
    max_override_rate=0.15,
    max_incidents=1,
    max_mean_latency_seconds=60.0,
)
_DEFAULT_AUTOPILOT: Final[PromotionThresholds] = PromotionThresholds(
    min_sample_size=50,
    min_success_rate=0.92,
    max_override_rate=0.05,
    max_incidents=0,
    max_mean_latency_seconds=45.0,
)


class PromotionGate:
    """Policy that decides the target :class:`AutonomyState`.

    Construction accepts custom thresholds; defaults reflect Cendra's
    five-metric rule (sample size + success + override + incidents +
    latency).  Thresholds are frozen value objects, so the gate itself
    is effectively immutable.
    """

    def __init__(
        self,
        *,
        to_semi_auto: PromotionThresholds = _DEFAULT_SEMI_AUTO,
        to_autopilot: PromotionThresholds = _DEFAULT_AUTOPILOT,
    ) -> None:
        self._to_semi_auto = to_semi_auto
        self._to_autopilot = to_autopilot

    @property
    def required_metrics(self) -> tuple[str, ...]:
        """Names of the metrics the gate evaluates."""
        return (
            "sample_size",
            "success_rate",
            "override_rate",
            "incidents",
            "mean_latency_seconds",
        )

    @property
    def to_semi_auto(self) -> PromotionThresholds:
        """Thresholds required to enter ``SEMI_AUTO``."""
        return self._to_semi_auto

    @property
    def to_autopilot(self) -> PromotionThresholds:
        """Thresholds required to enter ``AUTOPILOT``."""
        return self._to_autopilot

    def thresholds_for(
        self,
        target: AutonomyState,
    ) -> PromotionThresholds | None:
        """Return the thresholds gating entry into ``target``.

        Returns ``None`` for ``OBSERVE`` (the entry state has no
        promotion threshold) so callers can branch on the boundary
        without duplicating the enum match.
        """
        if target is AutonomyState.SEMI_AUTO:
            return self._to_semi_auto
        if target is AutonomyState.AUTOPILOT:
            return self._to_autopilot
        return None

    def evaluate(
        self,
        *,
        current: AutonomyState,
        metrics: WorkflowMetrics,
    ) -> AutonomyState:
        """Return the state this workflow should be in now.

        Promotion is conservative (all criteria must pass); demotion
        is aggressive (any breach drops one tier).
        """
        target = self._target_by_metrics(metrics)
        if state_rank(target) > state_rank(current):
            return self._next_higher(current)
        if state_rank(target) < state_rank(current):
            return self._next_lower(current)
        return current

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _target_by_metrics(self, m: WorkflowMetrics) -> AutonomyState:
        if self._passes(m, self._to_autopilot):
            return AutonomyState.AUTOPILOT
        if self._passes(m, self._to_semi_auto):
            return AutonomyState.SEMI_AUTO
        return AutonomyState.OBSERVE

    @staticmethod
    def _passes(m: WorkflowMetrics, t: PromotionThresholds) -> bool:
        return (
            m.sample_size >= t.min_sample_size
            and m.success_rate >= t.min_success_rate
            and m.override_rate <= t.max_override_rate
            and m.incidents <= t.max_incidents
            and m.mean_latency_seconds <= t.max_mean_latency_seconds
        )

    @staticmethod
    def _next_higher(state: AutonomyState) -> AutonomyState:
        if state is AutonomyState.OBSERVE:
            return AutonomyState.SEMI_AUTO
        return AutonomyState.AUTOPILOT

    @staticmethod
    def _next_lower(state: AutonomyState) -> AutonomyState:
        if state is AutonomyState.AUTOPILOT:
            return AutonomyState.SEMI_AUTO
        return AutonomyState.OBSERVE
