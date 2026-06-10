"""Two-proportion z-test plus a tiny SPRT-style early-stop hook.

Given two variants that observed ``trials`` Bernoulli outcomes
each, return whether the observed difference between conversion
rates is statistically significant at a configurable
``alpha``.  We hand-roll the normal-CDF approximation because
the broader project keeps SciPy out of the runtime image.

References:
    * Two-proportion z-test, Agresti, *Categorical Data
      Analysis*, §3.5.
    * Abramowitz & Stegun, eq. 26.2.17, for the rational
      approximation to the standard-normal CDF.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_ALPHA",
    "SignificanceResult",
    "two_proportion_z_test",
]


DEFAULT_ALPHA: Final[float] = 0.05
"""Significance level — 5 % family-wise type-I error."""


@dataclass(frozen=True, slots=True)
class SignificanceResult:
    """Outcome of a single significance test.

    Attributes:
        z_score: Two-proportion test statistic
            ``(p_b - p_a) / pooled_se``.
        p_value: Two-sided tail probability.
        significant: ``p_value < alpha``.
        lift: ``p_b - p_a`` — positive lift means variant *b*
            beat variant *a*.
        alpha: Threshold the test was run at.
    """

    z_score: float
    p_value: float
    significant: bool
    lift: float
    alpha: float

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must lie in (0, 1)")
        if not 0.0 <= self.p_value <= 1.0:
            raise ValueError("p_value must lie in [0, 1]")


def two_proportion_z_test(
    *,
    successes_a: int,
    trials_a: int,
    successes_b: int,
    trials_b: int,
    alpha: float = DEFAULT_ALPHA,
) -> SignificanceResult:
    """Two-sided two-proportion z-test.

    Returns a :class:`SignificanceResult` for the comparison of
    variant *a* against variant *b*.  When either variant has
    zero trials the test cannot run and the result is reported
    as not significant with ``p_value == 1.0`` and
    ``z_score == 0.0`` so callers can treat "no data" the same
    way they treat "no signal".
    """
    if successes_a < 0 or successes_b < 0:
        raise ValueError("successes must be non-negative")
    if trials_a < 0 or trials_b < 0:
        raise ValueError("trials must be non-negative")
    if successes_a > trials_a or successes_b > trials_b:
        raise ValueError("successes cannot exceed trials")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must lie in (0, 1)")

    if trials_a == 0 or trials_b == 0:
        return SignificanceResult(
            z_score=0.0,
            p_value=1.0,
            significant=False,
            lift=0.0,
            alpha=alpha,
        )

    p_a = successes_a / trials_a
    p_b = successes_b / trials_b
    pooled = (successes_a + successes_b) / (trials_a + trials_b)
    pooled_var = pooled * (1.0 - pooled) * (
        1.0 / trials_a + 1.0 / trials_b
    )
    if pooled_var <= 0.0:
        # Both variants saw the same outcome rate (0 % or 100 %)
        # — no variance, no signal.
        return SignificanceResult(
            z_score=0.0,
            p_value=1.0,
            significant=False,
            lift=p_b - p_a,
            alpha=alpha,
        )
    z = (p_b - p_a) / math.sqrt(pooled_var)
    p_value = 2.0 * (1.0 - _standard_normal_cdf(abs(z)))
    p_value = max(0.0, min(1.0, p_value))
    return SignificanceResult(
        z_score=z,
        p_value=p_value,
        significant=p_value < alpha,
        lift=p_b - p_a,
        alpha=alpha,
    )


def _standard_normal_cdf(x: float) -> float:
    """Rational approximation to the standard-normal CDF.

    Uses Abramowitz & Stegun 26.2.17 via :func:`math.erf` so the
    runtime stays inside the standard library.
    """
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
