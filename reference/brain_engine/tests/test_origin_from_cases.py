"""Tests for the ``_origin_from_cases`` helper + W5 wiring.

Sprint 6 PR-1 wires the foundation provenance trail
(:class:`PatternOrigin`) into every :class:`PatternRule` that
:class:`PatternExtractor` and :class:`PatternMiner` emit.  The
helper itself is pure: it walks the rule's supporting cases and
collects the unique ``foundation_scenario_id`` slugs in
first-occurrence order.  These tests pin:

* The helper's behaviour on legacy cases (no foundation slug ⇒
  empty origin), partially-populated batches, fully-populated
  batches with duplicates, and ordering invariants.
* End-to-end wiring: a :class:`PatternMiner` run over cases that
  carry ``foundation_scenario_id`` produces rules whose
  ``origin.foundation_scenario_ids`` reflect the inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from brain_engine.patterns.extractor import _origin_from_cases
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    PatternOrigin,
    ResolutionType,
    Scenario,
)
from brain_engine.patterns.pattern_miner import PatternMiner


def _case(
    *,
    foundation_scenario_id: str | None = None,
    case_id: str | None = None,
    action: DecisionType = DecisionType.APPROVE,
    successful: bool = True,
    origin: PatternOrigin | None = None,
) -> DecisionCase:
    """Build a minimal :class:`DecisionCase` for helper tests."""
    kwargs: dict[str, object] = {
        "case_id": case_id or "case-1",
        "stage": BookingStage.IN_STAY,
        "scenario": Scenario.EARLY_CHECKIN,
        "property_id": "prop-1",
        "owner_id": "owner-1",
        "decision": DecisionAction(action_type=action, params={}),
        "outcome": CaseOutcome(
            successful=successful,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        "foundation_scenario_id": foundation_scenario_id,
    }
    if origin is not None:
        kwargs["origin"] = origin
    return DecisionCase(**kwargs)


# ── helper behaviour ──────────────────────────────────────── #


def test_empty_iterable_yields_empty_origin() -> None:
    """An empty case list produces an empty :class:`PatternOrigin`."""
    origin = _origin_from_cases([])
    assert origin == PatternOrigin()
    assert origin.is_empty()


def test_cases_without_foundation_slug_yield_empty_origin() -> None:
    """Legacy cases that never crossed FL-16 carry no foundation slug."""
    cases = [_case(case_id=f"c{i}") for i in range(3)]
    origin = _origin_from_cases(cases)
    assert origin.is_empty()
    assert origin.foundation_scenario_ids == ()


def test_single_slug_populates_origin() -> None:
    """One case with a slug yields a one-element foundation list."""
    case = _case(foundation_scenario_id="s4_209_gas")
    origin = _origin_from_cases([case])
    assert origin.foundation_scenario_ids == ("s4_209_gas",)
    # The helper never invents source events or signals — those
    # live upstream (FL-16 orchestrator populates them).
    assert origin.source_event_ids == ()
    assert origin.contributing_signal_ids == ()


def test_duplicate_slugs_collapse_to_unique_set() -> None:
    """Repeated slugs surface once, in first-occurrence order."""
    cases = [
        _case(foundation_scenario_id="s1_1_alpha", case_id="c1"),
        _case(foundation_scenario_id="s1_1_alpha", case_id="c2"),
        _case(foundation_scenario_id="s2_5_beta", case_id="c3"),
        _case(foundation_scenario_id="s1_1_alpha", case_id="c4"),
    ]
    origin = _origin_from_cases(cases)
    assert origin.foundation_scenario_ids == (
        "s1_1_alpha",
        "s2_5_beta",
    )


def test_mixed_legacy_and_linked_cases() -> None:
    """Legacy cases are skipped without erasing linked slugs."""
    cases = [
        _case(foundation_scenario_id=None, case_id="legacy"),
        _case(foundation_scenario_id="s3_7_gamma", case_id="linked"),
        _case(foundation_scenario_id="", case_id="empty"),
    ]
    origin = _origin_from_cases(cases)
    assert origin.foundation_scenario_ids == ("s3_7_gamma",)


def test_first_occurrence_order_preserved() -> None:
    """Output order mirrors the iteration order of first sightings."""
    cases = [
        _case(foundation_scenario_id="z_last"),
        _case(foundation_scenario_id="a_first"),
        _case(foundation_scenario_id="m_middle"),
    ]
    origin = _origin_from_cases(cases)
    assert origin.foundation_scenario_ids == (
        "z_last",
        "a_first",
        "m_middle",
    )


# ── pattern_miner wiring (end-to-end) ────────────────────── #


def _approve_case(
    *,
    case_id: str,
    foundation_scenario_id: str,
    last_seen: datetime,
) -> DecisionCase:
    """Approve-action case the miner accepts as positive evidence."""
    return DecisionCase(
        case_id=case_id,
        stage=BookingStage.IN_STAY,
        scenario=Scenario.EARLY_CHECKIN,
        property_id="prop-1",
        owner_id="owner-1",
        decision=DecisionAction(
            action_type=DecisionType.APPROVE,
            params={},
        ),
        outcome=CaseOutcome(
            successful=True,
            resolution_type=ResolutionType.PM_APPROVED,
        ),
        created_at=last_seen,
        foundation_scenario_id=foundation_scenario_id,
    )


def test_pattern_miner_fills_origin_from_supporting_cases() -> None:
    """A mined rule carries the union of its source cases' slugs."""
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"approval-{i}",
            foundation_scenario_id="s1_16_early_checkin",
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules, "miner should emit at least one rule under default knobs"
    rule = rules[0]
    assert rule.origin.foundation_scenario_ids == ("s1_16_early_checkin",)


