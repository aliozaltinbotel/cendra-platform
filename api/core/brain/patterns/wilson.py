"""Wilson score interval — statistically-sound success-rate lower bound.

The naive success rate ``k / n`` is a maximum-likelihood point estimate
and says nothing about sampling uncertainty: a rule that succeeded
4 times out of 5 reports a 0.80 ratio that is indistinguishable — by
point estimate alone — from a rule that succeeded 800 out of 1 000.
The first is almost pure noise, the second is solid.

Wilson's 1927 score interval corrects for this by producing an
*interval* whose lower bound shrinks the naive rate as the sample
size shrinks.  Promotion gates that require
``wilson_lower_bound(k, n) >= threshold`` are therefore resilient to
both (a) small-sample over-confidence and (b) the continuity biases
of the normal approximation.

This module is a **pure-math leaf** — no I/O, no logging, no optional
dependencies — so it can be used from hot paths without any async
ceremony.  Default ``z`` is 1.96 (95 % two-sided confidence level).

References:
    Wilson, E. B. (1927). "Probable inference, the law of succession,
    and statistical inference". Journal of the American Statistical
    Association, 22(158), 209-212.
"""

from __future__ import annotations

from math import sqrt
from typing import Final


# Common z-scores for one-sided upper tail probabilities.  95 % ≈ 1.96
# (two-sided) is the convention used across the Brain Engine promotion
# gates; callers who need tighter bounds can pass ``z`` explicitly.
Z_90: Final[float] = 1.645
Z_95: Final[float] = 1.96
Z_99: Final[float] = 2.576


def wilson_lower_bound(
    successes: int,
    trials: int,
    *,
    z: float = Z_95,
) -> float:
    """Return the Wilson-score lower bound on the success probability.

    For zero trials the function returns ``0.0`` — with no evidence we
    assume nothing about the true rate.  For ``successes`` equal to
    ``trials`` the lower bound will still be below 1.0, correctly
    reflecting the statistical uncertainty that any finite sample
    carries.

    Args:
        successes: Observed positive outcomes (``k``).  Must be
            non-negative and ``<= trials``.
        trials: Total observations (``n``).  Must be non-negative.
        z: Standard-normal quantile for the desired confidence level.
            Defaults to the 95 % two-sided convention (``1.96``).

    Returns:
        The Wilson lower bound in the closed interval ``[0.0, 1.0]``.

    Raises:
        ValueError: If ``trials`` is negative, ``successes`` is
            negative, ``successes > trials``, or ``z`` is non-positive.

    Examples:
        >>> round(wilson_lower_bound(8, 10), 4)
        0.4902
        >>> round(wilson_lower_bound(80, 100), 4)
        0.7112
        >>> round(wilson_lower_bound(800, 1000), 4)
        0.7741
    """
    if trials < 0:
        raise ValueError("trials must be non-negative")
    if successes < 0:
        raise ValueError("successes must be non-negative")
    if successes > trials:
        raise ValueError("successes cannot exceed trials")
    if z <= 0.0:
        raise ValueError("z must be positive")

    if trials == 0:
        return 0.0

    n = float(trials)
    p_hat = successes / n
    z_squared = z * z

    denominator = 1.0 + z_squared / n
    centre = p_hat + z_squared / (2.0 * n)
    margin = z * sqrt(p_hat * (1.0 - p_hat) / n + z_squared / (4.0 * n * n))

    lower = (centre - margin) / denominator
    # Numerical noise around the boundaries can push us a hair outside
    # [0, 1]; clamp so downstream comparisons stay well-behaved.
    if lower < 0.0:
        return 0.0
    if lower > 1.0:
        return 1.0
    return lower


# ---------------------------------------------------------------------------
# Autonomy tier thresholds (roadmap §Sprint 2)
# ---------------------------------------------------------------------------

AUTONOMY_WILSON_L2: Final[float] = 0.60
AUTONOMY_WILSON_L3: Final[float] = 0.75
AUTONOMY_WILSON_L4: Final[float] = 0.85
AUTONOMY_WILSON_L4_AUTO: Final[float] = 0.90


# ---------------------------------------------------------------------------
# PatternRule promotion gate thresholds (roadmap §Sprint 2)
# ---------------------------------------------------------------------------

PROMOTION_WILSON_AUTO: Final[float] = 0.75
PROMOTION_MIN_SUPPORT_AUTO: Final[int] = 20
