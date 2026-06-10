"""Behaviour of :class:`LinearWorldModel` (M17)."""

from __future__ import annotations

from datetime import date

import pytest

from brain_engine.property_twin.linear_world_model import (
    BaselineDrift,
    LinearEffect,
    LinearWorldModel,
)
from brain_engine.property_twin.models import (
    TwinAction,
    TwinState,
)


def _state(**overrides: object) -> TwinState:
    base: dict[str, object] = {
        "property_id": "p1",
        "as_of": date(2026, 5, 11),
        "occupancy_30d": 0.65,
        "adr": 180.0,
        "review_score": 4.6,
        "maintenance_debt": 8.0,
    }
    base.update(overrides)
    return TwinState(**base)  # type: ignore[arg-type]


def _action(
    kind: str,
    magnitude: float = 1.0,
    *,
    on: date = date(2026, 5, 12),
) -> TwinAction:
    return TwinAction(
        kind=kind, magnitude=magnitude, effective_on=on,
    )


def _no_drift() -> BaselineDrift:
    """Drift that leaves state untouched between steps (for clean math)."""
    return BaselineDrift(
        occupancy_baseline=0.0,
        occupancy_speed=0.0,
        adr_baseline=0.0,
        adr_speed=0.0,
        maintenance_growth=0.0,
    )


def test_linear_effect_field_validation() -> None:
    """Field name must be one of the four supported."""
    with pytest.raises(ValueError, match="field"):
        LinearEffect(field="garbage", delta_per_unit=1.0)


def test_linear_effect_proportional_to_validation() -> None:
    """``proportional_to`` must also be a known field."""
    with pytest.raises(ValueError, match="proportional_to"):
        LinearEffect(
            field="adr",
            delta_per_unit=1.0,
            proportional_to="not_a_field",
        )


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"occupancy_baseline": -0.1}, "occupancy_baseline"),
        ({"occupancy_baseline": 1.5}, "occupancy_baseline"),
        ({"occupancy_speed": -0.1}, "occupancy_speed"),
        ({"occupancy_speed": 1.5}, "occupancy_speed"),
        ({"adr_baseline": -1.0}, "adr_baseline"),
        ({"adr_speed": 1.5}, "adr_speed"),
        ({"maintenance_growth": -0.1}, "maintenance_growth"),
    ],
    ids=[
        "neg_occ_base",
        "high_occ_base",
        "neg_occ_speed",
        "high_occ_speed",
        "neg_adr_base",
        "high_adr_speed",
        "neg_maint_growth",
    ],
)
def test_baseline_drift_validation(
    override: dict[str, float],
    match: str,
) -> None:
    """Each :class:`BaselineDrift` invariant is enforced fail-fast."""
    with pytest.raises(ValueError, match=match):
        BaselineDrift(**override)  # type: ignore[arg-type]


def test_price_change_adjusts_adr_and_occupancy() -> None:
    """Default catalog: price_change moves ADR + nudges occupancy."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(adr=200.0, occupancy_30d=0.7)
    next_state = model.step(
        state=state,
        action=_action("price_change", magnitude=20.0),
    )
    # +20 to ADR; -0.0008 * 20 = -0.016 to occupancy.
    assert next_state.adr == pytest.approx(220.0)
    assert next_state.occupancy_30d == pytest.approx(0.684)


def test_maintenance_dispatch_drains_debt() -> None:
    """maintenance_dispatch reduces debt by ``magnitude``."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(maintenance_debt=10.0)
    next_state = model.step(
        state=state,
        action=_action("maintenance_dispatch", magnitude=4.0),
    )
    assert next_state.maintenance_debt == pytest.approx(6.0)


def test_escalate_adds_maintenance_debt() -> None:
    """escalate raises maintenance_debt (ops cost)."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(maintenance_debt=2.0)
    next_state = model.step(
        state=state,
        action=_action("escalate", magnitude=2.0),
    )
    assert next_state.maintenance_debt == pytest.approx(3.0)


def test_block_date_drops_occupancy_one_thirtieth() -> None:
    """block_date reduces 30-day occupancy by 1/30 per blocked night."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(occupancy_30d=0.6)
    next_state = model.step(
        state=state,
        action=_action("block_date", magnitude=1.0),
    )
    assert next_state.occupancy_30d == pytest.approx(
        0.6 - 1.0 / 30.0
    )


