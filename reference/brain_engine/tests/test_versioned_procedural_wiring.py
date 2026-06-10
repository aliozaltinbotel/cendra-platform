"""Tests for the VersionedProceduralMemory wiring + Protocol defaults.

Covers two surfaces:

1. The factory exposes ``FullSystem.versioned_procedural`` and the
   collaborator Protocols are satisfied by the shipped defaults.
2. The defaults round-trip through the snapshot/restore policy
   (smoke for advisory §7.2 — evolve → record → rollback).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import brain_engine.memory.factory as factory_mod
from brain_engine.memory.versioned_procedural import (
    EvolutionOutcome,
    RollbackOutcome,
    VersionedProceduralMemory,
)
from brain_engine.memory.versioned_procedural_defaults import (
    BrainZFSSnapshotStore,
    InMemoryEvolutionTracker,
    InMemorySkillStore,
    InMemorySuccessSignalSource,
)
from brain_engine.zfs.brain_zfs import BrainZFS


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


# ── Factory wiring ─────────────────────────────────────────────────


def test_create_full_system_attaches_versioned_procedural(
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    system = factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    assert hasattr(system, "versioned_procedural")
    assert isinstance(system.versioned_procedural, VersionedProceduralMemory)


# ── In-memory defaults round-trip ──────────────────────────────────


def test_in_memory_skill_store_upsert_and_restore() -> None:
    store = InMemorySkillStore()

    store.upsert("s-1", {"version": 1})
    assert store.skills["s-1"] == {"version": 1}

    store.restore("s-1", {"version": 0})
    assert store.skills["s-1"] == {"version": 0}


def test_in_memory_evolution_tracker_records_and_lists() -> None:
    from brain_engine.memory.versioned_procedural import EvolutionRecord

    tracker = InMemoryEvolutionTracker()
    record = EvolutionRecord(
        skill_id="s-1",
        snapshot_id="snap-1",
        evolved_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        success_threshold=0.85,
        evaluation_window=timedelta(days=7),
    )

    tracker.record(record)

    assert tracker.open_records() == (record,)


def test_in_memory_signal_source_aggregates_outcomes() -> None:
    source = InMemorySuccessSignalSource()
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)

    for ts_offset, success in [(0, True), (1, True), (2, False), (3, True)]:
        source.record_outcome(
            "s-1",
            success=success,
            when=base + timedelta(hours=ts_offset),
        )

    signal = source.signal_for("s-1", since=base)
    assert signal.sample_count == 4
    assert signal.success_rate == pytest.approx(3 / 4)


def test_brain_zfs_snapshot_store_roundtrips_payload() -> None:
    """snapshot then materialise returns the captured payload."""
    zfs = BrainZFS()
    skills = InMemorySkillStore()
    skills.upsert("s-1", {"rule": "always_approve_pet"})

    store = BrainZFSSnapshotStore(zfs=zfs, skill_store=skills)
    snapshot_id = store.snapshot("s-1")

    restored = store.materialise(snapshot_id)
    assert restored == {"rule": "always_approve_pet"}


# ── End-to-end policy through wired defaults ───────────────────────


def test_evolve_records_apply_outcome() -> None:
    skills = InMemorySkillStore()
    vp = VersionedProceduralMemory(
        skills=skills,
        snapshots=BrainZFSSnapshotStore(zfs=BrainZFS(), skill_store=skills),
        tracker=InMemoryEvolutionTracker(),
        signals=InMemorySuccessSignalSource(),
    )

    outcome = vp.evolve(
        skill_id="s-1", new_payload={"rule": "v2"},
    )

    assert outcome == EvolutionOutcome.APPLIED
    assert skills.skills["s-1"] == {"rule": "v2"}


def test_auto_rollback_keeps_skill_when_success_high() -> None:
    skills = InMemorySkillStore()
    signals = InMemorySuccessSignalSource()
    tracker = InMemoryEvolutionTracker()
    vp = VersionedProceduralMemory(
        skills=skills,
        snapshots=BrainZFSSnapshotStore(zfs=BrainZFS(), skill_store=skills),
        tracker=tracker,
        signals=signals,
        default_threshold=0.5,
        default_window=timedelta(seconds=0.001),
        min_samples=2,
    )

    skills.upsert("s-1", {"rule": "v1"})
    base = datetime(2026, 5, 7, tzinfo=timezone.utc)
    vp.evolve(skill_id="s-1", new_payload={"rule": "v2"}, now=base)
    for offset in (1, 2, 3):
        signals.record_outcome(
            "s-1",
            success=True,
            when=base + timedelta(seconds=offset),
        )

    results = vp.auto_rollback_check(now=base + timedelta(minutes=1))

    assert results == {"s-1": RollbackOutcome.KEPT}
    assert skills.skills["s-1"] == {"rule": "v2"}
