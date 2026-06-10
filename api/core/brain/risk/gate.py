"""Runtime gate: refuse actions whose CVaR exceeds policy.

The gate is the entry point the action pipeline consults *before*
any side-effecting tool-call carrying a non-trivial loss
distribution (refunds, vendor payouts, dynamic-pricing changes).
It returns a structured :class:`RiskGateDecision` the audit log
records so the regulator can replay both the rationale and the
underlying numbers.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from core.brain.risk.cvar import DEFAULT_ALPHA, compute_risk
from core.brain.risk.models import (
    OutcomeSample,
    RiskEstimate,
    RiskVerdict,
)

__all__ = [
    "DEFAULT_CVAR_THRESHOLD",
    "DEFAULT_MIN_SAMPLES",
    "RiskGate",
    "RiskGateDecision",
]


DEFAULT_CVAR_THRESHOLD: Final[float] = 100.0
DEFAULT_MIN_SAMPLES: Final[int] = 32


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskGateDecision:
    """Structured outcome of a gate evaluation."""

    verdict: RiskVerdict
    estimate: RiskEstimate | None
    threshold: float
    rationale: str


class RiskGate:
    """Decide whether to proceed with a candidate action.

    Construction takes the policy thresholds — the tail probability
    ``alpha``, the per-action CVaR cut-off ``cvar_threshold``, and
    the minimum number of samples below which the gate refuses to
    decide either way (returns :attr:`RiskVerdict.INSUFFICIENT_DATA`).
    """

    def __init__(
        self,
        *,
        cvar_threshold: float = DEFAULT_CVAR_THRESHOLD,
        alpha: float = DEFAULT_ALPHA,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0.0, 1.0)")
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if cvar_threshold != cvar_threshold:
            raise ValueError("cvar_threshold must be finite")
        self._cvar_threshold = cvar_threshold
        self._alpha = alpha
        self._min_samples = min_samples

    def decide(
        self,
        samples: Sequence[OutcomeSample],
    ) -> RiskGateDecision:
        """Evaluate ``samples`` and return a verdict."""
        if len(samples) < self._min_samples:
            return RiskGateDecision(
                verdict=RiskVerdict.INSUFFICIENT_DATA,
                estimate=None,
                threshold=self._cvar_threshold,
                rationale=(f"only {len(samples)} sample(s); min_samples={self._min_samples}"),
            )
        estimate = compute_risk(samples, alpha=self._alpha)
        if estimate.cvar > self._cvar_threshold:
            logger.info(
                "risk.cvar_above_threshold cvar=%s threshold=%s alpha=%s",
                estimate.cvar,
                self._cvar_threshold,
                self._alpha,
            )
            return RiskGateDecision(
                verdict=RiskVerdict.ABSTAIN,
                estimate=estimate,
                threshold=self._cvar_threshold,
                rationale=(
                    f"cvar={estimate.cvar:.3f} > threshold={self._cvar_threshold:.3f} (alpha={self._alpha:.2f})"
                ),
            )
        return RiskGateDecision(
            verdict=RiskVerdict.PROCEED,
            estimate=estimate,
            threshold=self._cvar_threshold,
            rationale=(f"cvar={estimate.cvar:.3f} ≤ threshold={self._cvar_threshold:.3f}; ev={estimate.ev:.3f}"),
        )
