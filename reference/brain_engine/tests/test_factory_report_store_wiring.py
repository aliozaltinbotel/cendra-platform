"""Tests for the ReportStore wiring in factory.py.

The Redis side of ReportStore is exercised by smart_engine unit
tests.  This file pins only the wiring contract: the slot is
attached to ``FullSystem`` and the construction propagates the
factory-level ``redis_url`` so reports land in the same cluster
the rest of the system already shares.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import brain_engine.memory.factory as factory_mod
from brain_engine.smart_engine.report_store import ReportStore


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
        "ReportStore",
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


def test_create_full_system_attaches_report_store(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    assert hasattr(system, "report_store")
    assert system.report_store is (
        stubbed_full_system["ReportStore"].return_value
    )


def test_report_store_constructed_with_factory_redis_url(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """Wiring must propagate the factory's redis_url verbatim."""
    factory_mod.create_full_system(
        redis_url="redis://prod-redis:6379/2",
        qdrant_url="http://q",
    )

    stubbed_full_system["ReportStore"].assert_called_once_with(
        redis_url="redis://prod-redis:6379/2",
    )


def test_report_store_real_class_constructs_without_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real class is import-clean and lazy-opens Redis only on use."""
    import redis.asyncio  # noqa: PLC0415

    monkeypatch.setattr(
        redis.asyncio,
        "from_url",
        MagicMock(return_value=MagicMock(name="lazy-client")),
    )

    store = ReportStore(redis_url="redis://x")
    assert store._prefix == "brain:report:"
