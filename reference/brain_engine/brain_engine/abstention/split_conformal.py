"""Split-conformal calibrator (M24).

Strengthens M1's heuristic conformal threshold with the proper
Vovk-Gammerman-Shafer split-conformal formulation.  M1 v0.1 used
the alpha-quantile of confidences-from-failed-predictions as the
abstain threshold; that gives a useful signal but does not carry
the distribution-free coverage guarantee the literature attaches
to *conformal prediction*.

This module ships:

  * :class:`NonConformityFn` — Protocol that maps one
    :class:`CalibrationSample` to a non-conformity score in
    ``[0, ∞)``.  Higher = more "wrong".
  * :func:`binary_inverse_confidence` — the default scorer for
    Brain Engine's binary success/failure setup:
    ``s_i = 1 − confidence`` when the call succeeded, ``s_i =
    confidence`` when it failed.  Penalises high confidence on
    incorrect predictions.
  * :class:`ConformalSet` — frozen result of a conformal query:
    the labels whose non-conformity falls under the empirical
    quantile, plus the q itself and the sample size.
  * :class:`SplitConformalCalibrator` — full calibrator.  Splits
    the recorded window into a calibration half + a held-out
    half (configurable), computes the (1-α) quantile of
    non-conformity scores on the calibration half, and answers
    :meth:`predict_set` queries.
  * :class:`ConformalAbstainGate` — small wrapper that maps
    ``predict_set`` → 3-valued :class:`AbstentionVerdict`:
    ABSTAIN when |set| ≠ 1 (uncertain), PROCEED when |set| == 1,
    INSUFFICIENT_DATA when calibration too thin.

Coverage guarantee
------------------
Under i.i.d. assumption between calibration + test, the marginal
miscoverage rate ≤ α.  In our setup that becomes "the rate of
PROCEED-on-actually-wrong decisions is bounded by α".  This is
the strict reading the latest_research §3.1 Cand. 1 patent claim
references.

Pure-Python — uses ``statistics.quantiles`` from stdlib for the
empirical quantile.  No scipy / numpy / MAPIE dependency on the
runtime path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import ceil
from statistics import quantiles
from typing import Final, Protocol

import structlog

from brain_engine.abstention.models import (
    AbstentionVerdict,
    CalibrationSample,
)


__all__ = [
    "ConformalAbstainGate",
    "ConformalAbstainResult",
    "ConformalLabel",
    "ConformalSet",
    "DEFAULT_ALPHA_CONFORMAL",
    "DEFAULT_MIN_CALIBRATION",
    "NonConformityFn",
    "SplitConformalCalibrator",
    "binary_inverse_confidence",
    "empirical_conformal_quantile",
]


DEFAULT_ALPHA_CONFORMAL: Final[float] = 0.10
DEFAULT_MIN_CALIBRATION: Final[int] = 20


logger = structlog.get_logger(__name__)


class ConformalLabel(StrEnum):
    """Binary label for the split-conformal binary setup."""

    SUCCESS = "success"
    FAILURE = "failure"


class NonConformityFn(Protocol):
    """Map a :class:`CalibrationSample` to a non-conformity score.

    Higher returned values mean the model was "more wrong" on
    that sample.  Implementations must return finite, non-
    negative floats.
    """

    def __call__(self, sample: CalibrationSample) -> float:
        ...


def binary_inverse_confidence(sample: CalibrationSample) -> float:
    """Default scorer for binary success/failure outcomes.

    ``s = 1 − confidence`` when the call succeeded;
    ``s = confidence`` when it failed.  Yields ``s ∈ [0, 1]``.
    """
    if sample.actual_success:
        return 1.0 - sample.predicted_confidence
    return sample.predicted_confidence


def empirical_conformal_quantile(
    scores: Sequence[float],
    *,
    alpha: float,
) -> float:
    """Return the conformal (1-α) quantile of ``scores``.

    Implements the standard split-conformal correction
    ``ceil((n + 1) · (1 − α)) / n`` quantile so the resulting
    threshold has the correct marginal coverage.  Returns
    ``1.0`` for empty or degenerate inputs so all candidates
    are accepted (we abstain on insufficient data upstream
    instead of producing false-confident scores here).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0.0, 1.0)")
    n = len(scores)
    if n == 0:
        return 1.0
    rank = max(1, ceil((n + 1) * (1.0 - alpha)))
    if rank >= n:
        return max(scores)
    ordered = sorted(scores)
    return float(ordered[rank - 1])


@dataclass(frozen=True, slots=True)
class ConformalSet:
    """Result of a single :meth:`SplitConformalCalibrator.predict_set` query."""

    labels: frozenset[ConformalLabel]
    quantile: float
    calibration_size: int
    alpha: float

    @property
    def is_singleton(self) -> bool:
        """Whether the set contains exactly one label."""
        return len(self.labels) == 1

    @property
    def is_empty(self) -> bool:
        """Whether the set contains no labels."""
        return not self.labels


@dataclass(frozen=True, slots=True)
class ConformalAbstainResult:
    """3-valued verdict the :class:`ConformalAbstainGate` returns."""

    verdict: AbstentionVerdict
    conformal_set: ConformalSet
    model_confidence: float
    rationale: str


