"""Tests for the episodic dedup wiring inside NightlyConsolidator step 1.

The dedup pass is a no-op by default — these tests pin every gate
and the happy path so a regression that silently disables dedup or
silently runs it without an encoder shows up immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.brain.cognition.continual.nightly_consolidator import (
    NightlyConsolidator,
)
from core.brain.memory.episodic_dedup import (
    DedupConfig,
    EpisodicDedupConsolidator,
)
from core.brain.memory.episodic_memory import Episode


def _episode(idx: int, text: str) -> Episode:
    return Episode(
        event="conversation",
        content=text,
        id=f"ep-{idx}",
        timestamp=datetime.now(UTC),
    )


def _build_consolidator(
    *,
    episodes: list[Episode] | None = None,
    encoder: Any = None,
    dedup: EpisodicDedupConsolidator | None = None,
) -> NightlyConsolidator:
    """Build a NightlyConsolidator with the minimal stubs each test needs.

    Only ``self._memory.episodic.get_recent`` and
    ``self._memory.semantic._encoder`` are touched by
    ``_dedup_recent_episodes``; everything else is irrelevant.
    """
    memory = MagicMock()
    memory.episodic = MagicMock()
    memory.episodic.get_recent = MagicMock(return_value=episodes or [])
    memory.semantic = MagicMock()
    memory.semantic._encoder = encoder

    return NightlyConsolidator(
        memory=memory,
        skills=MagicMock(),
        recorder=MagicMock(),
        grader=MagicMock(),
        dedup_consolidator=dedup,
    )


def test_dedup_skipped_when_no_consolidator() -> None:
    consolidator = _build_consolidator(dedup=None)

    result = consolidator._dedup_recent_episodes()

    assert result == {"skipped": True, "reason": "no_consolidator"}


def test_dedup_skipped_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRAIN_EPISODIC_DEDUP_ENABLED", raising=False)
    consolidator = _build_consolidator(
        dedup=EpisodicDedupConsolidator(),
    )

    result = consolidator._dedup_recent_episodes()

    assert result == {"skipped": True, "reason": "flag_off"}


def test_dedup_skipped_when_encoder_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_EPISODIC_DEDUP_ENABLED", "1")
    consolidator = _build_consolidator(
        dedup=EpisodicDedupConsolidator(),
        encoder=None,
    )

    result = consolidator._dedup_recent_episodes()

    assert result == {"skipped": True, "reason": "no_encoder"}


def test_dedup_returns_zero_counts_for_empty_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_EPISODIC_DEDUP_ENABLED", "1")
    encoder = MagicMock()
    consolidator = _build_consolidator(
        dedup=EpisodicDedupConsolidator(),
        episodes=[],
        encoder=encoder,
    )

    result = consolidator._dedup_recent_episodes()

    assert result == {"input_count": 0, "kept_count": 0, "removed_count": 0}
    encoder.encode.assert_not_called()


def test_dedup_consolidates_near_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_EPISODIC_DEDUP_ENABLED", "1")

    # Encoder that returns near-identical vectors for "duplicate"
    # texts and a clearly orthogonal vector for the third one.
    def fake_encode(text: str, normalize_embeddings: bool = True) -> Any:
        if text.startswith("duplicate"):
            arr = MagicMock()
            arr.tolist.return_value = [1.0, 0.0, 0.0]
            return arr
        arr = MagicMock()
        arr.tolist.return_value = [0.0, 1.0, 0.0]
        return arr

    encoder = MagicMock()
    encoder.encode.side_effect = fake_encode

    episodes = [
        _episode(0, "duplicate A"),
        _episode(1, "duplicate B"),
        _episode(2, "totally different"),
    ]

    consolidator = _build_consolidator(
        dedup=EpisodicDedupConsolidator(
            DedupConfig(similarity_threshold=0.9, min_cluster_size=2),
        ),
        episodes=episodes,
        encoder=encoder,
    )

    result = consolidator._dedup_recent_episodes()

    assert result["input_count"] == 3
    # Two near-duplicates fold to one representative; the orthogonal
    # singleton survives untouched.
    assert result["kept_count"] == 2
    assert result["removed_count"] == 1
    assert result["summary_count"] >= 1
    assert encoder.encode.call_count == 3


def test_dedup_skips_episode_when_encoder_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_EPISODIC_DEDUP_ENABLED", "1")

    arr = MagicMock()
    arr.tolist.return_value = [1.0, 0.0]

    encoder = MagicMock()
    encoder.encode.side_effect = [
        Exception("encoder boom"),
        arr,
    ]

    episodes = [_episode(0, "bad"), _episode(1, "good")]

    consolidator = _build_consolidator(
        dedup=EpisodicDedupConsolidator(),
        episodes=episodes,
        encoder=encoder,
    )

    result = consolidator._dedup_recent_episodes()

    # First episode dropped; second one survives the consolidation.
    assert result["input_count"] == 1
    assert result["kept_count"] == 1
    assert result["removed_count"] == 0


def test_run_nightly_includes_episodic_dedup_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 1 surfaces the dedup result in the stats dict."""
    monkeypatch.delenv("BRAIN_EPISODIC_DEDUP_ENABLED", raising=False)
    consolidator = _build_consolidator(dedup=None)
    # Stub remaining steps so run_nightly does not need real backends.
    consolidator._memory.consolidator = MagicMock()
    consolidator._memory.consolidator.consolidate = MagicMock(return_value={})
    consolidator._step2_evolve_skills = MagicMock(return_value={})  # type: ignore[method-assign]
    consolidator._step3_aggregate_preferences = MagicMock(return_value={})  # type: ignore[method-assign]
    consolidator._step4_update_knowledge_graph = MagicMock(return_value={})  # type: ignore[method-assign]
    consolidator._step5_prune_skills = MagicMock(return_value={})  # type: ignore[method-assign]
    consolidator._step6_mira_ready = MagicMock(return_value={})  # type: ignore[method-assign]
    consolidator._step7_extract_patterns = MagicMock(return_value={})  # type: ignore[method-assign]
    consolidator._decay_skill_confidence = MagicMock(return_value=None)  # type: ignore[method-assign]

    stats = consolidator.run_nightly()

    assert "step1_memory" in stats
    assert stats["step1_memory"]["episodic_dedup"] == {
        "skipped": True,
        "reason": "no_consolidator",
    }
