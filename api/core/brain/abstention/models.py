"""Value objects for the abstention layer.

The objects here are deliberately simple — frozen dataclasses with
slots — because they cross every layer (calibrator → gate → audit
log) and must serialise cleanly without mutation surprises.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

__all__ = [
    "AbstentionDecision",
    "AbstentionVerdict",
    "CalibrationSample",
]


class AbstentionVerdict(StrEnum):
    """Three-valued result of an abstention gate query.

    - ``PROCEED``: both the Wilson lower bound and the conformal
      threshold permit the call.
    - ``ABSTAIN``: at least one bound rejects the call.
    - ``INSUFFICIENT_DATA``: not enough calibration samples yet to
      compute either bound — the caller decides whether to fall
      back to model confidence alone or escalate to a human.
    """

    PROCEED = "proceed"
    ABSTAIN = "abstain"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One historical observation for a tool's calibration window.

    Attributes:
        tool_id: Stable identifier of the tool / action class
            (e.g. ``"price_recommend"``, ``"send_message"``).
        predicted_confidence: Model-reported confidence at the time
            of the call, in the closed interval ``[0.0, 1.0]``.
        actual_success: Post-hoc ground truth — ``True`` when the
            call's outcome was correct/successful, ``False``
            otherwise.
        recorded_at: UTC timestamp of when the sample was recorded;
            defaults to ``datetime.now(timezone.utc)``.
    """

    tool_id: str
    predicted_confidence: float
    actual_success: bool
    recorded_at: datetime

    def __post_init__(self) -> None:
        """Validate ``predicted_confidence`` is a probability."""
        if not 0.0 <= self.predicted_confidence <= 1.0:
            raise ValueError(f"predicted_confidence must be in [0.0, 1.0], got {self.predicted_confidence!r}")

    @classmethod
    def now(
        cls,
        *,
        tool_id: str,
        predicted_confidence: float,
        actual_success: bool,
    ) -> CalibrationSample:
        """Build a sample stamped at the current UTC instant."""
        return cls(
            tool_id=tool_id,
            predicted_confidence=predicted_confidence,
            actual_success=actual_success,
            recorded_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class AbstentionDecision:
    """Structured verdict returned by the abstention gate.

    Carries every input the audit log needs to explain *why* the
    call proceeded or abstained.  Downstream consumers should treat
    this object as immutable evidence — the planner / autonomy
    pipeline references it, never rewrites it.

    Attributes:
        tool_id: Tool the decision concerns.
        verdict: One of :class:`AbstentionVerdict`.
        model_confidence: Confidence the caller asked the gate to
            evaluate; copied here for the audit trail.
        wilson_lb: Wilson-score lower bound on empirical success
            rate at the configured ``z`` confidence level.
        sample_size: Number of calibration samples backing the
            bounds.
        conformal_threshold: Conformal coverage threshold (alpha-
            quantile of confidences when the tool failed); ``None``
            when no failed samples have been observed yet.
        rationale: One-line plain-English reason for the verdict —
            consumed by the audit log and the V2 UI.
    """

    tool_id: str
    verdict: AbstentionVerdict
    model_confidence: float
    wilson_lb: float
    sample_size: int
    conformal_threshold: float | None
    rationale: str
