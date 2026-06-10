"""Value objects for the Property Twin layer (Moat #13).

A *twin* is a forward-simulating shadow of a property: a latent
:class:`TwinState` evolves under :class:`TwinAction` records, and
imagined rollouts let the planner ask "what would happen if I
applied policy X for the next 30 days?".

v0.1 ships the data shape; v1.0 plugs a DreamerV3-style RSSM with
a LinOSS backbone.  The split is deliberate so the moat-defining
architectural claim lands now without waiting on ML infra.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date


__all__ = [
    "RolloutTrace",
    "TwinAction",
    "TwinObservation",
    "TwinState",
]


@dataclass(frozen=True, slots=True)
class TwinState:
    """Latent + bookkeeping snapshot of one property at one date.

    Attributes:
        property_id: Stable id of the property.
        as_of: Calendar date the snapshot describes.
        occupancy_30d: Rolling 30-day occupancy in ``[0.0, 1.0]``.
        adr: Average Daily Rate (caller-defined currency unit).
        review_score: Latest aggregate guest review score in
            ``[0.0, 5.0]``.
        maintenance_debt: Sum of pending maintenance work hours.
        latent: Free-form latent feature map; v1.0 RSSM populates
            this with a low-dimensional embedding of the historic
            time series.
    """

    property_id: str
    as_of: date
    occupancy_30d: float
    adr: float
    review_score: float
    maintenance_debt: float = 0.0
    latent: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.property_id:
            raise ValueError("property_id required")
        if not 0.0 <= self.occupancy_30d <= 1.0:
            raise ValueError(
                "occupancy_30d must be in [0.0, 1.0]"
            )
        if self.adr < 0.0:
            raise ValueError("adr must be non-negative")
        if not 0.0 <= self.review_score <= 5.0:
            raise ValueError(
                "review_score must be in [0.0, 5.0]"
            )
        if self.maintenance_debt < 0.0:
            raise ValueError(
                "maintenance_debt must be non-negative"
            )


@dataclass(frozen=True, slots=True)
class TwinAction:
    """Caller-controlled intervention applied to the twin.

    Attributes:
        kind: Action class identifier (e.g. ``"price_change"`` /
            ``"min_nights_change"`` / ``"maintenance_dispatch"``).
        magnitude: Numeric intensity / delta the action carries.
            Units are caller-defined.
        effective_on: Calendar date the action lands; the world
            model uses this to align with :class:`TwinState`
            ticks.
    """

    kind: str
    magnitude: float
    effective_on: date

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("kind required")


@dataclass(frozen=True, slots=True)
class TwinObservation:
    """Realised outcome the runtime feeds back into the twin.

    Attributes:
        property_id: Stable id of the property.
        observed_on: Calendar date of the observation.
        occupancy_delta: Realised change in 30-day occupancy.
        revenue: Realised revenue for the day.
        review_added: Optional guest review the day added (0–5).
    """

    property_id: str
    observed_on: date
    occupancy_delta: float
    revenue: float
    review_added: float | None = None

    def __post_init__(self) -> None:
        if not self.property_id:
            raise ValueError("property_id required")
        if self.review_added is not None and not (
            0.0 <= self.review_added <= 5.0
        ):
            raise ValueError(
                "review_added must be in [0.0, 5.0]"
            )


@dataclass(frozen=True, slots=True)
class RolloutTrace:
    """Tuple of states an imagined rollout walked through.

    Attributes:
        states: Ordered tuple of :class:`TwinState` snapshots; the
            first element is the rollout's starting state, the
            last is the terminal state after the action sequence.
        actions: Ordered tuple of :class:`TwinAction` records the
            rollout applied between consecutive states.  Length
            equals ``len(states) - 1``.
    """

    states: tuple[TwinState, ...]
    actions: tuple[TwinAction, ...]

    def __post_init__(self) -> None:
        if not self.states:
            raise ValueError("states must be non-empty")
        if len(self.actions) != len(self.states) - 1:
            raise ValueError(
                "actions length must be len(states) - 1"
            )
