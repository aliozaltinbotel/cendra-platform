"""Tests for the Sprint H per-scenario feature whitelist.

Sprint H closes Mümin's 2026-05-06 complaint that the
``access_code_release`` rule mined ``currency``, ``total_price``,
``source`` and ``status`` even though those fields are
domain-irrelevant for an access-code release decision.  The fix is
a hand-curated whitelist in
``brain_engine.patterns.scenario_features.SCENARIO_FEATURES`` that
the synthesiser consults during feature flattening when the
``BRAIN_SCENARIO_FEATURES_ENABLED`` env flag is truthy.

These tests pin both halves of the contract:

* **Flag off (default)** — flattened features are bit-for-bit
  identical to pre-Sprint-H even for scenarios listed in
  :data:`SCENARIO_FEATURES`.
* **Flag on** — listed scenarios drop fields outside their
  whitelist; unlisted scenarios still use the global defaults.

Runtime evaluation (``models._evaluate_condition``) is unchanged;
this is purely a feature-surface restriction during mining.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import pytest

from brain_engine.patterns.condition_synthesizer import (
    _flatten,
    _resolve_feature_keys,
    _scenario_features_enabled,
)
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionCase,
    DecisionType,
    Scenario,
)
from brain_engine.patterns.scenario_features import (
    SCENARIO_FEATURES,
    FeatureWhitelist,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_scenario_features_flag() -> Iterator[None]:
    """Ensure the Sprint H flag is unset at every test entry.

    Tests that need the flag on toggle it explicitly; the autouse
    reset guarantees no leak between tests on the same worker — and
    no leak into adjacent test files that do not subscribe to this
    fixture.
    """
    previous = os.environ.pop("BRAIN_SCENARIO_FEATURES_ENABLED", None)
    try:
        yield
    finally:
        # Whatever the test put back must be cleared, otherwise
        # a downstream module-scoped test in the same pytest run
        # would see Sprint H enabled by accident.
        os.environ.pop("BRAIN_SCENARIO_FEATURES_ENABLED", None)
        if previous is not None:
            os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = previous


def _make_case(
    *,
    scenario: Scenario,
    pms_snapshot: dict[str, object] | None = None,
) -> DecisionCase:
    """Build a minimal DecisionCase carrying the requested scenario."""
    return DecisionCase(
        case_id="c1",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=scenario,
        message_text="msg",
        response_text="resp",
        decision=DecisionAction(action_type=DecisionType.APPROVE),
        pms_snapshot=pms_snapshot or {},
    )


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_flag_off_by_default() -> None:
    assert _scenario_features_enabled() is False


@pytest.mark.parametrize(
    "raw", ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(raw: str) -> None:
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = raw
    assert _scenario_features_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "garbage"])
def test_flag_falsy_values(raw: str) -> None:
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = raw
    assert _scenario_features_enabled() is False


# ---------------------------------------------------------------------------
# Resolution semantics
# ---------------------------------------------------------------------------


def test_resolve_keys_flag_off_returns_global_defaults_for_listed() -> None:
    """Flag off — even listed scenarios get global defaults."""
    pms_keys, _, _ = _resolve_feature_keys(Scenario.ACCESS_CODE_RELEASE)
    assert "currency" in pms_keys
    assert "total_price" in pms_keys
    assert "source" in pms_keys


def test_resolve_keys_flag_on_listed_scenario_uses_whitelist() -> None:
    """Flag on + listed scenario — whitelist replaces PMS default."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    pms_keys, _, _ = _resolve_feature_keys(Scenario.ACCESS_CODE_RELEASE)
    expected = SCENARIO_FEATURES[Scenario.ACCESS_CODE_RELEASE].pms_keys
    assert pms_keys == expected
    assert "currency" not in pms_keys
    assert "total_price" not in pms_keys
    assert "source" not in pms_keys
    assert "status" not in pms_keys


def test_resolve_keys_flag_on_unlisted_scenario_uses_default() -> None:
    """Flag on + unlisted scenario — fallback to global defaults."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    pms_keys, _, _ = _resolve_feature_keys(Scenario.DISCOUNT_REQUEST)
    assert "currency" in pms_keys
    assert "total_price" in pms_keys


def test_partial_override_falls_back_per_source() -> None:
    """``None`` on a source means "use the global default" for it."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    # ACCESS_CODE_RELEASE only overrides PMS keys; calendar and
    # guest sources stay at their global defaults.
    _, calendar_keys, guest_keys = _resolve_feature_keys(
        Scenario.ACCESS_CODE_RELEASE,
    )
    assert "occupancy_7d" in calendar_keys
    assert "rating" in guest_keys


# ---------------------------------------------------------------------------
# Flatten contract
# ---------------------------------------------------------------------------


def test_flatten_flag_off_keeps_irrelevant_fields() -> None:
    """Pre-Sprint-H surface — currency/source remain in flat."""
    case = _make_case(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        pms_snapshot={
            "currency": "EUR",
            "source": "bookingcom",
            "stage": "in_stay",
        },
    )
    flat = _flatten(case)
    assert flat["currency"] == "EUR"
    assert flat["source"] == "bookingcom"
    assert flat["stage"] == "in_stay"


