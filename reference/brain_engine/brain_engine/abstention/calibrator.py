"""Conformal calibrator combining Wilson LB + coverage threshold.

Given a sliding-window calibration set of (predicted_confidence,
actual_success) pairs per tool, the calibrator derives two bounds
that the abstention gate consults:

1. **Wilson lower bound** on the empirical success rate
   (:func:`brain_engine.patterns.wilson.wilson_lower_bound`).
2. **Conformal threshold** on the predicted-confidence distribution
   *of failed predictions* — the alpha-quantile of those
   confidences.  If a new call's confidence is at or below this
   threshold, the model is operating in a region the calibration
   window says it has been wrong about.

The pair is the architectural fact Moat #1 is staked on: no
published frontier system pairs bi-temporal lifecycle (already in
:mod:`brain_engine.patterns.postgres_rule_store`) + Wilson + a
conformal coverage gate in one runtime path.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import quantiles
from typing import Final

from brain_engine.abstention.models import CalibrationSample
from brain_engine.abstention.protocols import CalibrationStore
from brain_engine.patterns.wilson import (
    Z_95,
    wilson_lower_bound,
)


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_MIN_SAMPLES",
    "ConformalCalibrator",
]


DEFAULT_MIN_SAMPLES: Final[int] = 30
DEFAULT_ALPHA: Final[float] = 0.10


class ConformalCalibrator:
    """Derive Wilson + conformal bounds from a calibration store.

    The calibrator is stateless — it owns no calibration data; it
    reads from the provided :class:`CalibrationStore`.  Recording
    new outcomes is delegated to :meth:`record` for caller
    convenience.
    """

    def __init__(
        self,
        *,
        store: CalibrationStore,
        z: float = Z_95,
    ) -> None:
        if z <= 0.0:
            raise ValueError("z must be positive")
        self._store = store
        self._z = z

    def record(self, sample: CalibrationSample) -> None:
        """Append a new outcome to the underlying store."""
        self._store.record(sample)

    def sample_size(self, tool_id: str) -> int:
        """Return the number of samples currently in the window."""
        return len(self._store.samples_for(tool_id))

    def wilson_lb(self, tool_id: str) -> float:
        """Wilson-score lower bound on the empirical success rate.

        Returns ``0.0`` when the window is empty (no evidence — no
        confidence).
        """
        samples = self._store.samples_for(tool_id)
        if not samples:
            return 0.0
        successes = sum(1 for s in samples if s.actual_success)
        return wilson_lower_bound(
            successes=successes,
            trials=len(samples),
            z=self._z,
        )

    def conformal_threshold(
        self,
        tool_id: str,
        *,
        alpha: float = DEFAULT_ALPHA,
    ) -> float | None:
        """Return the alpha-quantile of confidences when failed.

        The conformal coverage idea: take the predicted confidences
        of all FAILED calls in the window and pick the
        alpha-quantile.  At inference, if the model reports a
        confidence at or below this number, it is in a region
        historically associated with errors and the gate abstains.

        Returns ``None`` when no failed samples are available — the
        gate then falls back to the Wilson bound alone.
        """
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0.0, 1.0)")
        samples = self._store.samples_for(tool_id)
        failed_confidences = [
            s.predicted_confidence for s in samples
            if not s.actual_success
        ]
        if not failed_confidences:
            return None
        if len(failed_confidences) == 1:
            return float(failed_confidences[0])
        return _quantile(failed_confidences, alpha)


def _quantile(values: Sequence[float], q: float) -> float:
    """Return the ``q``-quantile (linear interpolation).

    Uses :func:`statistics.quantiles` with ``n=100`` for a 1 %
    resolution; clamps the result to the closed range of the
    input distribution so out-of-bound interpolation is avoided.
    """
    if not values:
        raise ValueError("values must be non-empty")
    if len(values) == 1:
        return float(values[0])
    cuts = quantiles(values, n=100, method="inclusive")
    index = max(0, min(len(cuts) - 1, int(round(q * 100)) - 1))
    candidate = cuts[index]
    return float(max(min(candidate, max(values)), min(values)))