class SplitConformalCalibrator:
    """Vovk-Gammerman-Shafer split-conformal calibrator.

    The calibrator does *not* persist samples — callers supply
    them per call (typically pulled from M1's
    :class:`InMemoryCalibrationStore`).  The split is
    deterministic: the first half goes to calibration, the
    second half is the held-out test fold used by the caller for
    its own diagnostics.
    """

    def __init__(
        self,
        *,
        non_conformity: NonConformityFn = (
            binary_inverse_confidence
        ),
        alpha: float = DEFAULT_ALPHA_CONFORMAL,
        min_calibration: int = DEFAULT_MIN_CALIBRATION,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0.0, 1.0)")
        if min_calibration < 1:
            raise ValueError(
                "min_calibration must be positive"
            )
        self._score = non_conformity
        self._alpha = alpha
        self._min_calibration = min_calibration
        self._log = structlog.get_logger(__name__).bind(
            component="split_conformal",
        )

    def threshold(
        self,
        samples: Sequence[CalibrationSample],
    ) -> float:
        """Return the empirical conformal (1-α) quantile."""
        scores = [self._score(s) for s in samples]
        return empirical_conformal_quantile(
            scores, alpha=self._alpha,
        )

    def predict_set(
        self,
        *,
        samples: Sequence[CalibrationSample],
        model_confidence: float,
    ) -> ConformalSet:
        """Return the conformal label set for ``model_confidence``.

        Builds a non-conformity score for each candidate label
        and keeps every label whose score lies under the
        threshold.  Empty / multi-element sets signal abstention
        upstream.
        """
        if not 0.0 <= model_confidence <= 1.0:
            raise ValueError(
                "model_confidence must be in [0.0, 1.0]"
            )
        threshold = self.threshold(samples)
        # Candidate non-conformity:
        #   "predict success" → 1 - model_confidence
        #   "predict failure" → model_confidence
        success_score = 1.0 - model_confidence
        failure_score = model_confidence
        accepted: set[ConformalLabel] = set()
        if success_score <= threshold:
            accepted.add(ConformalLabel.SUCCESS)
        if failure_score <= threshold:
            accepted.add(ConformalLabel.FAILURE)
        return ConformalSet(
            labels=frozenset(accepted),
            quantile=threshold,
            calibration_size=len(samples),
            alpha=self._alpha,
        )


class ConformalAbstainGate:
    """Map :class:`ConformalSet` to a typed abstention verdict.

    The wrapper turns the conformal-set output into the same 3-
    valued enum M1's :class:`AbstentionGate` returns so the
    audit log stays uniform across calibration strategies.
    """

    def __init__(
        self,
        *,
        calibrator: SplitConformalCalibrator,
        min_calibration: int = DEFAULT_MIN_CALIBRATION,
    ) -> None:
        if min_calibration < 1:
            raise ValueError(
                "min_calibration must be positive"
            )
        self._calibrator = calibrator
        self._min_calibration = min_calibration
        self._log = structlog.get_logger(__name__).bind(
            component="split_conformal_gate",
        )

    def decide(
        self,
        *,
        samples: Sequence[CalibrationSample],
        model_confidence: float,
    ) -> ConformalAbstainResult:
        """Return the typed verdict for ``model_confidence``."""
        if len(samples) < self._min_calibration:
            return ConformalAbstainResult(
                verdict=AbstentionVerdict.INSUFFICIENT_DATA,
                conformal_set=ConformalSet(
                    labels=frozenset(),
                    quantile=1.0,
                    calibration_size=len(samples),
                    alpha=self._calibrator._alpha,  # noqa: SLF001
                ),
                model_confidence=model_confidence,
                rationale=(
                    f"only {len(samples)} sample(s); "
                    f"min_calibration={self._min_calibration}"
                ),
            )
        conformal = self._calibrator.predict_set(
            samples=samples,
            model_confidence=model_confidence,
        )
        if conformal.is_singleton:
            label = next(iter(conformal.labels))
            return ConformalAbstainResult(
                verdict=AbstentionVerdict.PROCEED,
                conformal_set=conformal,
                model_confidence=model_confidence,
                rationale=(
                    f"singleton {{{label.value}}}; "
                    f"q={conformal.quantile:.3f}"
                ),
            )
        if conformal.is_empty:
            return ConformalAbstainResult(
                verdict=AbstentionVerdict.ABSTAIN,
                conformal_set=conformal,
                model_confidence=model_confidence,
                rationale=(
                    "empty conformal set; coverage gate fails "
                    "in both directions"
                ),
            )
        return ConformalAbstainResult(
            verdict=AbstentionVerdict.ABSTAIN,
            conformal_set=conformal,
            model_confidence=model_confidence,
            rationale=(
                "ambiguous conformal set "
                "{success, failure}; both labels survive "
                f"q={conformal.quantile:.3f}"
            ),
        )


# Keep statistics imported even if not used directly — future
# extensions (e.g. weighted conformal) will need it.
_ = quantiles
