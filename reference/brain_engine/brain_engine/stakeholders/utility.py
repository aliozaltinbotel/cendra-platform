"""Utility models for the negotiation layer.

A utility model maps an :class:`ActionCandidate`'s feature vector
to a real number in the closed interval ``[0.0, 1.0]``.  v0.1 ships
a single concrete implementation — :class:`LinearUtilityFunction` —
that takes feature weights, optional clamps, and an optional
constant bias.  Future versions may add concave / risk-averse
forms; the :class:`UtilityFunction` Protocol keeps that extension
open.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from brain_engine.stakeholders.models import (
    ActionCandidate,
    StakeholderId,
)


__all__ = [
    "DEFAULT_UTILITY_FLOOR",
    "LinearUtilityFunction",
    "UtilityFunction",
]


DEFAULT_UTILITY_FLOOR: Final[float] = 0.0


class UtilityFunction(Protocol):
    """Score an :class:`ActionCandidate` for one stakeholder."""

    def score(self, action: ActionCandidate) -> float:
        """Return a utility value in ``[0.0, 1.0]``."""
        ...


@dataclass(frozen=True, slots=True)
class LinearUtilityFunction:
    """Weighted linear combination of feature values.

    The score is computed as::

        raw = bias + sum(weight_i * feature_i)
        score = clamp(raw, 0.0, 1.0)

    Features absent from the candidate's feature map contribute
    zero — convenient when stakeholders only care about a subset
    of the global feature schema.

    Attributes:
        stakeholder: The stakeholder this function speaks for.
        weights: Per-feature weight mapping; finite floats.
        bias: Constant offset added before clamping.
        floor: Minimum score the function can return; defaults to
            ``0.0``.  Useful when callers want to express "this
            stakeholder is *never* fully unhappy".
    """

    stakeholder: StakeholderId
    weights: Mapping[str, float]
    bias: float = 0.0
    floor: float = DEFAULT_UTILITY_FLOOR

    def __post_init__(self) -> None:
        if not 0.0 <= self.floor <= 1.0:
            raise ValueError("floor must be in [0.0, 1.0]")
        for name, value in self.weights.items():
            if value != value or value in (
                float("inf"),
                float("-inf"),
            ):
                raise ValueError(
                    f"weight {name!r} must be finite"
                )

    def score(self, action: ActionCandidate) -> float:
        """Return the clamped weighted-linear score."""
        raw = self.bias
        for name, weight in self.weights.items():
            raw += weight * action.features.get(name, 0.0)
        if raw < self.floor:
            return self.floor
        if raw > 1.0:
            return 1.0
        return raw


@dataclass(frozen=True, slots=True)
class UtilityRoster:
    """Bundle of utility functions keyed by stakeholder.

    The engine queries the roster once per (stakeholder,
    candidate) pair.  Construction validates that every
    stakeholder maps to exactly one function.
    """

    functions: Mapping[StakeholderId, UtilityFunction] = field(
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        if not self.functions:
            raise ValueError(
                "UtilityRoster must contain at least one function"
            )

    def for_stakeholder(
        self,
        stakeholder: StakeholderId,
    ) -> UtilityFunction:
        """Return the function registered for ``stakeholder``."""
        try:
            return self.functions[stakeholder]
        except KeyError as exc:
            raise KeyError(
                f"no utility function for {stakeholder!r}"
            ) from exc

    def stakeholders(self) -> tuple[StakeholderId, ...]:
        """Return every registered stakeholder."""
        return tuple(self.functions.keys())
