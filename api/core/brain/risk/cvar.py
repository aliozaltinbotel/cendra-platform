"""Expected value + Conditional-Value-at-Risk calculator.

CVaR_α is the mean of the worst ``α`` fraction of outcomes — the
right metric when a regulator asks "how bad does this action get
in the tail?"  EV alone hides tail risk; VaR (the threshold
itself) hides post-threshold magnitude.  CVaR captures both.

Implementation is pure-Python with weighted samples — no NumPy.
Inputs are :class:`OutcomeSample` records; outputs are a single
:class:`RiskEstimate`.

References:
    Rockafellar / Uryasev (2000).  *Optimization of conditional
    value-at-risk*.  Journal of Risk 2(3), 21–41.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from core.brain.risk.models import (
    OutcomeSample,
    RiskEstimate,
    _validate_samples,
)

__all__ = [
    "DEFAULT_ALPHA",
    "compute_risk",
]


DEFAULT_ALPHA: Final[float] = 0.05


def compute_risk(
    samples: Sequence[OutcomeSample],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> RiskEstimate:
    """Return EV + VaR + CVaR for ``samples`` at the given tail.

    Args:
        samples: Outcome samples for one candidate action.
        alpha: Tail probability in the open interval ``(0, 1)``.

    Returns:
        A populated :class:`RiskEstimate`.
    """
    _validate_samples(samples)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0.0, 1.0)")
    total_weight = sum(s.weight for s in samples)
    if total_weight <= 0.0:
        raise ValueError("total weight must be positive")
    ev = sum(s.loss * s.weight for s in samples) / total_weight
    var, cvar = _tail_metrics(
        samples=samples,
        total_weight=total_weight,
        alpha=alpha,
    )
    return RiskEstimate(
        sample_size=len(samples),
        ev=ev,
        cvar=cvar,
        alpha=alpha,
        var=var,
    )


def _tail_metrics(
    *,
    samples: Sequence[OutcomeSample],
    total_weight: float,
    alpha: float,
) -> tuple[float, float]:
    """Return (VaR, CVaR) for the upper tail of magnitude ``alpha``.

    Uses the Rockafellar–Uryasev exact tail-mean formulation:
    sort descending, accumulate exactly ``alpha * total_weight``
    weight from the top of the distribution, partial-including the
    boundary sample when needed so non-uniform-weight inputs land
    on the correct mean.
    """
    sorted_desc = sorted(samples, key=lambda s: -s.loss)
    target_weight = alpha * total_weight
    cumulative = 0.0
    tail_loss = 0.0
    tail_weight = 0.0
    var_value = sorted_desc[0].loss
    for sample in sorted_desc:
        if cumulative >= target_weight:
            break
        needed = target_weight - cumulative
        take = min(sample.weight, needed)
        tail_weight += take
        tail_loss += sample.loss * take
        cumulative += take
        var_value = sample.loss
    if tail_weight <= 0.0:
        cvar_value = var_value
    else:
        cvar_value = tail_loss / tail_weight
    return var_value, cvar_value
