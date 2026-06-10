"""Tests for the Mem0ExtractorService + FactStore wiring in factory.py.

``create_full_system`` instantiates Redis / Qdrant / many subsystems,
so a full run would fail outside an integration environment. We pin
only the static contract: env-flag helper, module wiring of the two
new dependencies, and a minimal stub of ``create_full_system`` that
asserts the relevant fields end up where ``NightlyConsolidator``
reads them.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import brain_engine.memory.factory as factory_mod
from brain_engine.memory.factory import (
    _MEM0_FACTS_COLLECTION,
    _mem0_extractor_enabled,
)


# ── Env-flag helper ────────────────────────────────────────────────


def test_mem0_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAIN_MEM0_EXTRACTOR_ENABLED", raising=False)
    assert _mem0_extractor_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_mem0_flag_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("BRAIN_MEM0_EXTRACTOR_ENABLED", value)
    assert _mem0_extractor_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_mem0_flag_falsy(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("BRAIN_MEM0_EXTRACTOR_ENABLED", value)
    assert _mem0_extractor_enabled() is False


# ── Constants surface ──────────────────────────────────────────────


def test_mem0_facts_collection_constant() -> None:
    """The collection name is the contract between factory + nightly path."""
    assert _MEM0_FACTS_COLLECTION == "mem0_facts"


# ── create_full_system wiring ───────────────────────────────────────


@pytest.fixture
def stubbed_full_system(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace every external constructor in factory_mod with a MagicMock.

    Lets ``create_full_system`` run end-to-end in-process without
    touching Redis, Qdrant, or any real LLM endpoint. Returns the
    captured constructors so tests can assert on call args.
    """
    captured: dict[str, MagicMock] = {}

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
        "FactStore",
        "Mem0ExtractorService",
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
    for name in targets:
        stub = MagicMock(name=name)
        # Make returned instance a MagicMock so attribute access works.
        stub.return_value = MagicMock(name=f"{name}-instance")
        monkeypatch.setattr(factory_mod, name, stub, raising=True)
        captured[name] = stub

    # aioredis.from_url is imported inside create_full_system; patch
    # the attribute on the real module so the import statement still
    # resolves through the actual redis-py package (replacing the
    # whole module breaks redis-py's own internal imports).
    import redis.asyncio  # noqa: PLC0415

    monkeypatch.setattr(
        redis.asyncio,
        "from_url",
        MagicMock(return_value=MagicMock(name="redis-client")),
    )

    # _create_guest_memory_store is internal; patch it too.
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


def test_create_full_system_wires_fact_store_unconditionally(
    monkeypatch: pytest.MonkeyPatch,
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    """FactStore is always constructed — flag off does not disable it."""
    monkeypatch.delenv("BRAIN_MEM0_EXTRACTOR_ENABLED", raising=False)

    factory_mod.create_full_system(
        redis_url="redis://x",
        qdrant_url="http://q",
    )

    fact_store_stub = stubbed_full_system["FactStore"]
    fact_store_stub.assert_called_once_with(qdrant_url="http://q")


def test_create_full_system_skips_mem0_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    monkeypatch.delenv("BRAIN_MEM0_EXTRACTOR_ENABLED", raising=False)

    factory_mod.create_full_system(redis_url="redis://x", qdrant_url="http://q")

    stubbed_full_system["Mem0ExtractorService"].assert_not_called()
    nightly_call = stubbed_full_system["NightlyConsolidator"].call_args
    assert nightly_call.kwargs["mem0_extractor"] is None
    # FactStore handle still propagates so contradictions / dedup
    # paths can rely on it once they ship their own flags.
    assert nightly_call.kwargs["fact_store"] is not None


def test_create_full_system_constructs_mem0_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
    stubbed_full_system: dict[str, MagicMock],
) -> None:
    monkeypatch.setenv("BRAIN_MEM0_EXTRACTOR_ENABLED", "1")

    factory_mod.create_full_system(
        redis_url="redis://r",
        qdrant_url="http://q",
        llm_model="gpt-4o-mini",
    )

    mem0_stub = stubbed_full_system["Mem0ExtractorService"]
    mem0_stub.assert_called_once_with(
        qdrant_url="http://q",
        qdrant_collection=_MEM0_FACTS_COLLECTION,
        redis_url="redis://r",
        llm_model="gpt-4o-mini",
    )
    nightly_call = stubbed_full_system["NightlyConsolidator"].call_args
    assert nightly_call.kwargs["mem0_extractor"] is mem0_stub.return_value
