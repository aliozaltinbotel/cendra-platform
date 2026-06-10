"""MAPIE-backed split-conformal calibrator for M1 (refinement).

Strengthens M1's abstention path with a literature-grade
implementation of split-conformal prediction provided by the
MAPIE library (Taquet et al., Quantmetry; arXiv:2207.12274 +
ongoing development).  The pure-Python
:class:`~brain_engine.abstention.split_conformal.
SplitConformalCalibrator` shipped in M24 already gives the
``ceil((n + 1)(1 - α)) / n`` empirical-quantile formulation;
this module is the *third-party-audited* alternative that:

  * exposes the same :class:`~brain_engine.abstention.
    split_conformal.ConformalSet` value object so downstream
    consumers (notably :class:`~brain_engine.abstention.
    split_conformal.ConformalAbstainGate`) work unchanged
    whichever calibrator the caller wires;
  * uses MAPIE's :class:`SplitConformalClassifier` with the
    *LAC* (Least Ambiguous set-valued Classifier) score under
    the hood;
  * passes the LLM's already-calibrated
    ``predicted_confidence`` straight through via a wrapping
    estimator (:class:`_ConfidencePassthroughClassifier`), so
    Brain Engine does not pay the cost of re-fitting a logistic
    regression on top of its own confidence scores.

Honest scope
------------

  * **Library-backed alternative**, not a replacement.  The
    pure-Python :class:`SplitConformalCalibrator` remains the
    default runtime path; this module is opt-in.  Callers that
    want the audit-grade implementation construct
    :class:`MapieSplitConformalCalibrator` instead and feed the
    same :class:`CalibrationSample` window.
  * Numerics: MAPIE uses ``numpy``; we delegate everything
    numeric to it.  Test parity (M24 vs MAPIE) is required to
    pass within a small floating-point tolerance.
  * MAPIE 1.4+ API surface
    (``SplitConformalClassifier.conformalize`` +
    ``predict_set``).  Pinned in ``requirements.txt``.

Reference: Vovk, Gammerman, Shafer (2005); Romano, Sesia,
Candès (2020); MAPIE docs https://mapie.readthedocs.io.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, cast

import numpy as np
import structlog
from mapie.classification import SplitConformalClassifier
from sklearn.base import BaseEstimator, ClassifierMixin

from brain_engine.abstention.models import (
    AbstentionVerdict,
    CalibrationSample,
)
from brain_engine.abstention.split_conformal import (
    DEFAULT_ALPHA_CONFORMAL,
    DEFAULT_MIN_CALIBRATION,
    ConformalAbstainResult,
    ConformalLabel,
    ConformalSet,
)


__all__ = [
    "DEFAULT_MAPIE_CONFORMITY_SCORE",
    "MapieAbstainGate",
    "MapieSplitConformalCalibrator",
]


DEFAULT_MAPIE_CONFORMITY_SCORE: Final[str] = "lac"


logger = structlog.get_logger(__name__)


class _ConfidencePassthroughClassifier(BaseEstimator, ClassifierMixin):
    """Sklearn-shaped classifier whose probability *is* the input.

    Brain Engine's upstream LLM produces a calibrated
    ``predicted_confidence`` in ``[0, 1]`` that we want MAPIE to
    interpret as ``P(success)`` directly.  This adapter satisfies
    sklearn's ``ClassifierMixin`` interface with the trivial
    mapping ``predict_proba([[c]]) → [[1 - c, c]]`` so MAPIE can
    plug it in without us paying for a separate logistic
    regression fit.

    The class is *not* a public API — the symbol starts with an
    underscore precisely so callers do not accidentally rely on
    it.  We re-export only :class:`MapieSplitConformalCalibrator`.
    """

    classes_: np.ndarray

    def __init__(self) -> None:
        self.classes_ = np.array([0, 1])

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
    ) -> _ConfidencePassthroughClassifier:
        """No-op fit; classes are fixed at ``[0, 1]``."""
        unique = np.unique(y)
        if len(unique) == 2:
            self.classes_ = unique
        else:
            self.classes_ = np.array([0, 1])
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Argmax over the pass-through probabilities."""
        confidences = np.asarray(X, dtype=float).ravel()
        return (confidences >= 0.5).astype(int)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return ``[[1 - c, c] for c in X[:, 0]]``."""
        confidences = np.asarray(X, dtype=float).ravel()
        return np.column_stack(
            [1.0 - confidences, confidences],
        )

    def __sklearn_is_fitted__(self) -> bool:
        """MAPIE asks sklearn whether the estimator is fitted."""
        return True


def _samples_to_arrays(
    samples: Sequence[CalibrationSample],
) -> tuple[np.ndarray, np.ndarray]:
    """Project ``samples`` into ``(X, y)`` MAPIE expects."""
    if not samples:
        return (
            np.empty((0, 1), dtype=float),
            np.empty((0,), dtype=int),
        )
    X = np.array(
        [[s.predicted_confidence] for s in samples],
        dtype=float,
    )
    y = np.array(
        [1 if s.actual_success else 0 for s in samples],
        dtype=int,
    )
    return X, y


class MapieSplitConformalCalibrator:
    """MAPIE-backed split-conformal calibrator.

    Same :meth:`predict_set` surface as the pure-Python
    :class:`~brain_engine.abstention.split_conformal.
    SplitConformalCalibrator`; under the hood it delegates the
    quantile + set construction to MAPIE's
    :class:`SplitConformalClassifier`.

    The calibrator is stateless across :meth:`predict_set` calls
    — every call rebuilds the MAPIE estimator with the supplied
    window.  That keeps the surface symmetric with the pure-
    Python sibling and avoids accumulating stale calibration
    state when the caller swaps tools or windows.
    """

    def __init__(
        self,
        *,
        alpha: float = DEFAULT_ALPHA_CONFORMAL,
        conformity_score: str = DEFAULT_MAPIE_CONFORMITY_SCORE,
        random_state: int = 0,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0.0, 1.0)")
        if not conformity_score:
            raise ValueError(
                "conformity_score must be non-empty"
            )
        self._alpha = alpha
        self._conformity_score = conformity_score
        self._random_state = random_state
        self._log = logger.bind(component="mapie_calibrator")

    @property
    def alpha(self) -> float:
        """Return the configured target miscoverage rate."""
        return self._alpha

    def predict_set(
        self,
        *,
        samples: Sequence[CalibrationSample],
        model_confidence: float,
    ) -> ConformalSet:
        """Return the conformal label set for ``model_confidence``.

        Empty / degenerate windows fall back to the unit set
        ``{SUCCESS, FAILURE}`` so the downstream gate maps to
        ``ABSTAIN`` upstream rather than producing
        false-confident output.
        """
        if not 0.0 <= model_confidence <= 1.0:
            raise ValueError(
                "model_confidence must be in [0.0, 1.0]"
            )
        X_calib, y_calib = _samples_to_arrays(samples)
        unique_labels = set(np.unique(y_calib).tolist())
        if (
            X_calib.shape[0] == 0
            or unique_labels != {0, 1}
        ):
            return ConformalSet(
                labels=frozenset(
                    {
                        ConformalLabel.SUCCESS,
                        ConformalLabel.FAILURE,
                    }
                ),
                quantile=1.0,
                calibration_size=int(X_calib.shape[0]),
                alpha=self._alpha,
            )
        scc = self._build_classifier()
        scc.conformalize(X_calib, y_calib)
        _, y_set = scc.predict_set(
            np.array([[model_confidence]], dtype=float),
        )
        accepted = self._set_from_mapie(y_set)
        return ConformalSet(
            labels=accepted,
            quantile=float(1.0 - self._alpha),
            calibration_size=int(X_calib.shape[0]),
            alpha=self._alpha,
        )

    # ── internals ─────────────────────────────────────────── #

    def _build_classifier(self) -> SplitConformalClassifier:
        """Fresh MAPIE classifier with the pass-through estimator."""
        estimator = _ConfidencePassthroughClassifier()
        estimator.fit(
            np.array([[0.0], [1.0]], dtype=float),
            np.array([0, 1], dtype=int),
        )
        return SplitConformalClassifier(
            estimator=estimator,
            confidence_level=1.0 - self._alpha,
            conformity_score=self._conformity_score,
            prefit=True,
            random_state=self._random_state,
        )

    def _set_from_mapie(
        self,
        y_set: np.ndarray,
    ) -> frozenset[ConformalLabel]:
        """Map MAPIE's bool array to our :class:`ConformalLabel` set.

        ``y_set`` shape per MAPIE 1.4 is ``(n_samples, n_classes,
        n_confidence_levels)``.  We pass exactly one sample and
        one confidence level, so we slice ``[0, :, 0]``.
        """
        flags = y_set[0, :, 0]
        accepted: set[ConformalLabel] = set()
        if bool(flags[0]):
            accepted.add(ConformalLabel.FAILURE)
        if bool(flags[1]):
            accepted.add(ConformalLabel.SUCCESS)
        return frozenset(accepted)


class MapieAbstainGate:
    """Map :class:`ConformalSet` from MAPIE to a typed abstention verdict.

    Surface mirrors :class:`~brain_engine.abstention.
    split_conformal.ConformalAbstainGate` so callers can swap
    implementations without changing audit-log shape.
    """

    def __init__(
        self,
        *,
        calibrator: MapieSplitConformalCalibrator,
        min_calibration: int = DEFAULT_MIN_CALIBRATION,
    ) -> None:
        if min_calibration < 1:
            raise ValueError(
                "min_calibration must be positive"
            )
        self._calibrator = calibrator
        self._min_calibration = min_calibration
        self._log = logger.bind(component="mapie_abstain_gate")

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
                    alpha=self._calibrator.alpha,
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
                    f"mapie singleton {{{label.value}}}; "
                    f"alpha={self._calibrator.alpha:.3f}"
                ),
            )
        if conformal.is_empty:
            return ConformalAbstainResult(
                verdict=AbstentionVerdict.ABSTAIN,
                conformal_set=conformal,
                model_confidence=model_confidence,
                rationale=(
                    "mapie empty conformal set; coverage gate "
                    "fails in both directions"
                ),
            )
        return ConformalAbstainResult(
            verdict=AbstentionVerdict.ABSTAIN,
            conformal_set=conformal,
            model_confidence=model_confidence,
            rationale=(
                "mapie ambiguous conformal set "
                "{success, failure}; both labels survive"
            ),
        )


_ = cast