def test_flatten_flag_on_drops_irrelevant_fields() -> None:
    """Sprint H surface — currency/source no longer reach the miner."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    case = _make_case(
        scenario=Scenario.ACCESS_CODE_RELEASE,
        pms_snapshot={
            "currency": "EUR",
            "source": "bookingcom",
            "total_price": 50.0,
            "stage": "in_stay",
            "hours_before_checkin": 6,
        },
    )
    flat = _flatten(case)
    assert "currency" not in flat
    assert "source" not in flat
    assert "total_price" not in flat
    assert flat["stage"] == "in_stay"
    assert flat["hours_before_checkin"] == 6


def test_flatten_flag_on_unlisted_scenario_unchanged() -> None:
    """Unlisted scenario keeps the full PMS surface even with flag on."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    case = _make_case(
        scenario=Scenario.DISCOUNT_REQUEST,
        pms_snapshot={
            "currency": "EUR",
            "total_price": 100.0,
        },
    )
    flat = _flatten(case)
    assert flat["currency"] == "EUR"
    assert flat["total_price"] == 100.0


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------


def test_scenario_features_uses_frozen_slots() -> None:
    """Whitelist value object is immutable for safe sharing."""
    whitelist = SCENARIO_FEATURES[Scenario.ACCESS_CODE_RELEASE]
    assert isinstance(whitelist, FeatureWhitelist)
    with pytest.raises(FrozenInstanceError):
        whitelist.pms_keys = ()  # type: ignore[misc]


def test_access_code_release_drops_muemin_complained_fields() -> None:
    """Anchor: the four fields Mümin called out are gone."""
    whitelist = SCENARIO_FEATURES[Scenario.ACCESS_CODE_RELEASE]
    pms_keys = whitelist.pms_keys or ()
    for field in ("currency", "total_price", "source", "status"):
        assert field not in pms_keys


# ---------------------------------------------------------------------------
# Round-3 — late_checkout / early_checkin (Mümin 2026-05-08 screenshot)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    [Scenario.LATE_CHECKOUT, Scenario.EARLY_CHECKIN],
)
def test_timing_occupancy_family_drops_irrelevant_fields(
    scenario: Scenario,
) -> None:
    """LATE_CHECKOUT / EARLY_CHECKIN must drop the same spurious keys."""
    whitelist = SCENARIO_FEATURES[scenario]
    pms_keys = whitelist.pms_keys or ()
    for field in ("currency", "total_price", "source", "status"):
        assert field not in pms_keys


def test_timing_occupancy_family_shares_one_whitelist() -> None:
    """Dedup anchor — three timing-occupancy scenarios share the same
    whitelist object, so future additions to the family stay in sync.
    """
    access_code = SCENARIO_FEATURES[Scenario.ACCESS_CODE_RELEASE]
    late_checkout = SCENARIO_FEATURES[Scenario.LATE_CHECKOUT]
    early_checkin = SCENARIO_FEATURES[Scenario.EARLY_CHECKIN]
    assert access_code is late_checkout
    assert late_checkout is early_checkin


def test_late_checkout_flatten_flag_on_matches_screenshot_scenario() -> None:
    """Mümin 2026-05-08 screenshot reproduction.

    Reservation context: ``source=manual``, ``status=Confirmed``,
    ``total_price=-3000``, ``stage=in_stay``.  With the flag on,
    none of the first three reach the synthesiser surface; only
    ``stage`` and ``hours_before_checkin`` survive — exactly the
    fields a late-checkout decision should be gated by.
    """
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    case = _make_case(
        scenario=Scenario.LATE_CHECKOUT,
        pms_snapshot={
            "source": "manual",
            "status": "Confirmed",
            "total_price": -3000,
            "currency": "EUR",
            "stage": "in_stay",
            "hours_before_checkin": -16,
            "adults": 2,
            "children": 0,
        },
    )
    flat = _flatten(case)
    for absent in ("source", "status", "total_price", "currency"):
        assert absent not in flat, (
            f"{absent} leaked into the synthesiser surface — "
            "late_checkout rule will over-fit again"
        )
    assert flat["stage"] == "in_stay"
    assert flat["hours_before_checkin"] == -16
    assert flat["adults"] == 2


def test_early_checkin_flatten_flag_on_keeps_timing_keys() -> None:
    """EARLY_CHECKIN parallels LATE_CHECKOUT — timing keys survive."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    case = _make_case(
        scenario=Scenario.EARLY_CHECKIN,
        pms_snapshot={
            "source": "airbnb",
            "total_price": 250.0,
            "currency": "EUR",
            "stage": "pre_arrival",
            "lead_time_hours": 36.0,
            "adults": 1,
        },
    )
    flat = _flatten(case)
    for absent in ("source", "total_price", "currency"):
        assert absent not in flat
    assert flat["stage"] == "pre_arrival"
    assert flat["lead_time_hours"] == 36.0
    assert flat["adults"] == 1
