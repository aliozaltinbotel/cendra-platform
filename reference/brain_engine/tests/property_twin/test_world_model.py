"""Behaviour of :class:`IdentityWorldModel`."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from brain_engine.property_twin.models import (
    TwinAction,
    TwinState,
)
from brain_engine.property_twin.protocols import (
    IdentityWorldModel,
)


def _state(**overrides: object) -> TwinState:
    base: dict[str, object] = {
        "property_id": "p1",
        "as_of": date(2026, 5, 10),
        "occupancy_30d": 0.6,
        "adr": 150.0,
        "review_score": 4.5,
        "maintenance_debt": 6.0,
    }
    base.update(overrides)
    return TwinState(**base)  # type: ignore[arg-type]


@pytest.fixture
def model() -> IdentityWorldModel:
    return IdentityWorldModel()


def test_price_change_adjusts_adr(
    model: IdentityWorldModel,
) -> None:
    """``price_change`` shifts ADR by ``magnitude``."""
    state = _state(adr=200.0)
    next_state = model.step(
        state=state,
        action=TwinAction(
            kind="price_change",
            magnitude=25.0,
            effective_on=date(2026, 5, 11),
        ),
    )
    assert next_state.adr == 225.0
    assert next_state.as_of == date(2026, 5, 12)


def test_price_change_floors_at_zero(
    model: IdentityWorldModel,
) -> None:
    """ADR cannot go negative under a steep cut."""
    state = _state(adr=50.0)
    next_state = model.step(
        state=state,
        action=TwinAction(
            kind="price_change",
            magnitude=-100.0,
            effective_on=date(2026, 5, 11),
        ),
    )
    assert next_state.adr == 0.0


def test_maintenance_dispatch_drains_debt(
    model: IdentityWorldModel,
) -> None:
    """``maintenance_dispatch`` reduces maintenance_debt."""
    state = _state(maintenance_debt=10.0)
    next_state = model.step(
        state=state,
        action=TwinAction(
            kind="maintenance_dispatch",
            magnitude=4.0,
            effective_on=date(2026, 5, 11),
        ),
    )
    assert next_state.maintenance_debt == 6.0


def test_maintenance_dispatch_floors_at_zero(
    model: IdentityWorldModel,
) -> None:
    """Maintenance debt cannot drop below zero."""
    state = _state(maintenance_debt=2.0)
    next_state = model.step(
        state=state,
        action=TwinAction(
            kind="maintenance_dispatch",
            magnitude=10.0,
            effective_on=date(2026, 5, 11),
        ),
    )
    assert next_state.maintenance_debt == 0.0


def test_unknown_action_kind_passes_state_through(
    model: IdentityWorldModel,
) -> None:
    """Unknown actions advance the date but leave state alone."""
    state = _state(adr=180.0, maintenance_debt=4.0)
    next_state = model.step(
        state=state,
        action=TwinAction(
            kind="unrecognised_kind",
            magnitude=99.0,
            effective_on=date(2026, 5, 11),
        ),
    )
    assert next_state.adr == 180.0
    assert next_state.maintenance_debt == 4.0
    assert next_state.as_of == date(2026, 5, 12)


def test_world_model_is_deterministic(
    model: IdentityWorldModel,
) -> None:
    """Same input pair produces identical output state."""
    state = _state()
    action = TwinAction(
        kind="price_change",
        magnitude=10.0,
        effective_on=date(2026, 5, 11),
    )
    a = model.step(state=state, action=action)
    b = model.step(state=state, action=action)
    assert a == b
