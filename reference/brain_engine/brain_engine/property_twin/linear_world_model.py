"""Analytical :class:`WorldModel` for the Property Twin (M17).

Replaces M13's :class:`IdentityWorldModel` baseline with a real
predictor: per-action :class:`LinearEffect` catalogue applied to
the state, plus baseline mean-reversion drift per day, plus
bounds enforcement at the end.  Pure-Python — no torch / NumPy /
DreamerV3.

This *partially* closes the M13 deferred TODO ("DreamerV3 +
LinOSS WorldModel implementation").  The full RSSM with a LinOSS
backbone still requires ML libs and lands in v1.0; the linear
analytical model here is the production-acceptable middle ground.

Honest scope:

  * What this is: a deterministic linear analytical predictor
    that handles four headline state fields (occupancy_30d,
    adr, review_score, maintenance_debt) with realistic
    coefficients for STR (price elasticity, mean reversion,
    wear-and-tear drift).
  * What this is NOT: a DreamerV3 RSSM with a learned latent.
    The :attr:`TwinState.latent` field is preserved across the
    step but not modelled.  For learned latent dynamics see
    v1.0.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

from brain_engine.property_twin.models import (
    TwinAction,
    TwinState,
)


__all__ = [
    "DEFAULT_ACTION_EFFECTS",
    "DEFAULT_DRIFT",
    "BaselineDrift",
    "LinearEffect",
    "LinearWorldModel",
]


# ── State-field constants (string keys we operate on) ───── #
_OCCUPANCY: Final[str] = "occupancy_30d"
_ADR: Final[str] = "adr"
_REVIEW: Final[str] = "review_score"
_MAINT: Final[str] = "maintenance_debt"


@dataclass(frozen=True, slots=True)
class LinearEffect:
    """One per-action effect row.

    Attributes:
        field: Name of the :class:`TwinState` field to adjust.
            One of ``"occupancy_30d"`` / ``"adr"`` /
            ``"review_score"`` / ``"maintenance_debt"``.
        delta_per_unit: How much the field changes per unit of
            :attr:`TwinAction.magnitude`.  Can be negative.
        proportional_to: Optional field name; when set, the
            effective delta becomes
            ``delta_per_unit * magnitude * state[proportional_to]``.
            Used for elasticity (price change → occupancy drop
            scales with current ADR).
    """

    field: str
    delta_per_unit: float
    proportional_to: str | None = None

    def __post_init__(self) -> None:
        valid = {_OCCUPANCY, _ADR, _REVIEW, _MAINT}
        if self.field not in valid:
            raise ValueError(
                f"field must be one of {valid}; got {self.field!r}"
            )
        if (
            self.proportional_to is not None
            and self.proportional_to not in valid
        ):
            raise ValueError(
                "proportional_to must be a valid state field"
            )


@dataclass(frozen=True, slots=True)
class BaselineDrift:
    """Per-day mean-reverting drift applied to every step.

    Attributes:
        occupancy_baseline: Long-run mean occupancy in
            ``[0.0, 1.0]``.
        occupancy_speed: Fraction of the gap closed per day in
            ``[0.0, 1.0]``.
        adr_baseline: Long-run mean ADR (caller-defined unit).
        adr_speed: Fraction of the gap closed per day.
        maintenance_growth: Wear-and-tear units added per day
            (non-negative).
    """

    occupancy_baseline: float = 0.7
    occupancy_speed: float = 0.05
    adr_baseline: float = 180.0
    adr_speed: float = 0.02
    maintenance_growth: float = 0.1

    def __post_init__(self) -> None:
        if not 0.0 <= self.occupancy_baseline <= 1.0:
            raise ValueError(
                "occupancy_baseline must be in [0.0, 1.0]"
            )
        if not 0.0 <= self.occupancy_speed <= 1.0:
            raise ValueError(
                "occupancy_speed must be in [0.0, 1.0]"
            )
        if self.adr_baseline < 0.0:
            raise ValueError("adr_baseline must be non-negative")
        if not 0.0 <= self.adr_speed <= 1.0:
            raise ValueError(
                "adr_speed must be in [0.0, 1.0]"
            )
        if self.maintenance_growth < 0.0:
            raise ValueError(
                "maintenance_growth must be non-negative"
            )


# ── Default action effect catalog ──────────────────────────── #

DEFAULT_ACTION_EFFECTS: Final[
    Mapping[str, tuple[LinearEffect, ...]]
] = {
    # Price change: ADR moves by the magnitude; occupancy reacts
    # via elasticity (positive magnitude reduces occupancy a bit).
    "price_change": (
        LinearEffect(field=_ADR, delta_per_unit=1.0),
        LinearEffect(
            field=_OCCUPANCY,
            delta_per_unit=-0.0008,
        ),
    ),
    # Maintenance dispatch: drains maintenance_debt.
    "maintenance_dispatch": (
        LinearEffect(field=_MAINT, delta_per_unit=-1.0),
    ),
    # Noise complaint warning: very small positive nudge on
    # review_score (recovery) plus tiny occupancy bump (better
    # guest comms).
    "noise_complaint_warning": (
        LinearEffect(field=_REVIEW, delta_per_unit=0.01),
        LinearEffect(field=_OCCUPANCY, delta_per_unit=0.005),
    ),
    # Block a date: drops 1/30 of the rolling 30-day occupancy
    # window (one less booked night out of thirty).
    "block_date": (
        LinearEffect(
            field=_OCCUPANCY,
            delta_per_unit=-1.0 / 30.0,
        ),
    ),
    # Escalate: ops cost — maintenance_debt grows by the
    # escalation magnitude.
    "escalate": (
        LinearEffect(field=_MAINT, delta_per_unit=0.5),
    ),
}


DEFAULT_DRIFT: Final[BaselineDrift] = BaselineDrift()


class LinearWorldModel:
    """Analytical :class:`WorldModel` for the Property Twin.

    Construction takes an optional effects catalog override and a
    drift override.  ``step()`` applies the action's effects, then
    the per-day drift, then the state-bound clamps.
    """

    def __init__(
        self,
        *,
        action_effects: Mapping[
            str, tuple[LinearEffect, ...]
        ] = DEFAULT_ACTION_EFFECTS,
        drift: BaselineDrift = DEFAULT_DRIFT,
    ) -> None:
        self._action_effects = dict(action_effects)
        self._drift = drift

    def step(
        self,
        *,
        state: TwinState,
        action: TwinAction,
    ) -> TwinState:
        """Return the predicted next state after applying ``action``."""
        next_date = action.effective_on + timedelta(days=1)
        adjusted = self._apply_action(state=state, action=action)
        drifted = self._apply_drift(adjusted)
        return self._materialise(
            property_id=state.property_id,
            next_date=next_date,
            occupancy=drifted[_OCCUPANCY],
            adr=drifted[_ADR],
            review=drifted[_REVIEW],
            maintenance=drifted[_MAINT],
            latent=state.latent,
        )

    # ── internals ─────────────────────────────────────────── #

    def _apply_action(
        self,
        *,
        state: TwinState,
        action: TwinAction,
    ) -> dict[str, float]:
        scratch = self._scratch_from_state(state)
        effects = self._action_effects.get(action.kind)
        if effects is None:
            return scratch
        for effect in effects:
            base = effect.delta_per_unit * action.magnitude
            if effect.proportional_to is not None:
                base *= scratch[effect.proportional_to]
            scratch[effect.field] += base
        return scratch

    def _apply_drift(
        self,
        scratch: dict[str, float],
    ) -> dict[str, float]:
        drift = self._drift
        scratch[_OCCUPANCY] += (
            drift.occupancy_baseline - scratch[_OCCUPANCY]
        ) * drift.occupancy_speed
        scratch[_ADR] += (
            drift.adr_baseline - scratch[_ADR]
        ) * drift.adr_speed
        scratch[_MAINT] += drift.maintenance_growth
        return scratch

    @staticmethod
    def _scratch_from_state(state: TwinState) -> dict[str, float]:
        return {
            _OCCUPANCY: state.occupancy_30d,
            _ADR: state.adr,
            _REVIEW: state.review_score,
            _MAINT: state.maintenance_debt,
        }

    @staticmethod
    def _materialise(
        *,
        property_id: str,
        next_date: date,
        occupancy: float,
        adr: float,
        review: float,
        maintenance: float,
        latent: Mapping[str, float],
    ) -> TwinState:
        return TwinState(
            property_id=property_id,
            as_of=next_date,
            occupancy_30d=_clamp(occupancy, 0.0, 1.0),
            adr=max(0.0, adr),
            review_score=_clamp(review, 0.0, 5.0),
            maintenance_debt=max(0.0, maintenance),
            latent=dict(latent),
        )


def _clamp(value: float, lo: float, hi: float) -> float:
    """Pure-Python clamp helper."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
