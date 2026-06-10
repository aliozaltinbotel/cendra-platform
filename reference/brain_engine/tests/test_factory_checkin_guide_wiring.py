"""Tests for the CheckinGuideGenerator wiring in factory.py.

The generator's text-rendering logic is exercised by smart_engine
unit tests.  This file pins only the wiring contract: the slot is
attached to ``FullSystem``, fed the active MemorySystem, and the
class import path stays reachable from production.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import brain_engine.memory.factory as factory_mod
from brain_engine.smart_engine.checkin_guide import (
    CheckinGuideGenerator,
    PropertyAccess,
)


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


def test_create_full_system_attaches_checkin_guide(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    assert hasattr(system, "checkin_guide_generator")
    assert isinstance(system.checkin_guide_generator, CheckinGuideGenerator)


def test_checkin_guide_generator_receives_memory_handle(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """Wiring must hand the freshly built MemorySystem to the generator."""
    memory_stub_instance = stubbed_full_system[
        "create_memory_system"
    ].return_value

    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    # Internal attribute name is private; the wiring contract is
    # "the value handed in matches the active MemorySystem".  We
    # read the private attr because no public getter exists yet.
    assert system.checkin_guide_generator._memory is memory_stub_instance


@pytest.mark.asyncio
async def test_checkin_guide_smoke_round_trip() -> None:
    """The wired generator produces a usable CheckinGuide (real class)."""
    generator = CheckinGuideGenerator()

    access = PropertyAccess(
        building_code="1234",
        wifi_name="GUEST",
        wifi_password="pwd",
    )
    guide = await generator.generate(
        guest_name="Alice",
        property_id="prop-1",
        access=access,
        language="en",
    )

    assert guide.guest_name == "Alice"
    assert guide.property_name == "prop-1"
    assert guide.access is access
    assert guide.language == "en"
