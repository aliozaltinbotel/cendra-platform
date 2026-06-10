"""Tests for the Sprint H gap fix in :class:`PatternExtractor`.

Background
----------
Sprint H landed a per-scenario PMS-feature whitelist
(``brain_engine.patterns.scenario_features.SCENARIO_FEATURES``) and
wired it into :class:`ConditionSynthesizer` via ``_flatten`` →
``_resolve_feature_keys``.  :class:`PatternMiner` (the bootstrap /
nightly path) consumes the synthesiser, so it correctly drops
spurious fields (``currency``, ``total_price``, ``source``,
``status``) for whitelisted scenarios when the
``BRAIN_SCENARIO_FEATURES_ENABLED`` flag is on.

Mümin 2026-05-08 round-3 follow-up: live ``/patterns/extract`` on
323133 still surfaced those fields after E (PR 198) merged because
:class:`PatternExtractor` uses its own ``_infer_conditions`` →
``_collect_snapshot_features`` path, which walked every snapshot
key without consulting the whitelist.  The asymmetry made it look
like the flag was off even though it was on.

The fix threads the scenario through ``_collect_snapshot_features``
and reuses ``_resolve_feature_keys`` so both paths apply the same
filter.  Property-agnostic — the whitelist itself is keyed by
:class:`Scenario`, never by ``property_id``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from brain_engine.patterns.extractor import PatternExtractor
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    CaseSource,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.store import InMemoryDecisionCaseStore


@pytest.fixture(autouse=True)
def _reset_scenario_features_flag() -> Iterator[None]:
    """Keep the Sprint H flag scoped to the test that toggles it."""
    previous = os.environ.pop("BRAIN_SCENARIO_FEATURES_ENABLED", None)
    try:
        yield
    finally:
        os.environ.pop("BRAIN_SCENARIO_FEATURES_ENABLED", None)
        if previous is not None:
            os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = previous


def _make_case(
    *,
    scenario: Scenario,
    property_id: str = "prop-1",
    pms_snapshot: dict | None = None,
) -> DecisionCase:
    """Build a learnable case carrying the given PMS snapshot."""
    return DecisionCase(
        stage=BookingStage.IN_STAY,
        scenario=scenario,
        property_id=property_id,
        owner_id="",
        decision=DecisionAction(action_type=DecisionType.INFORM, params={}),
        outcome=CaseOutcome(
            approved=True,
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        source=CaseSource.HISTORICAL,
        pms_snapshot=pms_snapshot or {},
        created_at=datetime(2026, 5, 8, tzinfo=UTC),
    )


async def _seed(cases: list[DecisionCase]) -> InMemoryDecisionCaseStore:
    store = InMemoryDecisionCaseStore()
    for case in cases:
        await store.store(case)
    return store


# ---------------------------------------------------------------------------
# Whitelist-aware snapshot collection
# ---------------------------------------------------------------------------


def _spurious_pms_snapshot() -> dict:
    """A snapshot loaded with both whitelist and non-whitelist keys."""
    return {
        # Sprint H whitelist for ACCESS_CODE_RELEASE / LATE_CHECKOUT /
        # EARLY_CHECKIN — these MUST survive when the flag is on.
        "stage": "in_stay",
        "hours_before_checkin": -16,
        "lead_time_hours": 12.0,
        "adults": 2,
        "children": 0,
        # Spurious fields Mümin called out — these MUST be dropped
        # when the flag is on, kept when it is off.
        "currency": "EUR",
        "source": "bookingcom",
        "status": "new",
        "total_price": 50.0,
    }


def test_extractor_collect_drops_spurious_fields_when_flag_on() -> None:
    """Live behaviour pinning: flag on → ``_collect`` filters by whitelist."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    cases = [
        _make_case(
            scenario=Scenario.LATE_CHECKOUT,
            pms_snapshot=_spurious_pms_snapshot(),
        )
        for _ in range(3)
    ]
    extractor = PatternExtractor(
        store=InMemoryDecisionCaseStore(),
        min_support=3,
        min_confidence=0.5,
    )
    features = extractor._collect_snapshot_features(
        cases, Scenario.LATE_CHECKOUT,
    )
    for spurious in ("currency", "source", "status", "total_price"):
        assert spurious not in features, (
            f"{spurious} leaked through extractor — Sprint H wiring "
            "regressed"
        )
    for kept in ("stage", "hours_before_checkin", "lead_time_hours"):
        assert kept in features