def test_pattern_miner_origin_empty_when_legacy_cases() -> None:
    """Rules mined from legacy cases keep the empty origin trail."""
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"approval-{i}",
            foundation_scenario_id="",  # legacy
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    rule = rules[0]
    assert rule.origin.is_empty()


def test_pattern_miner_origin_dedups_across_supporting_cases() -> None:
    """Multiple cases with mixed slugs produce a unique-ordered tuple."""
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    slugs = [
        "s1_16_early_checkin",
        "s1_16_early_checkin",
        "s4_209_gas",
        "s1_16_early_checkin",
        "s2_5_other",
    ]
    cases = [
        _approve_case(
            case_id=f"approval-{i}",
            foundation_scenario_id=slug,
            last_seen=base + timedelta(hours=i),
        )
        for i, slug in enumerate(slugs)
    ]
    rules, _report = miner.mine(cases)
    assert rules
    rule = rules[0]
    assert rule.origin.foundation_scenario_ids == (
        "s1_16_early_checkin",
        "s4_209_gas",
        "s2_5_other",
    )


def test_pattern_miner_no_signal_or_event_ids() -> None:
    """The miner never invents upstream event / signal ids.

    Those fields belong to FL-16 (orchestrator) populating them on
    the case before persistence.  The miner only mirrors what it
    sees — never fabricates an event id from a case id.
    """
    miner = PatternMiner()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    cases = [
        _approve_case(
            case_id=f"approval-{i}",
            foundation_scenario_id="s1_16_x",
            last_seen=base + timedelta(hours=i),
        )
        for i in range(5)
    ]
    rules, _report = miner.mine(cases)
    rule = rules[0]
    assert rule.origin.source_event_ids == ()
    assert rule.origin.contributing_signal_ids == ()


# ── round-trip through the dataclass ──────────────────────── #


def test_origin_round_trips_through_json() -> None:
    """The mined origin survives ``to_jsonable``/``from_jsonable``."""
    cases = [
        _case(foundation_scenario_id="s1_16_x"),
        _case(foundation_scenario_id="s4_209_gas", case_id="c2"),
    ]
    origin = _origin_from_cases(cases)
    payload = origin.to_jsonable()
    rebuilt = PatternOrigin.from_jsonable(payload)
    assert rebuilt == origin


