"""Tests for the per-request tenant override on the bootstrap pipeline.

Phase 1 multi-tenant bootstrap (2026-05-21).  When the
:meth:`OnboardingBootstrapPipeline.bootstrap_one` / ``bootstrap_fast``
caller passes ``customer_id_override`` / ``org_id_override`` /
``provider_type_override`` kwargs, the pipeline forwards them to the
archive loader so the GraphQL gateway sees the override tenant.
When the caller omits them, the loader receives ZERO extra kwargs —
that preserves the :class:`ConversationArchiveLoader` Protocol
contract for every existing implementation (mocks, stubs, the
in-memory PMS loader) without forcing them to advertise the new
tenant slots.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from brain_engine.onboarding.bootstrap_pipeline import (
    OnboardingBootstrapPipeline,
)

# ── Fixtures ──────────────────────────────────────────────────────


class _RecordingLoader:
    """Archive loader stub that records every ``load()`` kwarg."""

    name = "recording-loader"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def load(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.calls.append(dict(kwargs))
        return self._empty()

    async def _empty(self) -> AsyncIterator[Any]:
        if False:
            yield  # pragma: no cover - empty async iter helper


class _MemoryCaseStore:
    def __init__(self) -> None:
        self.stored: list[Any] = []

    async def store(self, case: Any) -> str:
        self.stored.append(case)
        return getattr(case, "case_id", "")


class _PassthroughEpisodeBuilder:
    def split(self, conversation: Any) -> tuple[list[Any], Any]:
        return [], object()


class _NoopExtractor:
    async def extract(
        self,
        *,
        episode: Any,
        conversation: Any,
        property_id: str,
    ) -> Any:
        return None


def _build_pipeline(loader: _RecordingLoader) -> OnboardingBootstrapPipeline:
    return OnboardingBootstrapPipeline(
        archive_loader=cast(Any, loader),
        episode_builder=cast(Any, _PassthroughEpisodeBuilder()),
        case_extractor=cast(Any, _NoopExtractor()),
        case_store=cast(Any, _MemoryCaseStore()),
    )


# ── bootstrap_one — override path ────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_one_passes_overrides_to_loader() -> None:
    """All three overrides land on the loader's ``load()`` kwargs."""
    loader = _RecordingLoader()
    pipeline = _build_pipeline(loader)

    await pipeline.bootstrap_one(
        property_id="598808",
        days=30,
        limit=10,
        mine_patterns=False,
        customer_id_override="ec9013b9",
        org_id_override="626ee566",
        provider_type_override="LODGIFY",
    )

    assert len(loader.calls) == 1
    call = loader.calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert call["org_id_override"] == "626ee566"
    assert call["provider_type_override"] == "LODGIFY"


@pytest.mark.asyncio
async def test_bootstrap_one_without_overrides_passes_no_extra_kwargs() -> (
    None
):
    """No override → loader sees only the legacy 4 kwargs.

    Required for Protocol-only loaders / mocks that do not declare
    the new tenant slots in their signature.
    """
    loader = _RecordingLoader()
    pipeline = _build_pipeline(loader)

    await pipeline.bootstrap_one(
        property_id="598808",
        days=30,
        limit=10,
        mine_patterns=False,
    )

    assert len(loader.calls) == 1
    call = loader.calls[0]
    assert set(call.keys()) == {"property_id", "since", "until", "limit"}


@pytest.mark.asyncio
async def test_bootstrap_one_partial_override_omits_unspecified() -> None:
    """Only the keys the caller supplied reach the loader — the
    others are not forwarded as explicit ``None`` so older loaders
    keep working."""
    loader = _RecordingLoader()
    pipeline = _build_pipeline(loader)

    await pipeline.bootstrap_one(
        property_id="598808",
        days=30,
        limit=10,
        mine_patterns=False,
        customer_id_override="ec9013b9",
    )

    call = loader.calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert "org_id_override" not in call
    assert "provider_type_override" not in call


# ── bootstrap_fast — override path ───────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_fast_passes_overrides_to_loader() -> None:
    """``bootstrap_fast`` plumbs overrides through the
    drain-then-fan-out variant too."""
    loader = _RecordingLoader()
    pipeline = _build_pipeline(loader)

    await pipeline.bootstrap_fast(
        property_id="598808",
        days=30,
        inner_concurrency=2,
        mine_patterns_inline=False,
        customer_id_override="ec9013b9",
        org_id_override="626ee566",
        provider_type_override="LODGIFY",
    )

    assert len(loader.calls) == 1
    call = loader.calls[0]
    assert call["customer_id_override"] == "ec9013b9"
    assert call["org_id_override"] == "626ee566"
    assert call["provider_type_override"] == "LODGIFY"


@pytest.mark.asyncio
async def test_bootstrap_fast_without_overrides_passes_no_extra_kwargs() -> (
    None
):
    """No-override path on ``bootstrap_fast`` is wire-compat too."""
    loader = _RecordingLoader()
    pipeline = _build_pipeline(loader)

    await pipeline.bootstrap_fast(
        property_id="598808",
        days=30,
        inner_concurrency=2,
        mine_patterns_inline=False,
    )

    assert len(loader.calls) == 1
    call = loader.calls[0]
    assert set(call.keys()) == {"property_id", "since", "until", "limit"}


# ── Backwards compatibility — empty-string override semantics ────


@pytest.mark.asyncio
async def test_empty_string_override_still_forwards_to_loader() -> None:
    """An empty-string ``org_id`` is forwarded as ``""`` — the loader
    itself interprets "blank = clear optional filter".  The pipeline
    does not pre-strip it because the loader's semantics are richer
    (blank ``customer_id`` is "no override", blank ``org_id`` is
    "drop the filter")."""
    loader = _RecordingLoader()
    pipeline = _build_pipeline(loader)

    await pipeline.bootstrap_one(
        property_id="598808",
        days=30,
        limit=10,
        mine_patterns=False,
        org_id_override="",
    )

    call = loader.calls[0]
    assert call["org_id_override"] == ""


# ── Window plumbing sanity ───────────────────────────────────────


@pytest.mark.asyncio
async def test_loader_receives_valid_window_alongside_overrides() -> None:
    """The override threading does not corrupt the existing ``since``
    / ``until`` / ``limit`` plumbing."""
    loader = _RecordingLoader()
    pipeline = _build_pipeline(loader)

    await pipeline.bootstrap_one(
        property_id="598808",
        days=30,
        limit=10,
        mine_patterns=False,
        customer_id_override="ec9013b9",
    )

    call = loader.calls[0]
    assert call["property_id"] == "598808"
    assert isinstance(call["since"], datetime)
    assert call["since"].tzinfo == UTC
    assert isinstance(call["until"], datetime)
    assert call["until"].tzinfo == UTC
    assert call["since"] < call["until"]
    assert call["limit"] == 10
