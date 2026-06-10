"""Tests for the SOPParser wiring in factory.py.

The parser's text-extraction logic is exercised by its own unit
tests; this file pins only the wiring contract — the slot is
attached to ``FullSystem`` and points at the active
ProceduralMemory so SOP-derived rules live in the same store as
manual + immutable rules (see SOPParser module docstring).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import brain_engine.memory.factory as factory_mod
from brain_engine.continual_learning.sop_parser import SOPParser


@pytest.fixture
def stubbed_full_system(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Stub heavy collaborators so create_full_system runs in-process."""
    targets = [
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
    captured: dict[str, MagicMock] = {}
    for name in targets:
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


def test_create_full_system_attaches_sop_parser(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    assert hasattr(system, "sop_parser")
    assert isinstance(system.sop_parser, SOPParser)


def test_sop_parser_uses_active_procedural_memory(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """Wired SOPParser must point at MemorySystem.procedural — SOP-derived
    rules share the single store with manual + immutable rules."""
    memory_stub_instance = stubbed_full_system[
        "create_memory_system"
    ].return_value

    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    # Private attribute access is intentional — no public getter
    # exists; the wiring contract is "the parser's procedural
    # handle equals the FullSystem's procedural handle".
    assert system.sop_parser._memory is memory_stub_instance.procedural


def test_sop_parser_propagates_factory_llm_model(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """Whichever LLM model the factory chose must reach the parser."""
    factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
        llm_model="gpt-4o",
    )

    # Walk the SOPParser instance attached to the system.
    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
        llm_model="gpt-4o",
    )

    assert system.sop_parser._llm_model == "gpt-4o"


def test_sop_parser_real_class_constructs_without_io() -> None:
    """The real class is import-clean and stateless at construction."""
    fake_procedural = MagicMock(name="procedural")
    parser = SOPParser(procedural_memory=fake_procedural)
    assert parser._memory is fake_procedural
    # Default model anchors the historical contract — flipping it
    # should be a deliberate change picked up here.
    assert parser._llm_model == "gpt-4o-mini"
