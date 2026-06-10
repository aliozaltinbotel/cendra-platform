"""Tests for the factory wiring follow-up (2026-05-08).

Audit ``project_wiring_audit_2026-05-07.md`` found that the four
components freshly wired into ``NightlyConsolidator.__init__`` by
PR #179 (golden_cases_runner), #181 (dedup_consolidator), #182
(contradiction_detector) and #192 (deterministic_kg_sync) were
never instantiated by ``brain_engine.memory.factory.create_full_system``.
The constructor accepted them but the factory call passed only six
arguments — every fifth-through-eighth slot stayed at its ``None``
default and the runtime path short-circuited regardless of the env
flags.

This file pins the closing contract: when ``create_full_system`` is
invoked with the ``case_store`` argument that the lifespan already
has on hand, every collaborator the four wiring PRs added is
constructed and threaded into ``NightlyConsolidator(...)``.

The test stubs heavy collaborators following the established
``test_factory_*_wiring.py`` pattern so no real Redis / Qdrant /
LLM connection is opened.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import brain_engine.memory.factory as factory_mod
from brain_engine.memory.contradiction_detector import ContradictionDetector
from brain_engine.memory.episodic_dedup import EpisodicDedupConsolidator


_STUB_TARGETS = [
    "create_memory_system",
    "ComplexityRouter",
    "LLMRouter",
    "StakeholderModel",
    "GuardrailPipeline",
    "InteractionRecorder",
    "APMGrader",
    "SkillEvolutionEngine",
    "AdaptiveAutonomyManager",
    "NightlyConsolidator",
    "MonthlyEvaluator",
    "BusinessFlagClassifier",
    "OpsSessionManager",
    "PipelineCheckpointer",
    "InterruptResume",
    "DurablePipeline",
    "TaskQueue",
    "WorkerPool",
    "AutomationEngine",
    "IoTProcessor",
]


@pytest.fixture
def stubbed_full_system(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, MagicMock]:
    """Stub heavy collaborators so create_full_system runs in-process.

    Mirrors the setup used by the other ``test_factory_*_wiring`` tests
    so the wiring assertions stay isolated from Redis / Qdrant / LLM.
    """
    captured: dict[str, MagicMock] = {}
    for name in _STUB_TARGETS:
        stub = MagicMock(name=name)
        stub.return_value = MagicMock(name=f"{name}-instance")
        monkeypatch.setattr(factory_mod, name, stub, raising=True)
        captured[name] = stub

    import redis.asyncio  # noqa: PLC0415

    monkeypatch.setattr(
        redis.asyncio,
        "from_url",
        MagicMock(return_value=MagicMock(name="redis-client")),
    )
    monkeypatch.setattr(
        factory_mod,
        "_create_guest_memory_store",
        MagicMock(return_value=MagicMock(name="guest-memory-store")),
        raising=True,
    )
    monkeypatch.setattr(
        factory_mod,
        "_register_task_handlers",
        MagicMock(return_value=None),
        raising=True,
    )
    return captured


def _consolidator_kwargs(
    stubbed: dict[str, MagicMock],
) -> dict[str, Any]:
    """Pull the kwargs that ``factory`` passed into ``NightlyConsolidator``."""
    call = stubbed["NightlyConsolidator"].call_args
    assert call is not None, (
        "factory must construct NightlyConsolidator exactly once"
    )
    return call.kwargs


# ---------------------------------------------------------------------------
# Full wiring path — case_store + rule_store provided
# ---------------------------------------------------------------------------


def test_full_wiring_injects_all_four_components(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """All four follow-up DI parameters reach NightlyConsolidator."""
    case_store = MagicMock(name="case_store")
    rule_store = MagicMock(name="rule_store")

    factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
        case_store=case_store,
        rule_store=rule_store,
    )

    kwargs = _consolidator_kwargs(stubbed_full_system)
    assert kwargs["case_store"] is case_store
    assert kwargs["rule_store"] is rule_store
    assert kwargs["golden_cases_runner"] is not None
    assert kwargs["dedup_consolidator"] is not None
    assert kwargs["contradiction_detector"] is not None
    assert kwargs["deterministic_kg_sync"] is not None


def test_dedup_consolidator_is_concrete_class(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """``EpisodicDedupConsolidator`` does not require external deps —
    factory builds it unconditionally so tests do not need a stub."""
    factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
        case_store=MagicMock(),
    )
    kwargs = _consolidator_kwargs(stubbed_full_system)
    assert isinstance(
        kwargs["dedup_consolidator"], EpisodicDedupConsolidator,
    )


def test_contradiction_detector_uses_factory_fact_store(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """``ContradictionDetector`` receives the same ``FactStore`` instance
    the factory wires into ``NightlyConsolidator`` so contradiction
    resolution sees the same fact graph as nightly fact storage."""
    factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
        case_store=MagicMock(),
    )
    kwargs = _consolidator_kwargs(stubbed_full_system)
    detector = kwargs["contradiction_detector"]
    assert isinstance(detector, ContradictionDetector)
    assert detector._store is kwargs["fact_store"]


# ---------------------------------------------------------------------------
# Partial wiring path — case_store missing
# ---------------------------------------------------------------------------


def test_no_case_store_skips_golden_cases_runner(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """``GoldenCasesRunner`` requires a case store — without one it must
    stay ``None`` so the runner's required argument never receives a
    bogus value."""
    factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )
    kwargs = _consolidator_kwargs(stubbed_full_system)
    assert kwargs["case_store"] is None
    assert kwargs["golden_cases_runner"] is None
    # The other three do not depend on case_store and must stay live.
    assert kwargs["dedup_consolidator"] is not None
    assert kwargs["contradiction_detector"] is not None
    assert kwargs["deterministic_kg_sync"] is not None


def test_deterministic_kg_sync_uses_memory_knowledge_graph(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """``DeterministicKGSync`` writes into the same KG instance the
    legacy LLM consolidation reads from — wiring assertion."""
    memory_stub = stubbed_full_system[
        "create_memory_system"
    ].return_value

    factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
        case_store=MagicMock(),
    )
    kwargs = _consolidator_kwargs(stubbed_full_system)
    sync = kwargs["deterministic_kg_sync"]
    assert sync is not None
    assert sync._kg is memory_stub.knowledge_graph