def test_extractor_collect_keeps_all_fields_when_flag_off() -> None:
    """Backward compat: flag off → behaviour matches pre-Sprint-H."""
    cases = [
        _make_case(
            scenario=Scenario.LATE_CHECKOUT,
            pms_snapshot=_spurious_pms_snapshot(),
        )
        for _ in range(3)
    ]
    extractor = PatternExtractor(
        store=InMemoryDecisionCaseStore(),
        min_support=3,
        min_confidence=0.5,
    )
    features = extractor._collect_snapshot_features(
        cases, Scenario.LATE_CHECKOUT,
    )
    for key in (
        "currency",
        "source",
        "status",
        "total_price",
        "stage",
        "hours_before_checkin",
    ):
        assert key in features


def test_extractor_collect_unlisted_scenario_keeps_all_fields() -> None:
    """Unlisted scenario uses global defaults even when flag is on."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    cases = [
        _make_case(
            scenario=Scenario.DISCOUNT_REQUEST,
            pms_snapshot=_spurious_pms_snapshot(),
        )
        for _ in range(3)
    ]
    extractor = PatternExtractor(
        store=InMemoryDecisionCaseStore(),
        min_support=3,
        min_confidence=0.5,
    )
    features = extractor._collect_snapshot_features(
        cases, Scenario.DISCOUNT_REQUEST,
    )
    for key in (
        "currency",
        "source",
        "status",
        "total_price",
        "stage",
    ):
        assert key in features


def test_extractor_collect_handles_none_scenario_gracefully() -> None:
    """Legacy call sites that pass no scenario keep working."""
    cases = [
        _make_case(
            scenario=Scenario.LATE_CHECKOUT,
            pms_snapshot=_spurious_pms_snapshot(),
        )
        for _ in range(2)
    ]
    extractor = PatternExtractor(
        store=InMemoryDecisionCaseStore(),
        min_support=3,
        min_confidence=0.5,
    )
    features = extractor._collect_snapshot_features(cases)
    assert "currency" in features
    assert "stage" in features


# ---------------------------------------------------------------------------
# End-to-end — /extract path drops spurious fields under flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_path_drops_spurious_fields_when_flag_on() -> None:
    """Reproduces Mümin's 2026-05-08 dev observation end-to-end.

    Pre-fix: live ``/patterns/extract`` for LATE_CHECKOUT on 323133
    surfaced ``source / status / currency / total_price`` in the
    rule conditions even after the flag was on, because
    ``PatternExtractor._infer_conditions`` did not consult the
    Sprint H whitelist.  This test pins the post-fix behaviour: the
    rule conditions only contain whitelisted features.
    """
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    cases = [
        _make_case(
            scenario=Scenario.LATE_CHECKOUT,
            pms_snapshot=_spurious_pms_snapshot(),
        )
        for _ in range(8)
    ]
    store = await _seed(cases)
    extractor = PatternExtractor(
        store=store,
        min_support=3,
        min_confidence=0.5,
    )

    result = await extractor.extract_patterns(
        scenario=Scenario.LATE_CHECKOUT,
        property_id="prop-1",
        owner_id="",
    )

    assert result.rules, "extractor produced no rule despite 8 cases"
    inform_rule = next(
        rule
        for rule in result.rules
        if rule.action.action_type is DecisionType.INFORM
    )
    condition_keys = set(inform_rule.conditions.keys())
    spurious = condition_keys & {
        "currency", "source", "status", "total_price",
    }
    assert not spurious, (
        f"spurious fields leaked into rule: {spurious}"
    )


# ---------------------------------------------------------------------------
# Property-agnostic — same filter regardless of property_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "property_id",
    ["323133", "uuid-prop-9", "tenant-2/property-7"],
)
def test_collect_filter_is_property_agnostic(property_id: str) -> None:
    """The Sprint H filter operates per-scenario, not per-property."""
    os.environ["BRAIN_SCENARIO_FEATURES_ENABLED"] = "1"
    cases = [
        _make_case(
            scenario=Scenario.ACCESS_CODE_RELEASE,
            property_id=property_id,
            pms_snapshot=_spurious_pms_snapshot(),
        )
        for _ in range(3)
    ]
    extractor = PatternExtractor(
        store=InMemoryDecisionCaseStore(),
        min_support=3,
        min_confidence=0.5,
    )
    features = extractor._collect_snapshot_features(
        cases, Scenario.ACCESS_CODE_RELEASE,
    )
    assert "currency" not in features
    assert "stage" in features
