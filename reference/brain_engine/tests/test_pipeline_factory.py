"""Tests for ``build_bootstrap_pipeline`` (the extracted assembly).

The factory is a faithful relocation of the FastAPI lifespan's
inline pipeline assembly, so the behaviour under test is exactly
what the lifespan used to do: pick the Redis-backed event bus +
job store when a Redis client is present, the in-memory variants
otherwise, and return a wired ``OnboardingBootstrapPipeline``.
Heavyweight dependencies are stand-ins — the factory only stores
them, so construction never touches a real backend.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from api_server.bootstrap.pipeline_factory import build_bootstrap_pipeline
from brain_engine.onboarding import OnboardingBootstrapPipeline
from brain_engine.onboarding.event_bus import (
    InMemoryBootstrapEventBus,
    RedisBootstrapEventBus,
)
from brain_engine.onboarding.job_store import (
    BootstrapJobStore,
    InMemoryBootstrapJobStore,
    RedisBootstrapJobStore,
)


def _build(
    *,
    rule_store: Any | None = None,
    redis_client: Any | None = None,
) -> tuple[OnboardingBootstrapPipeline, BootstrapJobStore]:
    return build_bootstrap_pipeline(
        archive_loader=MagicMock(),
        case_store=MagicMock(),
        rule_store=rule_store,
        profile_harvester=None,
        sandbox_generator=None,
        sandbox_store=None,
        foundation_orchestrator=None,
        memory_fanout=None,
        profile_customer_id="",
        profile_org_id="",
        profile_provider_type="",
        redis_client=redis_client,
    )


def test_returns_pipeline_and_job_store() -> None:
    pipeline, job_store = _build()
    assert isinstance(pipeline, OnboardingBootstrapPipeline)
    assert job_store is not None


def test_in_memory_backends_without_redis() -> None:
    pipeline, job_store = _build(redis_client=None)
    assert isinstance(job_store, InMemoryBootstrapJobStore)
    assert isinstance(pipeline.event_bus, InMemoryBootstrapEventBus)


def test_redis_backends_with_client() -> None:
    pipeline, job_store = _build(redis_client=MagicMock())
    assert isinstance(job_store, RedisBootstrapJobStore)
    assert isinstance(pipeline.event_bus, RedisBootstrapEventBus)


def test_builds_with_rule_store_for_mining() -> None:
    # rule_store present → miner + extractor enabled; must not raise.
    pipeline, _ = _build(rule_store=MagicMock())
    assert isinstance(pipeline, OnboardingBootstrapPipeline)
