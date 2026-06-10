"""Tests for the Foundation Analysis Orchestrator (FL-16).

Pins the Sprint 2 pipeline contract:

* ``match_foundation`` consults the wired matcher and catalog; an
  unwired matcher / blank text yields an empty match.
* ``log_origin`` always emits a :class:`PatternOrigin` containing
  the event id under ``source_event_ids`` so the FL-12 endpoint
  has something to render even before the matcher returns
  candidates.
* The three stubs (``guardrail``, ``mine``, ``route``) return the
  safe defaults that keep current call-site behaviour unchanged
  until FL-04 / FL-05 fill them in.
* The orchestrator never raises on malformed matcher / catalog
  output — it logs and falls back to the empty case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import pytest

from brain_engine.analysis import (
    AnalysisEvent,
    AnalysisEventType,
    AnalysisResult,
    FoundationAnalysisOrchestrator,
    MemoryTier,
    memory_type_label_to_tier,
)
from brain_engine.analysis.orchestrator import (
    FoundationCatalogFacade,
    ScenarioMatcherFacade,
)
from brain_engine.patterns.foundation_registry import FoundationScenario
from brain_engine.patterns.models import PatternOrigin

# ── fixtures ──────────────────────────────────────────────── #


_NOW: Final[datetime] = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _MatcherCandidate:
    """Minimal :class:`ScenarioCandidate`-compatible test object."""

    scenario_id: str
    similarity: float


class _StubMatcher:
    """Hand-built :class:`ScenarioMatcherFacade` for tests.

    Returns a deterministic ordered tuple of candidates so the
    orchestrator's behaviour is reproducible.
    """

    def __init__(
        self,
        candidates: tuple[_MatcherCandidate, ...],
    ) -> None:
        self._candidates = candidates

    def top_k(
        self,
        text: str,
        *,
        k: int = 5,
    ) -> tuple[_MatcherCandidate, ...]:
        del text
        return self._candidates[:k]


class _RaisingMatcher:
    """A matcher that always raises ``RuntimeError`` from ``top_k``."""

    def top_k(self, text: str, *, k: int = 5) -> tuple[_MatcherCandidate, ...]:
        del text, k
        raise RuntimeError("matcher unavailable")


class _StubCatalog:
    """In-memory catalog with a known set of scenarios."""

    def __init__(
        self,
        entries: dict[str, FoundationScenario],
    ) -> None:
        self._entries = entries

    async def get(self, scenario_id: str) -> FoundationScenario | None:
        return self._entries.get(scenario_id)


def _make_event(
    *,
    event_id: str = "evt-1",
    text: str = "Guest asks about parking",
    event_type: AnalysisEventType = AnalysisEventType.MESSAGE,
) -> AnalysisEvent:
    return AnalysisEvent(
        event_id=event_id,
        event_type=event_type,
        property_id="prop-123",
        occurred_at=_NOW,
        text=text,
    )


def _make_scenario(scenario_id: str, title: str) -> FoundationScenario:
    return FoundationScenario(
        scenario_id=scenario_id,
        title=title,
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger body",
        risk_level="Low",
    )


# ── Protocol compatibility ────────────────────────────────── #


def test_stub_matcher_satisfies_protocol() -> None:
    """The hand-built matcher satisfies the runtime Protocol."""
    stub = _StubMatcher(())
    assert isinstance(stub, ScenarioMatcherFacade)


def test_stub_catalog_satisfies_protocol() -> None:
    """The hand-built catalog satisfies the runtime Protocol."""
    stub = _StubCatalog({})
    assert isinstance(stub, FoundationCatalogFacade)


# ── pipeline behaviour ────────────────────────────────────── #


@pytest.mark.asyncio
async def test_analyze_with_no_matcher_returns_empty_match() -> None:
    """Unwired matcher ⇒ empty match, origin still has event id."""
    orchestrator = FoundationAnalysisOrchestrator()
    event = _make_event()
    result = await orchestrator.analyze(event)

    assert isinstance(result, AnalysisResult)
    assert result.event_id == event.event_id
    assert result.foundation_match.is_empty
    assert result.origin.foundation_scenario_ids == ()
    assert result.origin.source_event_ids == (event.event_id,)


@pytest.mark.asyncio
async def test_analyze_with_blank_text_skips_matcher() -> None:
    """Blank or whitespace-only text never reaches the matcher."""
    matcher = _StubMatcher((_MatcherCandidate("s1_1_a", 0.99),))
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
    )
    event = _make_event(text="   ")
    result = await orchestrator.analyze(event)
    assert result.foundation_match.is_empty
    assert result.origin.foundation_scenario_ids == ()


@pytest.mark.asyncio
async def test_analyze_with_matcher_only_returns_slug_match() -> None:
    """Without a catalog, candidates carry slug + similarity only."""
    matcher = _StubMatcher(
        (
            _MatcherCandidate("s1_1_a", 0.9),
            _MatcherCandidate("s1_2_b", 0.8),
        ),
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
    )
    result = await orchestrator.analyze(_make_event())
    match = result.foundation_match
    assert not match.is_empty
    assert len(match.candidates) == 2
    assert match.candidates[0].scenario_id == "s1_1_a"
    assert match.candidates[0].similarity == 0.9
    assert match.candidates[0].catalog_entry is None
    assert match.dominant_scenario_id == "s1_1_a"
    assert match.dominant_catalog_entry is None
    assert result.origin.foundation_scenario_ids == (
        "s1_1_a",
        "s1_2_b",
    )


@pytest.mark.asyncio
async def test_analyze_with_catalog_enriches_dominant_match() -> None:
    """When the catalog is wired, the dominant entry is enriched."""
    matcher = _StubMatcher(
        (
            _MatcherCandidate("s1_1_a", 0.9),
            _MatcherCandidate("s1_2_b", 0.8),
        ),
    )
    catalog = _StubCatalog(
        {
            "s1_1_a": _make_scenario("s1_1_a", "First scenario"),
            "s1_2_b": _make_scenario("s1_2_b", "Second scenario"),
        },
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    match = result.foundation_match
    assert match.candidates[0].catalog_entry is not None
    assert match.candidates[0].catalog_entry.title == "First scenario"
    assert match.dominant_catalog_entry is not None
    assert match.dominant_catalog_entry.title == "First scenario"


@pytest.mark.asyncio
async def test_analyze_with_unknown_slug_still_returns_candidate() -> None:
    """A slug missing from the catalog yields a slug-only candidate."""
    matcher = _StubMatcher(
        (_MatcherCandidate("s1_99_unknown", 0.7),),
    )
    catalog = _StubCatalog({})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert len(result.foundation_match.candidates) == 1
    assert result.foundation_match.candidates[0].catalog_entry is None


@pytest.mark.asyncio
async def test_analyze_recovers_from_matcher_failure() -> None:
    """A raising matcher never propagates out of the pipeline."""
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=_RaisingMatcher(),
    )
    result = await orchestrator.analyze(_make_event())
    assert result.foundation_match.is_empty
    assert result.origin.source_event_ids == ("evt-1",)


@pytest.mark.asyncio
async def test_analyze_without_catalog_emits_no_decisions() -> None:
    """Without a wired catalog every gate stays conservative.

    The orchestrator must not invent decisions when the foundation
    has nothing to say — both guardrail and learning gates stay
    silent and the memory routes stay empty.  Matches the
    pre-orchestrator behaviour so existing call sites see no
    regression until FL-05b wires them in.
    """
    orchestrator = FoundationAnalysisOrchestrator()
    result = await orchestrator.analyze(_make_event())
    assert result.guardrail_block is False
    assert result.pattern_candidate_emitted is False
    assert result.memory_routes == ()


@pytest.mark.asyncio
async def test_analyze_respects_matcher_top_k() -> None:
    """``matcher_top_k`` constrains how many candidates are recorded."""
    matcher = _StubMatcher(
        tuple(
            _MatcherCandidate(f"s1_{i}_x", 1.0 - i * 0.05) for i in range(7)
        ),
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        matcher_top_k=3,
    )
    result = await orchestrator.analyze(_make_event())
    assert len(result.foundation_match.candidates) == 3
    assert result.origin.foundation_scenario_ids == (
        "s1_0_x",
        "s1_1_x",
        "s1_2_x",
    )


def test_orchestrator_rejects_non_positive_top_k() -> None:
    """``matcher_top_k`` must be positive."""
    with pytest.raises(ValueError, match="matcher_top_k"):
        FoundationAnalysisOrchestrator(matcher_top_k=0)


# ── AnalysisEvent invariants ──────────────────────────────── #


def test_analysis_event_requires_event_id() -> None:
    """An empty event id raises at construction."""
    with pytest.raises(ValueError, match="event_id"):
        AnalysisEvent(
            event_id="",
            event_type=AnalysisEventType.MESSAGE,
            property_id="p",
            occurred_at=_NOW,
        )


@pytest.mark.asyncio
async def test_origin_round_trips_through_pattern_origin() -> None:
    """The origin emitted by the orchestrator survives JSONB roundtrip."""
    matcher = _StubMatcher((_MatcherCandidate("s1_1_a", 0.9),))
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
    )
    result = await orchestrator.analyze(_make_event(event_id="evt-42"))
    payload = result.origin.to_jsonable()
    rebuilt = PatternOrigin.from_jsonable(payload)
    assert rebuilt == result.origin


# ── FL-04: memory tier routing ────────────────────────────── #


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Property knowledge", MemoryTier.PROPERTY_KNOWLEDGE),
        ("PM preference memory", MemoryTier.PM_PREFERENCE_MEMORY),
        ("PM behavior memory", MemoryTier.PM_BEHAVIOR_MEMORY),
        (
            "Reservation context memory",
            MemoryTier.RESERVATION_CONTEXT_MEMORY,
        ),
        ("Guest profile memory", MemoryTier.GUEST_PROFILE_MEMORY),
        ("Guest risk memory", MemoryTier.GUEST_RISK_MEMORY),
        ("Owner preference memory", MemoryTier.OWNER_PREFERENCE_MEMORY),
        ("Vendor memory", MemoryTier.VENDOR_MEMORY),
        ("Task workflow memory", MemoryTier.TASK_WORKFLOW_MEMORY),
        (
            "Operational workflow memory",
            MemoryTier.OPERATIONAL_WORKFLOW_MEMORY,
        ),
        (
            "Channel-specific behavior memory",
            MemoryTier.CHANNEL_SPECIFIC_BEHAVIOR_MEMORY,
        ),
        ("Missing-info registry", MemoryTier.MISSING_INFO_REGISTRY),
        ("SOP candidate memory", MemoryTier.SOP_CANDIDATE_MEMORY),
    ],
)
def test_memory_type_label_to_tier_covers_all_thirteen(
    label: str,
    expected: MemoryTier,
) -> None:
    """Every catalog-observed Memory Type label maps to the right tier.

    The thirteen labels come straight from the FL-01 grep against
    the foundation MD; together they account for every Memory Type
    bullet the catalog emits.  A regression on this mapping would
    break the orchestrator's route output for whichever label was
    affected.
    """
    assert memory_type_label_to_tier(label) == expected


def test_memory_type_label_to_tier_is_case_insensitive() -> None:
    """Capitalisation drift in the MD must not change the routing."""
    assert (
        memory_type_label_to_tier("PROPERTY KNOWLEDGE")
        == MemoryTier.PROPERTY_KNOWLEDGE
    )
    assert (
        memory_type_label_to_tier("  guest profile memory  ")
        == MemoryTier.GUEST_PROFILE_MEMORY
    )


def test_memory_type_label_to_tier_returns_none_for_unknown() -> None:
    """An unknown label collapses to ``None`` instead of raising."""
    assert memory_type_label_to_tier("Made-up Memory Type") is None
    assert memory_type_label_to_tier("") is None


@pytest.mark.asyncio
async def test_route_to_memory_returns_empty_without_catalog_entry() -> None:
    """No catalog entry on the match ⇒ no memory routes."""
    matcher = _StubMatcher((_MatcherCandidate("s1_1_a", 0.9),))
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.memory_routes == ()


@pytest.mark.asyncio
async def test_route_to_memory_emits_tier_slugs() -> None:
    """A wired catalog entry maps memory_types to tier slugs."""
    scenario = FoundationScenario(
        scenario_id="s4_209_gas",
        title="Gas smell",
        stage_number=4,
        stage_label="Check-In Day",
        trigger="trigger",
        risk_level="Critical",
        memory_types=("Property knowledge", "Reservation context memory"),
    )
    matcher = _StubMatcher((_MatcherCandidate("s4_209_gas", 0.95),))
    catalog = _StubCatalog({"s4_209_gas": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.memory_routes == (
        MemoryTier.PROPERTY_KNOWLEDGE.value,
        MemoryTier.RESERVATION_CONTEXT_MEMORY.value,
    )


@pytest.mark.asyncio
async def test_route_to_memory_deduplicates_repeated_labels() -> None:
    """A catalog row that lists the same label twice yields one slug."""
    scenario = FoundationScenario(
        scenario_id="s1_1_dup",
        title="Dup labels",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger",
        memory_types=(
            "Property knowledge",
            "Property knowledge",  # duplicate intentionally
            "Guest profile memory",
        ),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_dup", 0.9),))
    catalog = _StubCatalog({"s1_1_dup": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.memory_routes == (
        MemoryTier.PROPERTY_KNOWLEDGE.value,
        MemoryTier.GUEST_PROFILE_MEMORY.value,
    )


@pytest.mark.asyncio
async def test_route_to_memory_skips_unknown_labels() -> None:
    """Catalog drift produces a warning but does not break routing."""
    scenario = FoundationScenario(
        scenario_id="s1_1_drift",
        title="Catalog drift example",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger",
        memory_types=(
            "Property knowledge",
            "Made-up Memory Type",  # unknown label
            "Vendor memory",
        ),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_drift", 0.9),))
    catalog = _StubCatalog({"s1_1_drift": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    # Unknown label is dropped; valid neighbours survive.
    assert result.memory_routes == (
        MemoryTier.PROPERTY_KNOWLEDGE.value,
        MemoryTier.VENDOR_MEMORY.value,
    )


# ── FL-05: safety gating ──────────────────────────────────── #


def _make_scenario_with_flags(
    *,
    scenario_id: str,
    should_auto_reply: str,
    should_learn_pattern: str,
    memory_types: tuple[str, ...] = (),
) -> FoundationScenario:
    """Build a :class:`FoundationScenario` with the safety flags set."""
    return FoundationScenario(
        scenario_id=scenario_id,
        title="Test scenario",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger",
        should_auto_reply=should_auto_reply,
        should_learn_pattern=should_learn_pattern,
        memory_types=memory_types,
    )


@pytest.mark.asyncio
async def test_guardrail_blocks_when_foundation_says_no() -> None:
    """A foundation entry with auto-reply=No blocks the event."""
    scenario = _make_scenario_with_flags(
        scenario_id="s4_209_gas",
        should_auto_reply="No",
        should_learn_pattern="No",
    )
    matcher = _StubMatcher((_MatcherCandidate("s4_209_gas", 0.95),))
    catalog = _StubCatalog({"s4_209_gas": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.guardrail_block is True


@pytest.mark.asyncio
async def test_guardrail_does_not_block_on_conditional() -> None:
    """Conditional auto-reply is not a hard block."""
    scenario = _make_scenario_with_flags(
        scenario_id="s1_16_early_checkin",
        should_auto_reply="Conditional",
        should_learn_pattern="Yes",
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s1_16_early_checkin", 0.9),),
    )
    catalog = _StubCatalog({"s1_16_early_checkin": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.guardrail_block is False


@pytest.mark.asyncio
async def test_guardrail_does_not_block_on_yes() -> None:
    """Explicit auto-reply Yes never blocks."""
    scenario = _make_scenario_with_flags(
        scenario_id="s1_12_parking",
        should_auto_reply="Yes",
        should_learn_pattern="Yes",
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s1_12_parking", 0.9),),
    )
    catalog = _StubCatalog({"s1_12_parking": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.guardrail_block is False


@pytest.mark.asyncio
async def test_mine_emits_when_foundation_says_yes() -> None:
    """``Should AI Learn Pattern: Yes`` ⇒ candidate is emitted."""
    scenario = _make_scenario_with_flags(
        scenario_id="s1_16_early_checkin",
        should_auto_reply="Conditional",
        should_learn_pattern="Yes",
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s1_16_early_checkin", 0.9),),
    )
    catalog = _StubCatalog({"s1_16_early_checkin": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.pattern_candidate_emitted is True


@pytest.mark.asyncio
async def test_mine_skipped_when_foundation_says_no() -> None:
    """Safety-only Critical scenarios must never become candidates.

    Pins the six pure-safety scenarios from the foundation MD that
    carry ``Should AI Learn Pattern: No``: gas smell, broken
    glass, medical, safety/security, CO alarm, post-stay injury.
    The orchestrator must not promote them as learning candidates
    even when ``Should AI Auto-Reply`` would have been ``No``
    (the guardrail short-circuit) or unspecified.
    """
    scenario = _make_scenario_with_flags(
        scenario_id="s4_209_gas",
        should_auto_reply="No",
        should_learn_pattern="No",
    )
    matcher = _StubMatcher((_MatcherCandidate("s4_209_gas", 0.95),))
    catalog = _StubCatalog({"s4_209_gas": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.pattern_candidate_emitted is False


@pytest.mark.asyncio
async def test_mine_skipped_when_guardrail_blocks() -> None:
    """Guardrail block short-circuits learning even when foundation says Yes.

    Defence in depth: a foundation entry that allows learning but
    forbids auto-reply must not silently emit a learning candidate
    — safety beats learnability when they conflict.  Verified
    against a synthetic scenario where the catalog is internally
    inconsistent on the two flags (auto-reply No + learn Yes).
    """
    scenario = _make_scenario_with_flags(
        scenario_id="s99_synthetic_inconsistent",
        should_auto_reply="No",
        should_learn_pattern="Yes",
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s99_synthetic_inconsistent", 0.9),),
    )
    catalog = _StubCatalog(
        {"s99_synthetic_inconsistent": scenario},
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.guardrail_block is True
    assert result.pattern_candidate_emitted is False


@pytest.mark.asyncio
async def test_safety_flags_are_case_insensitive() -> None:
    """Lower/upper case + whitespace in MD flag does not flip the gate."""
    scenario = _make_scenario_with_flags(
        scenario_id="s1_1_case",
        should_auto_reply="  NO  ",
        should_learn_pattern="yes",
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_case", 0.9),))
    catalog = _StubCatalog({"s1_1_case": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.guardrail_block is True
    # auto-reply No blocks → mine short-circuits to False
    assert result.pattern_candidate_emitted is False


@pytest.mark.asyncio
async def test_live_foundation_safety_six_scenarios_disable_learning() -> None:
    """Live foundation: the 6 safety-only Critical scenarios disable learning.

    Cross-checks the in-process orchestrator against the actual
    catalog produced by the FL-01 parser.  If any of the six
    scenarios drift in the MD (rename / removed entry / changed
    flag), this test surfaces the regression immediately.
    """
    from pathlib import Path

    from brain_engine.patterns.foundation_registry import (
        load_foundation_scenarios,
    )

    repo_root = Path(__file__).resolve().parents[1]
    md_path = (
        repo_root
        / "Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_"
        "Foundation.md"
    )
    if not md_path.is_file():
        pytest.skip("foundation markdown not present")
    catalog = {s.scenario_id: s for s in load_foundation_scenarios(md_path)}
    safety_ids = {
        "s4_209_guest_reports_gas_smell",
        "s4_211_guest_reports_broken_glass_or_injury",
        "s5_241_guest_asks_for_medical_help",
        "s5_242_guest_reports_safety_or_security_concern",
        "s5_295_guest_reports_carbon_monoxide_alarm",
        "s8_412_guest_reports_injury_after_stay",
    }
    for sid in safety_ids:
        scenario = catalog[sid]
        matcher = _StubMatcher((_MatcherCandidate(sid, 0.95),))
        stub_catalog = _StubCatalog({sid: scenario})
        orchestrator = FoundationAnalysisOrchestrator(
            scenario_matcher=matcher,
            foundation_catalog=stub_catalog,
        )
        result = await orchestrator.analyze(_make_event())
        assert result.pattern_candidate_emitted is False, (
            f"{sid} must not emit a learning candidate"
        )
        assert result.guardrail_block is True, f"{sid} must block auto-reply"


@pytest.mark.asyncio
async def test_route_to_memory_preserves_catalog_order() -> None:
    """The output order mirrors the catalog ``memory_types`` order."""
    scenario = FoundationScenario(
        scenario_id="s1_1_order",
        title="Order check",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger",
        memory_types=(
            "Vendor memory",
            "Property knowledge",
            "Owner preference memory",
        ),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_order", 0.9),))
    catalog = _StubCatalog({"s1_1_order": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.memory_routes == (
        MemoryTier.VENDOR_MEMORY.value,
        MemoryTier.PROPERTY_KNOWLEDGE.value,
        MemoryTier.OWNER_PREFERENCE_MEMORY.value,
    )


# ── Q5-A: Foundation Layer similarity gate ────────────────── #


def _make_gate_scenario(scenario_id: str) -> FoundationScenario:
    """Build a scenario that would trip every downstream gate.

    The flags below force ``_apply_guardrails`` to return ``True``
    and ``_mine_if_learnable`` to return ``True`` whenever the
    catalog entry survives the similarity gate.  This lets the
    tests prove that clearing ``dominant_catalog_entry`` actually
    short-circuits the policy steps to their safe defaults.
    """
    return FoundationScenario(
        scenario_id=scenario_id,
        title="Gate trip scenario",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger body",
        should_auto_reply="No",
        should_learn_pattern="Yes",
        memory_types=("Property knowledge",),
    )


@pytest.mark.asyncio
async def test_similarity_gate_clears_dominant_when_below_threshold() -> None:
    """Below-floor similarity ⇒ dominant entry cleared, candidates kept."""
    scenario = _make_gate_scenario("s1_99_weak_match")
    matcher = _StubMatcher((_MatcherCandidate("s1_99_weak_match", 0.30),))
    catalog = _StubCatalog({"s1_99_weak_match": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.45,
    )
    result = await orchestrator.analyze(_make_event())
    match = result.foundation_match
    assert match.dominant_catalog_entry is None
    assert match.dominant_scenario_id == "s1_99_weak_match"
    assert len(match.candidates) == 1
    assert match.candidates[0].catalog_entry is not None


@pytest.mark.asyncio
async def test_similarity_gate_keeps_dominant_when_above_threshold() -> None:
    """At-or-above-floor similarity ⇒ catalog entry survives."""
    scenario = _make_gate_scenario("s1_1_strong_match")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_strong_match", 0.92),))
    catalog = _StubCatalog({"s1_1_strong_match": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.45,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.foundation_match.dominant_catalog_entry is not None
    assert (
        result.foundation_match.dominant_catalog_entry.scenario_id
        == "s1_1_strong_match"
    )


@pytest.mark.asyncio
async def test_similarity_gate_is_inclusive_at_threshold() -> None:
    """Exactly-at-threshold similarity must NOT trip the gate.

    The gate uses ``<`` so a candidate sitting on the floor is
    treated as good enough.  Pins the comparison so a future
    refactor that flips to ``<=`` is caught immediately.
    """
    scenario = _make_gate_scenario("s1_1_floor")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_floor", 0.45),))
    catalog = _StubCatalog({"s1_1_floor": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.45,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.foundation_match.dominant_catalog_entry is not None


@pytest.mark.asyncio
async def test_similarity_gate_short_circuits_downstream_steps() -> None:
    """Cleared dominant ⇒ guardrail / mine / route all stay silent.

    Proves the gate's promise: a weak match never blocks auto-reply,
    never promotes a learning candidate, and never fans out to a
    memory tier — even though the catalog row is configured to
    trigger every one of those when the gate lets it through.
    """
    scenario = _make_gate_scenario("s1_99_weak")
    matcher = _StubMatcher((_MatcherCandidate("s1_99_weak", 0.20),))
    catalog = _StubCatalog({"s1_99_weak": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.45,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.guardrail_block is False
    assert result.pattern_candidate_emitted is False
    assert result.memory_routes == ()


@pytest.mark.asyncio
async def test_similarity_gate_preserves_origin_trail() -> None:
    """Origin trail records every candidate slug regardless of gate.

    Observability invariant: even when a match is too weak to drive
    policy, the FL-12 origin still captures every slug the matcher
    returned so an audit can replay why the orchestrator hesitated.
    """
    candidates = (
        _MatcherCandidate("s1_99_weak_a", 0.30),
        _MatcherCandidate("s1_99_weak_b", 0.28),
    )
    matcher = _StubMatcher(candidates)
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        min_similarity=0.45,
    )
    result = await orchestrator.analyze(_make_event(event_id="evt-trace"))
    assert result.origin.foundation_scenario_ids == (
        "s1_99_weak_a",
        "s1_99_weak_b",
    )
    assert result.origin.source_event_ids == ("evt-trace",)


@pytest.mark.asyncio
async def test_similarity_gate_reads_env_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``min_similarity=None`` reads FOUNDATION_MIN_SIMILARITY env."""
    monkeypatch.setenv("FOUNDATION_MIN_SIMILARITY", "0.80")
    scenario = _make_gate_scenario("s1_1_mid")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_mid", 0.60),))
    catalog = _StubCatalog({"s1_1_mid": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    # 0.60 < 0.80 ⇒ gate trips
    assert result.foundation_match.dominant_catalog_entry is None


@pytest.mark.asyncio
async def test_similarity_gate_env_default_unset_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset env var ⇒ default 0.45 floor applies."""
    monkeypatch.delenv("FOUNDATION_MIN_SIMILARITY", raising=False)
    scenario = _make_gate_scenario("s1_1_mid")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_mid", 0.50),))
    catalog = _StubCatalog({"s1_1_mid": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    # 0.50 >= 0.45 default ⇒ entry survives
    assert result.foundation_match.dominant_catalog_entry is not None


@pytest.mark.asyncio
async def test_similarity_gate_env_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparsable env value ⇒ default 0.45 floor applies, no crash."""
    monkeypatch.setenv("FOUNDATION_MIN_SIMILARITY", "not-a-number")
    scenario = _make_gate_scenario("s1_1_mid")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_mid", 0.50),))
    catalog = _StubCatalog({"s1_1_mid": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    # 0.50 >= 0.45 default ⇒ entry survives despite junk env
    assert result.foundation_match.dominant_catalog_entry is not None


@pytest.mark.asyncio
async def test_similarity_gate_env_blank_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace env value ⇒ default 0.45 floor applies."""
    monkeypatch.setenv("FOUNDATION_MIN_SIMILARITY", "   ")
    scenario = _make_gate_scenario("s1_1_mid")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_mid", 0.30),))
    catalog = _StubCatalog({"s1_1_mid": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event())
    # 0.30 < 0.45 default ⇒ entry cleared
    assert result.foundation_match.dominant_catalog_entry is None


@pytest.mark.asyncio
async def test_similarity_gate_constructor_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``min_similarity=`` wins over the env var."""
    monkeypatch.setenv("FOUNDATION_MIN_SIMILARITY", "0.99")
    scenario = _make_gate_scenario("s1_1_mid")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_mid", 0.60),))
    catalog = _StubCatalog({"s1_1_mid": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.50,
    )
    result = await orchestrator.analyze(_make_event())
    # 0.60 >= 0.50 explicit ⇒ entry survives; env 0.99 ignored
    assert result.foundation_match.dominant_catalog_entry is not None


@pytest.mark.asyncio
async def test_similarity_gate_zero_disables_floor() -> None:
    """``min_similarity=0`` admits every non-negative similarity."""
    scenario = _make_gate_scenario("s1_1_any")
    matcher = _StubMatcher((_MatcherCandidate("s1_1_any", 0.01),))
    catalog = _StubCatalog({"s1_1_any": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.0,
    )
    result = await orchestrator.analyze(_make_event())
    assert result.foundation_match.dominant_catalog_entry is not None


# ── Q5-B: required_data presence gate ─────────────────────── #


def _make_required_data_scenario(
    scenario_id: str,
    required_data_checks: tuple[str, ...],
) -> FoundationScenario:
    """Build a scenario that wants the given required_data checks.

    Sets ``should_learn_pattern=Yes`` so the mine step would
    promote the case in absence of Q5-B gating — that lets tests
    isolate the Q5-B behaviour from the FL-05 learn-pattern flag.
    """
    return FoundationScenario(
        scenario_id=scenario_id,
        title="Q5-B test scenario",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger body",
        should_learn_pattern="Yes",
        required_data_checks=required_data_checks,
    )


def _make_event_with_snapshots(
    *,
    event_id: str = "evt-q5b",
    pms_snapshot: dict | None = None,
    calendar_snapshot: dict | None = None,
    ops_snapshot: dict | None = None,
    guest_snapshot: dict | None = None,
) -> AnalysisEvent:
    """Build an event with arbitrary snapshot dicts."""
    return AnalysisEvent(
        event_id=event_id,
        event_type=AnalysisEventType.MESSAGE,
        property_id="prop-q5b",
        occurred_at=_NOW,
        text="Q5-B event text",
        pms_snapshot=pms_snapshot or {},
        calendar_snapshot=calendar_snapshot or {},
        ops_snapshot=ops_snapshot or {},
        guest_snapshot=guest_snapshot or {},
    )


@pytest.mark.asyncio
async def test_q5b_no_required_data_means_no_missing() -> None:
    """Catalog entry without required_data_checks ⇒ empty tuple."""
    scenario = _make_required_data_scenario("s1_1_no_checks", ())
    matcher = _StubMatcher((_MatcherCandidate("s1_1_no_checks", 0.9),))
    catalog = _StubCatalog({"s1_1_no_checks": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event_with_snapshots())
    assert result.missing_required_data == ()


@pytest.mark.asyncio
async def test_q5b_empty_snapshots_report_mapped_labels_missing() -> None:
    """Mapped label + empty snapshot ⇒ label lands in missing tuple."""
    scenario = _make_required_data_scenario(
        "s1_1_needs_data",
        ("PMS reservation", "arrival time"),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_needs_data", 0.9),))
    catalog = _StubCatalog({"s1_1_needs_data": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    result = await orchestrator.analyze(_make_event_with_snapshots())
    assert "PMS reservation" in result.missing_required_data
    assert "arrival time" in result.missing_required_data


@pytest.mark.asyncio
async def test_q5b_satisfied_snapshots_yield_empty_missing() -> None:
    """Snapshots covering every mapped label ⇒ no missing."""
    scenario = _make_required_data_scenario(
        "s1_1_satisfied",
        ("PMS reservation", "arrival time", "cleaning schedule"),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_satisfied", 0.9),))
    catalog = _StubCatalog({"s1_1_satisfied": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    event = _make_event_with_snapshots(
        pms_snapshot={"reservation_id": "r1"},
        calendar_snapshot={"arrival_time": "15:00"},
        ops_snapshot={"cleaner_eta": "12:00"},
    )
    result = await orchestrator.analyze(event)
    assert result.missing_required_data == ()


@pytest.mark.asyncio
async def test_q5b_unmapped_labels_never_gate() -> None:
    """Knowledge / policy labels stay observed-only (no gate trip)."""
    # Every required label is unmapped (Q5-B.2 candidates).
    scenario = _make_required_data_scenario(
        "s1_1_only_unmapped",
        ("house rules", "property sop", "compensation policy"),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_only_unmapped", 0.9),))
    catalog = _StubCatalog({"s1_1_only_unmapped": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    # Empty snapshots — none of the unmapped labels are reported,
    # and pattern_candidate is preserved.
    result = await orchestrator.analyze(_make_event_with_snapshots())
    assert result.missing_required_data == ()
    assert result.pattern_candidate_emitted is True


@pytest.mark.asyncio
async def test_q5b_missing_required_data_blocks_pattern_candidate() -> None:
    """Even a single missing mapped label flips learn_gate to False."""
    scenario = _make_required_data_scenario(
        "s1_1_needs_one",
        ("PMS reservation",),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_needs_one", 0.9),))
    catalog = _StubCatalog({"s1_1_needs_one": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    # PMS snapshot empty, foundation says Learn Pattern: Yes.
    result = await orchestrator.analyze(_make_event_with_snapshots())
    assert result.missing_required_data == ("PMS reservation",)
    assert result.pattern_candidate_emitted is False


@pytest.mark.asyncio
async def test_q5b_satisfied_data_keeps_pattern_candidate() -> None:
    """All required mapped labels covered ⇒ learning gate passes."""
    scenario = _make_required_data_scenario(
        "s1_1_ok",
        ("PMS reservation",),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_ok", 0.9),))
    catalog = _StubCatalog({"s1_1_ok": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    event = _make_event_with_snapshots(
        pms_snapshot={"reservation_id": "r1"},
    )
    result = await orchestrator.analyze(event)
    assert result.missing_required_data == ()
    assert result.pattern_candidate_emitted is True


@pytest.mark.asyncio
async def test_q5b_similarity_gate_trip_skips_required_data_check() -> None:
    """Q5-A clears catalog entry ⇒ Q5-B has nothing to validate."""
    scenario = _make_required_data_scenario(
        "s1_1_weak",
        ("PMS reservation",),
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_weak", 0.20),))
    catalog = _StubCatalog({"s1_1_weak": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.45,
    )
    # Below threshold ⇒ dominant_catalog_entry cleared by Q5-A ⇒
    # Q5-B sees nothing to validate, returns empty tuple.
    result = await orchestrator.analyze(_make_event_with_snapshots())
    assert result.missing_required_data == ()


@pytest.mark.asyncio
async def test_q5b_unwired_matcher_yields_empty_missing() -> None:
    """No matcher ⇒ no catalog entry ⇒ nothing to validate."""
    orchestrator = FoundationAnalysisOrchestrator()
    result = await orchestrator.analyze(_make_event_with_snapshots())
    assert result.missing_required_data == ()


@pytest.mark.asyncio
async def test_q5b_event_backward_compat_without_snapshots() -> None:
    """Pre-Q5-B callers that omit snapshots see no behaviour change.

    Default-empty snapshots + no required_data_checks on catalog
    ⇒ missing tuple stays empty, learning gate stays controlled
    by the existing FL-05 should_learn_pattern logic.
    """
    scenario = FoundationScenario(
        scenario_id="s1_1_legacy",
        title="Legacy",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger body",
        should_learn_pattern="Yes",
    )
    matcher = _StubMatcher((_MatcherCandidate("s1_1_legacy", 0.9),))
    catalog = _StubCatalog({"s1_1_legacy": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    # Use the legacy _make_event() helper — no snapshot kwargs.
    result = await orchestrator.analyze(_make_event())
    assert result.missing_required_data == ()
    assert result.pattern_candidate_emitted is True


# ── Q5-C: stage contradiction detection ───────────────────── #


def _make_stage_scenario(
    *,
    scenario_id: str,
    stage_number: int,
    should_learn: str = "Yes",
) -> FoundationScenario:
    """Build a scenario at the given catalog stage_number."""
    return FoundationScenario(
        scenario_id=scenario_id,
        title="Q5-C test scenario",
        stage_number=stage_number,
        stage_label="Test stage",
        trigger="trigger body",
        should_learn_pattern=should_learn,
    )


def _make_event_with_calendar(
    *,
    check_in: str | None = None,
    check_out: str | None = None,
    current_time: str | None = None,
    event_id: str = "evt-q5c",
) -> AnalysisEvent:
    """Build an event whose calendar_snapshot carries the given dates."""
    calendar: dict[str, str] = {}
    if check_in:
        calendar["check_in"] = check_in
    if check_out:
        calendar["check_out"] = check_out
    if current_time:
        calendar["current_time"] = current_time
    return AnalysisEvent(
        event_id=event_id,
        event_type=AnalysisEventType.MESSAGE,
        property_id="prop-q5c",
        occurred_at=_NOW,
        text="Q5-C test event text",
        calendar_snapshot=calendar,
    )


@pytest.mark.asyncio
async def test_q5c_hard_mismatch_reports_detail() -> None:
    """Calendar post-stay + scenario pre-arrival ⇒ mismatch reported.

    The classic Mümin adversarial test: guest message implies a
    pre-arrival question (Wi-Fi password before arrival), but
    calendar says the guest already checked out days ago.
    """
    # Stage 3 = PRE_ARRIVAL.  Calendar puts current_time well
    # after check_out → POST_CHECKOUT.
    scenario = _make_stage_scenario(
        scenario_id="s3_103_guest_asks_for_wifi_password_before",
        stage_number=3,
    )
    matcher = _StubMatcher(
        (
            _MatcherCandidate(
                "s3_103_guest_asks_for_wifi_password_before", 0.9
            ),
        ),
    )
    catalog = _StubCatalog(
        {"s3_103_guest_asks_for_wifi_password_before": scenario},
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    event = _make_event_with_calendar(
        check_in="2026-05-10T14:00:00Z",
        check_out="2026-05-12T10:00:00Z",
        current_time="2026-05-18T12:00:00Z",  # 6 days after checkout
    )
    result = await orchestrator.analyze(event)
    assert result.stage_mismatch is True
    assert result.stage_mismatch_detail == (
        "calendar=post_checkout scenario=pre_arrival"
    )


@pytest.mark.asyncio
async def test_q5c_exact_match_no_mismatch() -> None:
    """Calendar in_stay + scenario in_stay ⇒ no mismatch."""
    scenario = _make_stage_scenario(
        scenario_id="s5_253_guest_says_wifi_is_completely_down",
        stage_number=5,
    )
    matcher = _StubMatcher(
        (
            _MatcherCandidate(
                "s5_253_guest_says_wifi_is_completely_down",
                0.9,
            ),
        ),
    )
    catalog = _StubCatalog(
        {"s5_253_guest_says_wifi_is_completely_down": scenario},
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    event = _make_event_with_calendar(
        check_in="2026-05-15T14:00:00Z",
        check_out="2026-05-20T10:00:00Z",
        current_time="2026-05-17T15:00:00Z",  # in_stay
    )
    result = await orchestrator.analyze(event)
    assert result.stage_mismatch is False
    assert result.stage_mismatch_detail == ""


@pytest.mark.asyncio
async def test_q5c_adjacent_pair_no_mismatch() -> None:
    """PRE_ARRIVAL (calendar) + CHECKIN (scenario) is a normal transition."""
    # Stage 4 = CHECKIN catalog scenario; calendar shows guest
    # 5 days before check-in (PRE_ARRIVAL).  Adjacent pair.
    scenario = _make_stage_scenario(
        scenario_id="s4_152_guest_says_they_are_at_the",
        stage_number=4,
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s4_152_guest_says_they_are_at_the", 0.9),),
    )
    catalog = _StubCatalog(
        {"s4_152_guest_says_they_are_at_the": scenario},
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    event = _make_event_with_calendar(
        check_in="2026-05-15T14:00:00Z",
        check_out="2026-05-20T10:00:00Z",
        current_time="2026-05-10T12:00:00Z",  # PRE_ARRIVAL
    )
    result = await orchestrator.analyze(event)
    assert result.stage_mismatch is False


@pytest.mark.asyncio
async def test_q5c_stage_agnostic_scenario_never_mismatches() -> None:
    """Stage 9 catalog entries (Ops / Internal) are stage-agnostic."""
    scenario = _make_stage_scenario(
        scenario_id="s9_453_pms_calendar_sync_fails",
        stage_number=9,
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s9_453_pms_calendar_sync_fails", 0.9),),
    )
    catalog = _StubCatalog(
        {"s9_453_pms_calendar_sync_fails": scenario},
    )
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    # Calendar in_stay → would normally clash with stage 9, but
    # stage 9 is agnostic → no mismatch.
    event = _make_event_with_calendar(
        check_in="2026-05-15T14:00:00Z",
        check_out="2026-05-20T10:00:00Z",
        current_time="2026-05-17T12:00:00Z",
    )
    result = await orchestrator.analyze(event)
    assert result.stage_mismatch is False


@pytest.mark.asyncio
async def test_q5c_missing_calendar_no_mismatch() -> None:
    """No calendar data on event ⇒ Q5-C silently no-ops."""
    scenario = _make_stage_scenario(
        scenario_id="s3_103_test",
        stage_number=3,
    )
    matcher = _StubMatcher((_MatcherCandidate("s3_103_test", 0.9),))
    catalog = _StubCatalog({"s3_103_test": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    # _make_event() helper does NOT populate calendar_snapshot.
    result = await orchestrator.analyze(_make_event())
    assert result.stage_mismatch is False
    assert result.stage_mismatch_detail == ""


@pytest.mark.asyncio
async def test_q5c_q5a_trip_skips_stage_check() -> None:
    """Q5-A clears dominant ⇒ Q5-C has no scenario to compare."""
    scenario = _make_stage_scenario(
        scenario_id="s3_103_weak",
        stage_number=3,
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s3_103_weak", 0.20),),  # below 0.45
    )
    catalog = _StubCatalog({"s3_103_weak": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
        min_similarity=0.45,
    )
    event = _make_event_with_calendar(
        check_in="2026-05-10T14:00:00Z",
        check_out="2026-05-12T10:00:00Z",
        current_time="2026-05-18T12:00:00Z",
    )
    result = await orchestrator.analyze(event)
    # Q5-A cleared catalog_entry → Q5-C sees no scenario → no
    # mismatch reported.
    assert result.stage_mismatch is False


@pytest.mark.asyncio
async def test_q5c_does_not_gate_pattern_candidate() -> None:
    """Q5-C Variant A is observation-only: it MUST NOT gate mining.

    Hard mismatch is reported on AnalysisResult, but
    pattern_candidate_emitted stays True because the FL-05
    learn-gate logic owns that decision in Variant A.
    """
    scenario = _make_stage_scenario(
        scenario_id="s3_103_should_learn",
        stage_number=3,
        should_learn="Yes",
    )
    matcher = _StubMatcher(
        (_MatcherCandidate("s3_103_should_learn", 0.9),),
    )
    catalog = _StubCatalog({"s3_103_should_learn": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    event = _make_event_with_calendar(
        check_in="2026-05-10T14:00:00Z",
        check_out="2026-05-12T10:00:00Z",
        current_time="2026-05-18T12:00:00Z",
    )
    result = await orchestrator.analyze(event)
    assert result.stage_mismatch is True
    # CRITICAL: Variant A does NOT gate mining.  Q5-C Variant B
    # follow-up may flip this.
    assert result.pattern_candidate_emitted is True


@pytest.mark.asyncio
async def test_q5c_pre_q5c_callers_see_no_behavior_change() -> None:
    """Default-empty calendar_snapshot ⇒ default-False mismatch."""
    scenario = _make_stage_scenario(
        scenario_id="s5_legacy",
        stage_number=5,
    )
    matcher = _StubMatcher((_MatcherCandidate("s5_legacy", 0.9),))
    catalog = _StubCatalog({"s5_legacy": scenario})
    orchestrator = FoundationAnalysisOrchestrator(
        scenario_matcher=matcher,
        foundation_catalog=catalog,
    )
    # Use the original _make_event() — no calendar_snapshot.
    result = await orchestrator.analyze(_make_event())
    assert result.stage_mismatch is False
    assert result.stage_mismatch_detail == ""