def test_noise_complaint_warning_recovers_review() -> None:
    """noise_complaint_warning slightly bumps review and occupancy."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(review_score=4.5, occupancy_30d=0.6)
    next_state = model.step(
        state=state,
        action=_action("noise_complaint_warning", magnitude=1.0),
    )
    assert next_state.review_score == pytest.approx(4.51)
    assert next_state.occupancy_30d == pytest.approx(0.605)


def test_unknown_action_passes_through_with_drift_only() -> None:
    """Unknown action kind: only baseline drift applies."""
    model = LinearWorldModel()  # default drift
    state = _state(maintenance_debt=5.0)
    next_state = model.step(
        state=state,
        action=_action("not_a_real_kind", magnitude=999.0),
    )
    # Default drift adds maintenance_growth=0.1.
    assert next_state.maintenance_debt == pytest.approx(5.1)


def test_drift_mean_reverts_occupancy_toward_baseline() -> None:
    """Off-baseline occupancy moves toward baseline by speed * gap."""
    drift = BaselineDrift(
        occupancy_baseline=0.7,
        occupancy_speed=0.5,
        adr_baseline=0.0,
        adr_speed=0.0,
        maintenance_growth=0.0,
    )
    model = LinearWorldModel(
        action_effects={},  # disable action effects
        drift=drift,
    )
    state = _state(occupancy_30d=0.5)
    next_state = model.step(
        state=state,
        action=_action("noop", magnitude=0.0),
    )
    # 0.5 + (0.7 - 0.5) * 0.5 = 0.6
    assert next_state.occupancy_30d == pytest.approx(0.6)


def test_drift_mean_reverts_adr_toward_baseline() -> None:
    """Off-baseline ADR moves toward baseline by speed * gap."""
    drift = BaselineDrift(
        occupancy_baseline=0.0,
        occupancy_speed=0.0,
        adr_baseline=200.0,
        adr_speed=0.5,
        maintenance_growth=0.0,
    )
    model = LinearWorldModel(action_effects={}, drift=drift)
    state = _state(adr=160.0)
    next_state = model.step(
        state=state,
        action=_action("noop", magnitude=0.0),
    )
    # 160 + (200 - 160) * 0.5 = 180
    assert next_state.adr == pytest.approx(180.0)


def test_bounds_floor_adr_at_zero() -> None:
    """Steep negative magnitude does not push ADR below zero."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(adr=50.0)
    next_state = model.step(
        state=state,
        action=_action("price_change", magnitude=-500.0),
    )
    assert next_state.adr == 0.0


def test_bounds_clamp_occupancy_to_unit_interval() -> None:
    """Occupancy stays in ``[0, 1]`` even after extreme actions."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(occupancy_30d=0.05)
    # Many block_dates would normally push below zero
    next_state = model.step(
        state=state,
        action=_action("block_date", magnitude=10.0),
    )
    assert next_state.occupancy_30d == 0.0


def test_bounds_clamp_review_score_to_five() -> None:
    """review_score stays in ``[0, 5]``."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(review_score=4.99)
    # 200 warnings would shoot review past 5 in the linear math
    next_state = model.step(
        state=state,
        action=_action("noise_complaint_warning", magnitude=200.0),
    )
    assert next_state.review_score == 5.0


def test_step_advances_date_by_one_day() -> None:
    """Next state is dated one day after the action's effective_on."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state()
    next_state = model.step(
        state=state,
        action=_action("price_change", on=date(2026, 6, 1)),
    )
    assert next_state.as_of == date(2026, 6, 2)


def test_step_preserves_property_id_and_latent() -> None:
    """Property id + latent map round-trip across the step."""
    model = LinearWorldModel(drift=_no_drift())
    state = _state(
        property_id="p_abc",
    )
    state = TwinState(
        property_id=state.property_id,
        as_of=state.as_of,
        occupancy_30d=state.occupancy_30d,
        adr=state.adr,
        review_score=state.review_score,
        maintenance_debt=state.maintenance_debt,
        latent={"trend": 0.123, "season": -0.4},
    )
    next_state = model.step(
        state=state,
        action=_action("price_change"),
    )
    assert next_state.property_id == "p_abc"
    assert next_state.latent == {"trend": 0.123, "season": -0.4}


def test_default_drift_constants_finite() -> None:
    """Default drift values are finite and within bounds."""
    drift = BaselineDrift()
    assert 0.0 <= drift.occupancy_baseline <= 1.0
    assert 0.0 <= drift.occupancy_speed <= 1.0
    assert drift.adr_baseline > 0.0
    assert drift.maintenance_growth >= 0.0
