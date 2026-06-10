"""Tests for the temporal mining surface (Sprint 2).

Mümin observed (2026-05-05) that two access_code_release rules
produced by the miner — one ``deny``, one ``ask`` — carried no
condition that would distinguish them by *when* the question was
asked, even though the underlying data clearly differs ("PM
sends the code 1-2 days before check-in but defers earlier
asks").  Root cause: ``ConditionSynthesizer._flatten`` reads from
``DecisionCase`` snapshots through fixed allowlists, and neither
``CaseBuilder._build_pms_snapshot`` nor ``_PMS_KEYS`` carried
``lead_time_hours`` or ``stage`` — the temporal axes the
discriminator would need.

Sprint-2 fix:

1. ``CaseBuilder._build_pms_snapshot`` accepts ``calendar`` +
   ``stage`` kwargs and writes ``lead_time_hours`` (computed via
   :class:`FeatureBuilder`) and ``stage.value`` into the snapshot
   when they are available.
2. ``_PMS_KEYS`` allowlist gains ``"lead_time_hours"`` and
   ``"stage"`` so :func:`_flatten` surfaces them as candidate
   features.

These tests pin both halves of the contract end-to-end: a built
case carries the new fields, the flatten projection exposes
them, and the keys live in the allowlist.
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.condition_synthesizer import (
    _PMS_KEYS,
    _flatten,
)
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.patterns.models import (
    BookingStage,
    DecisionType,
    Scenario,
)

_PMS_FIXTURE = {
    "reservation_id": "R-1",
    "status": "confirmed",
    "check_in": "2026-05-12",
    "check_out": "2026-05-15",
    "adults": 2,
    "total_price": 350.0,
    "currency": "EUR",
    "source": "bookingcom",
    "created_at": "2026-05-05T08:00:00Z",
    "property_id": "p1",
}


@pytest.fixture
def case_builder() -> CaseBuilder:
    return CaseBuilder(feature_builder=FeatureBuilder())


# ---------------------------------------------------------------------------
# CaseBuilder snapshot now carries the temporal axes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pms_snapshot_carries_lead_time_when_calendar_supplied(
    case_builder: CaseBuilder,
) -> None:
    case = await case_builder.build(
        message_text="msg",
        response_text="",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DEFER,
        pms_data=_PMS_FIXTURE,
        calendar_data={},
    )
    assert "lead_time_hours" in case.pms_snapshot
    # 7 days × 24h - 8h offset ≈ 160h; the precise value is asserted
    # at the FeatureBuilder layer — we only require the snapshot
    # exposes a non-zero numeric so synthesis has a real signal.
    assert case.pms_snapshot["lead_time_hours"] > 0


@pytest.mark.asyncio
async def test_pms_snapshot_carries_stage(
    case_builder: CaseBuilder,
) -> None:
    case = await case_builder.build(
        message_text="msg",
        response_text="",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DEFER,
        pms_data=_PMS_FIXTURE,
        calendar_data={},
    )
    assert case.pms_snapshot.get("stage") == "pre_arrival"


@pytest.mark.asyncio
async def test_pms_snapshot_omits_lead_time_when_pms_lacks_created_at(
    case_builder: CaseBuilder,
) -> None:
    pms_no_created_at = {k: v for k, v in _PMS_FIXTURE.items()
                          if k != "created_at"}
    case = await case_builder.build(
        message_text="msg",
        response_text="",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DEFER,
        pms_data=pms_no_created_at,
        calendar_data={},
    )
    # Lead-time needs both ``created_at`` and ``check_in``; with the
    # former missing the FeatureBuilder returns 0.0 and the snapshot
    # must NOT fabricate the field — the synthesiser would otherwise
    # learn "lead_time_hours == 0" as a bogus discriminator.
    assert "lead_time_hours" not in case.pms_snapshot
    # Stage is still set because it is independent of PMS metadata.
    assert case.pms_snapshot.get("stage") == "pre_arrival"


@pytest.mark.asyncio
async def test_pms_snapshot_legacy_fields_unchanged(
    case_builder: CaseBuilder,
) -> None:
    case = await case_builder.build(
        message_text="msg",
        response_text="",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DEFER,
        pms_data=_PMS_FIXTURE,
    )
    # Pre-Sprint-2 fields stay byte-identical so existing tests +
    # downstream consumers see no schema regression.
    assert case.pms_snapshot["reservation_id"] == "R-1"
    assert case.pms_snapshot["status"] == "confirmed"
    assert case.pms_snapshot["check_in"] == "2026-05-12"
    assert case.pms_snapshot["adults"] == 2
    assert case.pms_snapshot["source"] == "bookingcom"


# ---------------------------------------------------------------------------
# Synthesiser allowlist now exposes the new keys
# ---------------------------------------------------------------------------


def test_lead_time_hours_in_pms_allowlist() -> None:
    assert "lead_time_hours" in _PMS_KEYS


def test_stage_in_pms_allowlist() -> None:
    assert "stage" in _PMS_KEYS


def test_hours_before_checkin_in_pms_allowlist() -> None:
    assert "hours_before_checkin" in _PMS_KEYS


@pytest.mark.asyncio
async def test_flatten_projects_temporal_axes_to_synthesiser(
    case_builder: CaseBuilder,
) -> None:
    case = await case_builder.build(
        message_text="msg",
        response_text="",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DEFER,
        pms_data=_PMS_FIXTURE,
        calendar_data={},
    )
    flat = _flatten(case)
    # The flatten projection — the exact dict ConditionSynthesizer
    # iterates as candidate split features — now carries the
    # temporal axes the synthesiser needs to discover patterns
    # like "PM defers when lead_time_hours >= 120" or "PM informs
    # when hours_before_checkin <= 48".
    assert "lead_time_hours" in flat
    assert "hours_before_checkin" in flat
    assert "stage" in flat
    assert flat["stage"] == "pre_arrival"


@pytest.mark.asyncio
async def test_pms_snapshot_carries_hours_before_checkin(
    case_builder: CaseBuilder,
) -> None:
    case = await case_builder.build(
        message_text="msg",
        response_text="",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DEFER,
        pms_data=_PMS_FIXTURE,
        calendar_data={},
    )
    # ``hours_before_checkin`` is signed: positive when ``check_in``
    # is in the future relative to "now", negative when already past.
    # The exact magnitude depends on wall clock, so we only assert
    # presence + numeric type — the FeatureBuilder unit tests pin
    # the arithmetic.
    assert "hours_before_checkin" in case.pms_snapshot
    assert isinstance(case.pms_snapshot["hours_before_checkin"], float)


@pytest.mark.asyncio
async def test_pms_snapshot_omits_hours_before_checkin_without_check_in(
    case_builder: CaseBuilder,
) -> None:
    pms_no_checkin = {
        k: v for k, v in _PMS_FIXTURE.items() if k != "check_in"
    }
    case = await case_builder.build(
        message_text="msg",
        response_text="",
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DEFER,
        pms_data=pms_no_checkin,
        calendar_data={},
    )
    # Without ``check_in`` the FeatureBuilder cannot compute proximity;
    # the snapshot must NOT fabricate the field.
    assert "hours_before_checkin" not in case.pms_snapshot
