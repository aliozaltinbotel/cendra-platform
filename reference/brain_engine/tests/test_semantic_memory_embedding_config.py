"""Tests for env-driven embedding config wiring into SemanticMemory.

Covers the contract added by the embedding_config wiring PR:
- ``SemanticMemory()`` with no kwargs honours ``BRAIN_EMBEDDING_MODEL``
  and ``BRAIN_EMBEDDING_DIM`` (falls back to pre-Sprint-A defaults).
- Explicit kwargs continue to win over env vars (backward compat).
- Pre-wiring callers using positional defaults see no behaviour change.

We stub :class:`SentenceTransformer` and :class:`AsyncQdrantClient`
so the test does not download any model weights or contact Qdrant.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import brain_engine.memory.semantic_memory as sm_mod
from brain_engine.memory.embedding_config import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    SPRINT_A_EMBEDDING_DIM,
    SPRINT_A_EMBEDDING_MODEL,
)


@pytest.fixture
def stub_external_clients(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace SentenceTransformer + AsyncQdrantClient so __init__ is offline.

    Returns a dict so individual tests can assert on call args (the
    SentenceTransformer mock receives the resolved model name, which
    is the contract the wiring PR is meant to enforce).
    """
    encoder_factory = MagicMock(return_value=MagicMock())
    qdrant_factory = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(sm_mod, "SentenceTransformer", encoder_factory)
    monkeypatch.setattr(sm_mod, "AsyncQdrantClient", qdrant_factory)
    return {"encoder": encoder_factory, "qdrant": qdrant_factory}


def test_default_construction_uses_pre_sprint_a_model(
    monkeypatch: pytest.MonkeyPatch,
    stub_external_clients: dict[str, MagicMock],
) -> None:
    """No env vars set → SemanticMemory loads the legacy defaults."""
    monkeypatch.delenv("BRAIN_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("BRAIN_EMBEDDING_DIM", raising=False)

    memory = sm_mod.SemanticMemory()

    assert memory.embedding_dim == DEFAULT_EMBEDDING_DIM
    stub_external_clients["encoder"].assert_called_once_with(DEFAULT_EMBEDDING_MODEL)


def test_env_var_drives_model_and_known_dim(
    monkeypatch: pytest.MonkeyPatch,
    stub_external_clients: dict[str, MagicMock],
) -> None:
    """``BRAIN_EMBEDDING_MODEL`` alone resolves the matching dim from the table."""
    monkeypatch.setenv("BRAIN_EMBEDDING_MODEL", SPRINT_A_EMBEDDING_MODEL)
    monkeypatch.delenv("BRAIN_EMBEDDING_DIM", raising=False)

    memory = sm_mod.SemanticMemory()

    assert memory.embedding_dim == SPRINT_A_EMBEDDING_DIM
    stub_external_clients["encoder"].assert_called_once_with(
        SPRINT_A_EMBEDDING_MODEL,
    )


def test_explicit_kwargs_override_env(
    monkeypatch: pytest.MonkeyPatch,
    stub_external_clients: dict[str, MagicMock],
) -> None:
    """Explicit constructor args beat the env vars (backward compat)."""
    monkeypatch.setenv("BRAIN_EMBEDDING_MODEL", SPRINT_A_EMBEDDING_MODEL)
    monkeypatch.setenv("BRAIN_EMBEDDING_DIM", "999")

    memory = sm_mod.SemanticMemory(
        embedding_model="custom-model",
        embedding_dim=128,
    )

    assert memory.embedding_dim == 128
    stub_external_clients["encoder"].assert_called_once_with("custom-model")


def test_explicit_dim_overrides_env_dim(
    monkeypatch: pytest.MonkeyPatch,
    stub_external_clients: dict[str, MagicMock],
) -> None:
    """Caller pinning only ``embedding_dim`` keeps env-resolved model."""
    monkeypatch.setenv("BRAIN_EMBEDDING_MODEL", SPRINT_A_EMBEDDING_MODEL)
    monkeypatch.delenv("BRAIN_EMBEDDING_DIM", raising=False)

    memory = sm_mod.SemanticMemory(embedding_dim=512)

    assert memory.embedding_dim == 512
    stub_external_clients["encoder"].assert_called_once_with(
        SPRINT_A_EMBEDDING_MODEL,
    )


def test_legacy_module_constants_still_exported() -> None:
    """Existing imports of the module-private constants still work.

    A handful of older callers reach into ``semantic_memory`` for the
    legacy defaults. The wiring PR re-exports them as module aliases
    so we do not surprise downstream code.
    """
    assert sm_mod._DEFAULT_EMBEDDING_MODEL == DEFAULT_EMBEDDING_MODEL
    assert sm_mod._DEFAULT_EMBEDDING_DIM == DEFAULT_EMBEDDING_DIM
