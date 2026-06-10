"""Abstention gate — entry point for tool-call decisions.

The gate combines the two bounds derived by
:class:`ConformalCalibrator` (Wilson LB + conformal threshold) into
a structured :class:`AbstentionDecision` that the runtime can record
into the audit log and feed into the autonomy pipeline.

The decision rule:

1. If the calibration window has fewer than ``min_samples``
   observations, return :attr:`AbstentionVerdict.INSUFFICIENT_DATA`
   so the caller can fall back to model confidence alone or
   escalate to a human.
2. If the Wilson LB on the empirical success rate is below
   ``wilson_threshold``, return :attr:`AbstentionVerdict.ABSTAIN`.
3. If the conformal threshold exists *and* the model confidence is
   at or below it, return :attr:`AbstentionVerdict.ABSTAIN`.
4. Otherwise, return :attr:`AbstentionVerdict.PROCEED`.

The constants below match the ``Moat #1`` plan in the roadmap:
``min_samples = 30`` to keep noise-driven false abstentions low,
``wilson_threshold = 0.6`` aligned with autonomy tier L2,
``alpha = 0.10`` for a 90 %-coverage conformal guarantee.
"""

from __future__ import annotations

from typing import Final

import structlog

from brain_engine.abstention.calibrator import (
    DEFAULT_ALPHA,
    DEFAULT_MIN_SAMPLES,
    ConformalCalibrator,
)
from brain_engine.abstention.models import (
    AbstentionDecision,
    AbstentionVerdict,
)


__all__ = [
    "DEFAULT_WILSON_THRESHOLD",
    "AbstentionGate",
]


DEFAULT_WILSON_THRESHOLD: Final[float] = 0.60


logger = structlog.get_logger(__name__)


class AbstentionGate:
    """Decide whether a tool call should proceed.

    The gate composes the calibrator-derived bounds with policy
    thresholds.  It is read-only with respect to the calibration
    store — recording new outcomes after a call is the caller's
    responsibility (typically wired through the audit pipeline).
    """

    def __init__(
        self,
        *,
        calibrator: ConformalCalibrator,
        wilson_threshold: float = DEFAULT_WILSON_THRESHOLD,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        alpha: float = DEFAULT_ALPHA,
    ) -> None:
        if not 0.0 <= wilson_threshold <= 1.0:
            raise ValueError(
                "wilson_threshold must be in [0.0, 1.0]"
            )
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0.0, 1.0)")
        self._calibrator = calibrator
        self._wilson_threshold = wilson_threshold
        self._min_samples = min_samples
        self._alpha = alpha
        self._log = logger.bind(component="abstention_gate")

    def decide(
        self,
        *,
        tool_id: str,
        model_confidence: float,
    ) -> AbstentionDecision:
        """Return an :class:`AbstentionDecision` for one tool call."""
        if not 0.0 <= model_confidence <= 1.0:
            raise ValueError(
                "model_confidence must be in [0.0, 1.0]"
            )
        sample_size = self._calibrator.sample_size(tool_id)
        wilson_lb = self._calibrator.wilson_lb(tool_id)
        conformal = self._calibrator.conformal_threshold(
            tool_id,
            alpha=self._alpha,
        )
        if sample_size < self._min_samples:
            return self._insufficient(
                tool_id=tool_id,
                model_confidence=model_confidence,
                wilson_lb=wilson_lb,
                sample_size=sample_size,
                conformal=conformal,
            )
        if wilson_lb < self._wilson_threshold:
            return self._abstain_wilson(
                tool_id=tool_id,
                model_confidence=model_confidence,
                wilson_lb=wilson_lb,
                sample_size=sample_size,
                conformal=conformal,
            )
        if (
            conformal is not None
            and model_confidence <= conformal
        ):
            return self._abstain_conformal(
                tool_id=tool_id,
                model_confidence=model_confidence,
                wilson_lb=wilson_lb,
                sample_size=sample_size,
                conformal=conformal,
            )
        return self._proceed(
            tool_id=tool_id,
            model_confidence=model_confidence,
            wilson_lb=wilson_lb,
            sample_size=sample_size,
            conformal=conformal,
        )

    # ── Verdict constructors ──────────────────────────────────── #

    def _insufficient(
        self,
        *,
        tool_id: str,
        model_confidence: float,
        wilson_lb: float,
        sample_size: int,
        conformal: float | None,
    ) -> AbstentionDecision:
        rationale = (
            f"only {sample_size} sample(s); "
            f"min_samples={self._min_samples}"
        )
        self._log.info(
            "abstention.insufficient",
            tool_id=tool_id,
            sample_size=sample_size,
        )
        return AbstentionDecision(
            tool_id=tool_id,
            verdict=AbstentionVerdict.INSUFFICIENT_DATA,
            model_confidence=model_confidence,
            wilson_lb=wilson_lb,
            sample_size=sample_size,
            conformal_threshold=conformal,
            rationale=rationale,
        )

    def _abstain_wilson(
        self,
        *,
        tool_id: str,
        model_confidence: float,
        wilson_lb: float,
        sample_size: int,
        conformal: float | None,
    ) -> AbstentionDecision:
        rationale = (
            f"wilson_lb={wilson_lb:.3f} < "
            f"threshold={self._wilson_threshold:.2f}"
        )
        self._log.info(
            "abstention.wilson_below_threshold",
            tool_id=tool_id,
            wilson_lb=wilson_lb,
            threshold=self._wilson_threshold,
        )
        return AbstentionDecision(
            tool_id=tool_id,
            verdict=AbstentionVerdict.ABSTAIN,
            model_confidence=model_confidence,
            wilson_lb=wilson_lb,
            sample_size=sample_size,
            conformal_threshold=conformal,
            rationale=rationale,
        )

    def _abstain_conformal(
        self,
        *,
        tool_id: str,
        model_confidence: float,
        wilson_lb: float,
        sample_size: int,
        conformal: float | None,
    ) -> AbstentionDecision:
        rationale = (
            f"model_confidence={model_confidence:.3f} <= "
            f"conformal_threshold={conformal:.3f} "
            f"(alpha={self._alpha:.2f})"
        )
        self._log.info(
            "abstention.conformal_below_threshold",
            tool_id=tool_id,
            model_confidence=model_confidence,
            conformal=conformal,
            alpha=self._alpha,
        )
        return AbstentionDecision(
            tool_id=tool_id,
            verdict=AbstentionVerdict.ABSTAIN,
            model_confidence=model_confidence,
            wilson_lb=wilson_lb,
            sample_size=sample_size,
            conformal_threshold=conformal,
            rationale=rationale,
        )

    def _proceed(
        self,
        *,
        tool_id: str,
        model_confidence: float,
        wilson_lb: float,
        sample_size: int,
        conformal: float | None,
    ) -> AbstentionDecision:
        rationale = (
            f"wilson_lb={wilson_lb:.3f} >= "
            f"threshold={self._wilson_threshold:.2f}; "
            f"model_confidence above conformal cutoff"
        )
        return AbstentionDecision(
            tool_id=tool_id,
            verdict=AbstentionVerdict.PROCEED,
            model_confidence=model_confidence,
            wilson_lb=wilson_lb,
            sample_size=sample_size,
            conformal_threshold=conformal,
            rationale=rationale,
        )
