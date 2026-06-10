"""Tests for the Sprint A env-driven embedding configuration.

Pins three guarantees:

* The pre-Sprint-A defaults are returned when no env vars are set —
  existing deploys keep their current 384-dim ``all-MiniLM-L6-v2``
  setup unchanged.
* ``BRAIN_EMBEDDING_MODEL`` resolves to the matching dim for known
  models without requiring an explicit ``BRAIN_EMBEDDING_DIM``.
* Unknown models warn and fall back; explicit dims override the
  table; malformed dims raise instead of silently corrupting Qdrant
  collection creation.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest

from brain_engine.memory.embedding_config import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    SPRINT_A_EMBEDDING_DIM,
    SPRINT_A_EMBEDDING_MODEL,
    resolve_embedding_dim,
    resolve_embedding_model,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_embedding_env() -> Iterator[None]:
    """Strip Sprint A env vars before each test to avoid leakage."""
    snapshot = {
        key: os.environ.pop(key, None)
        for key in ("BRAIN_EMBEDDING_MODEL", "BRAIN_EMBEDDING_DIM")
    }
    try:
        yield
    finally:
        for key in ("BRAIN_EMBEDDING_MODEL", "BRAIN_EMBEDDING_DIM"):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_model_when_env_unset() -> None:
    assert resolve_embedding_model() == DEFAULT_EMBEDDING_MODEL


def test_default_dim_when_env_unset() -> None:
    assert resolve_embedding_dim() == DEFAULT_EMBEDDING_DIM


def test_default_dim_matches_default_model() -> None:
    """Sanity: the documented default pair stays in sync."""
    assert DEFAULT_EMBEDDING_MODEL == "all-MiniLM-L6-v2"
    assert DEFAULT_EMBEDDING_DIM == 384


# ---------------------------------------------------------------------------
# Sprint A target
# ---------------------------------------------------------------------------


def test_sprint_a_model_resolves_matching_dim_without_explicit_dim() -> None:
    os.environ["BRAIN_EMBEDDING_MODEL"] = SPRINT_A_EMBEDDING_MODEL
    assert resolve_embedding_model() == SPRINT_A_EMBEDDING_MODEL
    assert resolve_embedding_dim() == SPRINT_A_EMBEDDING_DIM


def test_sprint_a_constants_anchor_target() -> None:
    """Anchor: the Sprint A target stays at mxbai 1024-dim."""
    assert SPRINT_A_EMBEDDING_MODEL == (
        "mixedbread-ai/mxbai-embed-large-v1"
    )
    assert SPRINT_A_EMBEDDING_DIM == 1024


# ---------------------------------------------------------------------------
# Explicit overrides
# ---------------------------------------------------------------------------


def test_explicit_dim_overrides_known_model() -> None:
    os.environ["BRAIN_EMBEDDING_MODEL"] = SPRINT_A_EMBEDDING_MODEL
    os.environ["BRAIN_EMBEDDING_DIM"] = "768"
    assert resolve_embedding_dim() == 768


def test_explicit_dim_overrides_default_model() -> None:
    os.environ["BRAIN_EMBEDDING_DIM"] = "512"
    assert resolve_embedding_dim() == 512


def test_unknown_model_falls_back_to_default_dim_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    os.environ["BRAIN_EMBEDDING_MODEL"] = "vendor/never-seen-model"
    with caplog.at_level(
        logging.WARNING, logger="brain_engine.memory.embedding_config",
    ):
        dim = resolve_embedding_dim()
    assert dim == DEFAULT_EMBEDDING_DIM
    assert any(
        "Unknown embedding model" in rec.message for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["abc", "1.5", "0xff", " "])
def test_malformed_dim_raises_value_error(raw: str) -> None:
    os.environ["BRAIN_EMBEDDING_DIM"] = raw
    if raw.strip() == "":
        # Empty / whitespace-only falls through to the fallback path.
        assert resolve_embedding_dim() == DEFAULT_EMBEDDING_DIM
        return
    with pytest.raises(ValueError, match="BRAIN_EMBEDDING_DIM"):
        resolve_embedding_dim()


@pytest.mark.parametrize("raw", ["0", "-5", "-1024"])
def test_non_positive_dim_raises_value_error(raw: str) -> None:
    os.environ["BRAIN_EMBEDDING_DIM"] = raw
    with pytest.raises(ValueError, match="positive integer"):
        resolve_embedding_dim()
