"""Sprint 6 W2 wiring tests — MemoryFanOut consumes FL-04 routes.

Pins:

* :func:`resolve_fanout_tiers` — empty / unknown-only input
  collapses to the all-tiers safety net so the fan-out never
  silently drops a write.  Known slugs map to the canonical
  destinations spelled out in the module docstring.
* :meth:`MemoryFanOut.record_case` — default ``routes=()`` writes
  to every wired tier (pre-W2 behaviour); explicit routes restrict
  the fan-out to the mapped tiers only.
* :class:`NullMemoryFanOut` — accepts the new ``routes`` kwarg
  for Protocol compatibility, still a silent no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain_engine.memory.fanout import (
    ALL_FANOUT_TIERS,
    FanOutTier,
    MemoryFanOut,
    NullMemoryFanOut,
    resolve_fanout_tiers,
)
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionCase,
    DecisionType,
    Scenario,
)

# ── fixtures ──────────────────────────────────────────────── #


class _StubEpisodic:
    """Recording stub mimicking the EpisodicMemory shape used by fan-out."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def add_episode(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _StubSemantic:
    """Recording stub mimicking the SemanticMemory.store shape."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def store(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _StubKG:
    """Recording stub mimicking the TemporalKnowledgeGraph.add_knowledge."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def add_knowledge(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _case(case_id: str = "case-1") -> DecisionCase:
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
        message_text="early check-in please",
        created_at=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
    )


def _build_fanout() -> tuple[
    MemoryFanOut,
    _StubEpisodic,
    _StubSemantic,
    _StubKG,
]:
    episodic = _StubEpisodic()
    semantic = _StubSemantic()
    kg = _StubKG()
    fanout = MemoryFanOut(
        episodic=episodic,
        semantic=semantic,
        knowledge_graph=kg,
    )
    return fanout, episodic, semantic, kg


# ── resolve_fanout_tiers ──────────────────────────────────── #


def test_resolve_empty_routes_returns_all_tiers() -> None:
    """Empty input ⇒ all-tiers safety net."""
    assert resolve_fanout_tiers([]) == ALL_FANOUT_TIERS


def test_resolve_only_unknown_routes_returns_all_tiers() -> None:
    """Unrecognised slugs fall back to all tiers (never drop writes)."""
    assert resolve_fanout_tiers(["mystery_tier"]) == ALL_FANOUT_TIERS


def test_resolve_property_knowledge_targets_semantic() -> None:
    """``property_knowledge`` slug ⇒ SEMANTIC tier only."""
    assert resolve_fanout_tiers(["property_knowledge"]) == frozenset(
        {FanOutTier.SEMANTIC},
    )


def test_resolve_reservation_context_targets_episodic() -> None:
    """``reservation_context_memory`` slug ⇒ EPISODIC tier only."""
    assert resolve_fanout_tiers(["reservation_context_memory"]) == frozenset(
        {FanOutTier.EPISODIC},
    )


def test_resolve_missing_info_targets_kg() -> None:
    """``missing_info_registry`` slug ⇒ KG tier only."""
    assert resolve_fanout_tiers(["missing_info_registry"]) == frozenset(
        {FanOutTier.KG},
    )


def test_resolve_guest_profile_targets_two_tiers() -> None:
    """Guest profile straddles SEMANTIC + EPISODIC."""
    tiers = resolve_fanout_tiers(["guest_profile_memory"])
    assert tiers == frozenset(
        {FanOutTier.SEMANTIC, FanOutTier.EPISODIC},
    )


def test_resolve_union_across_multiple_routes() -> None:
    """Multiple recognised slugs union their mapped tiers."""
    tiers = resolve_fanout_tiers(
        [
            "property_knowledge",          # SEMANTIC
            "reservation_context_memory",  # EPISODIC
            "missing_info_registry",        # KG
        ],
    )
    assert tiers == ALL_FANOUT_TIERS


def test_resolve_skips_unknown_keeps_known() -> None:
    """A mix of known + unknown ⇒ known wins; no fall-through."""
    tiers = resolve_fanout_tiers(
        [
            "property_knowledge",
            "mystery_tier",  # unknown — silently ignored
        ],
    )
    assert tiers == frozenset({FanOutTier.SEMANTIC})


# ── MemoryFanOut.record_case routing ──────────────────────── #


@pytest.mark.asyncio
async def test_record_case_default_routes_writes_to_all_tiers() -> None:
    """Default ``routes=()`` keeps the pre-W2 behaviour intact."""
    fanout, episodic, semantic, kg = _build_fanout()
    await fanout.record_case(_case())
    assert len(episodic.calls) == 1
    assert len(semantic.calls) == 1
    assert len(kg.calls) == 1


@pytest.mark.asyncio
async def test_record_case_property_knowledge_writes_only_semantic() -> None:
    """``property_knowledge`` route ⇒ SEMANTIC only — Episodic + KG skipped."""
    fanout, episodic, semantic, kg = _build_fanout()
    await fanout.record_case(
        _case(),
        routes=("property_knowledge",),
    )
    assert episodic.calls == []
    assert len(semantic.calls) == 1
    assert kg.calls == []


@pytest.mark.asyncio
async def test_record_case_reservation_context_writes_only_episodic() -> None:
    """``reservation_context_memory`` route ⇒ EPISODIC only."""
    fanout, episodic, semantic, kg = _build_fanout()
    await fanout.record_case(
        _case(),
        routes=("reservation_context_memory",),
    )
    assert len(episodic.calls) == 1
    assert semantic.calls == []
    assert kg.calls == []


@pytest.mark.asyncio
async def test_record_case_missing_info_writes_only_kg() -> None:
    """``missing_info_registry`` route ⇒ KG only."""
    fanout, episodic, semantic, kg = _build_fanout()
    await fanout.record_case(
        _case(),
        routes=("missing_info_registry",),
    )
    assert episodic.calls == []
    assert semantic.calls == []
    assert len(kg.calls) == 1


@pytest.mark.asyncio
async def test_record_case_unknown_routes_fall_back_to_all() -> None:
    """Unrecognised slugs ⇒ all-tier safety net (never silent)."""
    fanout, episodic, semantic, kg = _build_fanout()
    await fanout.record_case(
        _case(),
        routes=("mystery_tier",),
    )
    assert len(episodic.calls) == 1
    assert len(semantic.calls) == 1
    assert len(kg.calls) == 1


@pytest.mark.asyncio
async def test_record_case_routes_combine() -> None:
    """Two routes ⇒ union of mapped tiers."""
    fanout, episodic, semantic, kg = _build_fanout()
    await fanout.record_case(
        _case(),
        routes=(
            "property_knowledge",  # SEMANTIC
            "reservation_context_memory",  # EPISODIC
        ),
    )
    assert len(episodic.calls) == 1
    assert len(semantic.calls) == 1
    assert kg.calls == []


# ── NullMemoryFanOut Protocol compat ──────────────────────── #


@pytest.mark.asyncio
async def test_null_fanout_accepts_routes_and_is_no_op() -> None:
    """:class:`NullMemoryFanOut` accepts ``routes`` but does nothing."""
    fanout = NullMemoryFanOut()
    # Must not raise.
    await fanout.record_case(_case())
    await fanout.record_case(_case(), routes=("property_knowledge",))
