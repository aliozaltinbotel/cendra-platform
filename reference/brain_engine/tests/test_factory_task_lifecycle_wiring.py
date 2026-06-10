"""Tests for the TaskLifecycleManager wiring in factory.py.

The state-machine itself is exercised by the smart_engine unit tests;
this module pins only the wiring contract: the slot is always
attached to ``FullSystem`` (no env flag — there is no I/O in the
constructor) and exposes a working ``create_task`` method against the
real class so the import graph is reachable from production.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import brain_engine.memory.factory as factory_mod
from brain_engine.smart_engine.task_lifecycle import (
    TaskLifecycleManager,
    TaskState,
)


@pytest.fixture
def stubbed_full_system(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace every external constructor in factory_mod with a MagicMock.

    Keeps ``create_full_system`` runnable in-process — same approach
    as ``test_factory_mem0_wiring`` but smaller scope (we only care
    about the TaskLifecycleManager bind here).
    """
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


def test_create_full_system_attaches_task_lifecycle(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    assert hasattr(system, "task_lifecycle")
    assert isinstance(system.task_lifecycle, TaskLifecycleManager)


def test_task_lifecycle_create_task_round_trip() -> None:
    """The wired manager produces a usable TaskState (real class smoke)."""
    mgr = TaskLifecycleManager()

    task = mgr.create_task(
        title="Cleaner check-in",
        description="Aynur needs to confirm 14:00 arrival",
        category="Cleaning",
        property_id="prop-1",
    )

    assert isinstance(task, TaskState)
    assert task.status == "pending"
    assert task.title == "Cleaner check-in"
    assert task.property_id == "prop-1"
    assert task.task_id  # uuid generated


def test_task_lifecycle_assignment_transitions_to_waiting() -> None:
    """Sanity check the state machine reaches WAITING on assign()."""
    mgr = TaskLifecycleManager()
    task = mgr.create_task(
        title="t", description="d", category="Other", property_id="p",
    )

    updated = mgr.assign(task, assignee_id="user-1", assignee_name="Alice")

    assert updated.status == "waiting"
    assert updated.assignee_id == "user-1"
    assert updated.assignee_name == "Alice"
