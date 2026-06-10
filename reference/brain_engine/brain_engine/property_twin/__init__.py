"""Property Twin layer (Moat #13, Cand. 2 from latest_research §3.2).

A non-medical Digital Twin Brain for STR property: a forward-
simulating shadow that produces imagined rollouts the planner can
inspect before committing to an action.

Public surface:

- :class:`TwinState` — frozen latent + bookkeeping snapshot.
- :class:`TwinAction` — intervention applied to the twin.
- :class:`TwinObservation` — realised outcome fed back from
  production into the twin.
- :class:`RolloutTrace` — ordered tuple of states + the actions
  walked between them.
- :class:`WorldModel` Protocol + :class:`IdentityWorldModel`
  baseline + :class:`ObservationStore` Protocol.
- :class:`PropertyTwin` — runtime façade walking actions through
  a world model from an explicit starting state.

Defensibility (Moat #13, Cand. 2): non-medical digital twin for
STR portfolio with latent state + WorldModel Protocol +
interventional rollouts.  Industrial twins (Predix, MindSphere)
model physical processes; brain twins (DTB / Spaun / Tianjic /
Fudan) model biology; *no published twin* combines property
trajectory state + agent decision rollouts in one runtime.

v0.1 ships the data model + Protocol + IdentityWorldModel
baseline; v1.0 plugs the DreamerV3 + LinOSS backbone the moat
proposal calls for (latest_research §5).
"""

from __future__ import annotations

from brain_engine.property_twin.models import (
    RolloutTrace,
    TwinAction,
    TwinObservation,
    TwinState,
)
from brain_engine.property_twin.linear_world_model import (
    DEFAULT_ACTION_EFFECTS,
    DEFAULT_DRIFT,
    BaselineDrift,
    LinearEffect,
    LinearWorldModel,
)
from brain_engine.property_twin.protocols import (
    IdentityWorldModel,
    ObservationStore,
    WorldModel,
)
from brain_engine.property_twin.twin import PropertyTwin


__all__ = [
    "BaselineDrift",
    "DEFAULT_ACTION_EFFECTS",
    "DEFAULT_DRIFT",
    "IdentityWorldModel",
    "LinearEffect",
    "LinearWorldModel",
    "ObservationStore",
    "PropertyTwin",
    "RolloutTrace",
    "TwinAction",
    "TwinObservation",
    "TwinState",
    "WorldModel",
]