@pytest.mark.parametrize(
    "slug",
    ["", None, "  "],
)
def test_blank_or_none_slugs_are_skipped(
    slug: str | None,
) -> None:
    """Empty / whitespace slugs do not enter the origin tuple."""
    cases = [
        _case(foundation_scenario_id=slug),
        _case(foundation_scenario_id="s1_real", case_id="c2"),
    ]
    origin = _origin_from_cases(cases)
    # Only the real slug should survive; whitespace-only slugs
    # bypass the helper's truthy check.
    assert "s1_real" in origin.foundation_scenario_ids


# ── source_event_ids + contributing_signal_ids aggregation ─── #
#
# Mümin 2026-05-15 round-5 #3 — once PR-B persists the
# orchestrator's :class:`PatternOrigin` on each case, the miner
# must aggregate ``source_event_ids`` and
# ``contributing_signal_ids`` from those cases onto the resulting
# rule.  Otherwise the FL-12 ``/rules/{id}/origin`` endpoint keeps
# returning empty arrays even after PR-B.


def test_source_event_ids_aggregated_across_cases() -> None:
    """Every unique upstream event id surfaces on the rule origin."""
    cases = [
        _case(
            case_id="c1",
            origin=PatternOrigin(source_event_ids=("evt-1",)),
        ),
        _case(
            case_id="c2",
            origin=PatternOrigin(source_event_ids=("evt-2",)),
        ),
        _case(
            case_id="c3",
            origin=PatternOrigin(source_event_ids=("evt-3",)),
        ),
    ]
    origin = _origin_from_cases(cases)
    assert origin.source_event_ids == ("evt-1", "evt-2", "evt-3")


def test_source_event_ids_dedup_first_occurrence_wins() -> None:
    """Duplicate event ids collapse; order matches first sighting."""
    cases = [
        _case(
            case_id="c1",
            origin=PatternOrigin(source_event_ids=("evt-1", "evt-2")),
        ),
        _case(
            case_id="c2",
            origin=PatternOrigin(source_event_ids=("evt-2", "evt-3")),
        ),
        _case(
            case_id="c3",
            origin=PatternOrigin(source_event_ids=("evt-1",)),
        ),
    ]
    origin = _origin_from_cases(cases)
    assert origin.source_event_ids == ("evt-1", "evt-2", "evt-3")


def test_contributing_signal_ids_aggregated_across_cases() -> None:
    """Signal ids follow the same dedup / first-occurrence rules."""
    cases = [
        _case(
            case_id="c1",
            origin=PatternOrigin(
                contributing_signal_ids=("sig-a",),
            ),
        ),
        _case(
            case_id="c2",
            origin=PatternOrigin(
                contributing_signal_ids=("sig-b", "sig-a"),
            ),
        ),
    ]
    origin = _origin_from_cases(cases)
    assert origin.contributing_signal_ids == ("sig-a", "sig-b")


def test_legacy_case_without_origin_does_not_break_aggregation() -> None:
    """Cases whose ``origin`` was left at the empty default skip cleanly."""
    cases = [
        _case(case_id="legacy-1"),  # empty origin
        _case(
            case_id="linked",
            foundation_scenario_id="s1_16_early_checkin",
            origin=PatternOrigin(
                foundation_scenario_ids=("s1_16_early_checkin",),
                source_event_ids=("evt-from-orchestrator",),
            ),
        ),
        _case(case_id="legacy-2"),  # empty origin
    ]
    origin = _origin_from_cases(cases)
    assert origin.foundation_scenario_ids == ("s1_16_early_checkin",)
    assert origin.source_event_ids == ("evt-from-orchestrator",)
    assert origin.contributing_signal_ids == ()


def test_empty_event_id_strings_are_skipped() -> None:
    """Whitespace / empty event ids never enter the rule origin."""
    cases = [
        _case(
            case_id="c1",
            origin=PatternOrigin(
                source_event_ids=("", "evt-real"),
            ),
        ),
    ]
    origin = _origin_from_cases(cases)
    assert origin.source_event_ids == ("evt-real",)
