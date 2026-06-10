"""Value objects for the EV / CVaR risk layer (Moat #9)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "OutcomeSample",
    "RiskEstimate",
    "RiskVerdict",
]


class RiskVerdict(StrEnum):
    """Three-valued result from :class:`RiskGate`."""

    PROCEED = "proceed"
    ABSTAIN = "abstain"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    """One scenario sample for a candidate action.

    Attributes:
        loss: Realised loss in this scenario.  Positive numbers
            denote losses; negative numbers denote gains.  Units
            are caller-defined (EUR, minutes, review-points, ...).
        weight: Probability mass for this scenario; must be
            non-negative.  When the sample set is unweighted
            (Monte-Carlo draws), set every weight to ``1.0`` —
            the calculator normalises.
    """

    loss: float
    weight: float = 1.0

    def __post_init__(self) -> None:
        if self.loss != self.loss or self.loss in (
            float("inf"),
            float("-inf"),
        ):
            raise ValueError("loss must be finite")
        if self.weight < 0.0:
            raise ValueError("weight must be non-negative")
        if self.weight != self.weight or self.weight == float(
            "inf",
        ):
            raise ValueError("weight must be finite")


@dataclass(frozen=True, slots=True)
class RiskEstimate:
    """Summary statistics for a candidate action.

    Attributes:
        sample_size: Number of weighted samples used.
        ev: Expected value of the loss (probability-weighted mean).
        cvar: Conditional Value-at-Risk at the configured ``alpha``
            tail (mean of the worst ``alpha`` fraction of losses).
        alpha: Tail probability used to compute :attr:`cvar`.
        var: Value-at-Risk threshold — the ``(1-alpha)``-quantile
            of the loss distribution (the cut-off below which
            :attr:`cvar` averages).
    """

    sample_size: int
    ev: float
    cvar: float
    alpha: float
    var: float


def _validate_samples(samples: Sequence[OutcomeSample]) -> None:
    """Fail fast when the sample set is empty."""
    if not samples:
        raise ValueError("at least one sample required")
