"""Tests for the contradiction-detection wiring inside step 1.

The detector hook is a no-op by default — these tests pin every
gate and the resolution branches so a regression that drops valid
facts (or stores contradicting ones) shows up immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain_engine.continual_learning.nightly_consolidator import (
    NightlyConsolidator,
)
from brain_engine.memory.contradiction_detector import (
    DetectionResult,
    Resolution,
)
from brain_engine.memory.fact_store import StoredFact


def _fact(idx: int, content: str = "fact text") -> StoredFact:
    return StoredFact(
        fact_id=f"fact-{idx}",
        content=content,
        fact_type="info",
        entity_id="prop-1",
        confidence=0.9,
        source="conv-1",
        created_at=datetime.now(timezone.utc),
    )


def _build_consolidator(
    detector: MagicMock | None = None,
) -> NightlyConsolidator:
    return NightlyConsolidator(
        memory=MagicMock(),
        skills=MagicMock(),
        recorder=MagicMock(),
        grader=MagicMock(),
        contradiction_detector=detector,
    )


@pytest.mark.asyncio
async def test_filter_skipped_when_no_detector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_CONTRADICTION_CHECK_ENABLED", "1")
    consolidator = _build_consolidator(detector=None)
    facts = [_fact(0), _fact(1)]

    result = await consolidator._filter_contradicting_facts(facts)

    assert result == facts
    assert consolidator._contradiction_stats["checked"] == 0
    assert consolidator._contradiction_stats["skipped"] == 2


@pytest.mark.asyncio
async def test_filter_skipped_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRAIN_CONTRADICTION_CHECK_ENABLED", raising=False)
    detector = MagicMock()
    detector.check = AsyncMock()
    consolidator = _build_consolidator(detector=detector)
    facts = [_fact(0)]

    result = await consolidator._filter_contradicting_facts(facts)

    assert result == facts
    detector.check.assert_not_called()
    assert consolidator._contradiction_stats["skipped"] == 1


@pytest.mark.asyncio
async def test_filter_keeps_facts_without_contradictions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_CONTRADICTION_CHECK_ENABLED", "1")
    detector = MagicMock()
    detector.check = AsyncMock(return_value=DetectionResult())
    consolidator = _build_consolidator(detector=detector)
    facts = [_fact(0), _fact(1)]

    result = await consolidator._filter_contradicting_facts(facts)

    assert len(result) == 2
    assert detector.check.await_count == 2
    assert consolidator._contradiction_stats["checked"] == 2
    assert consolidator._contradiction_stats["contradictions"] == 0


@pytest.mark.asyncio
async def test_filter_drops_pm_flagged_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_CONTRADICTION_CHECK_ENABLED", "1")
    detector = MagicMock()
    detector.check = AsyncMock(
        return_value=DetectionResult(
            has_contradiction=True,
            resolution=Resolution.FLAG_PM,
        ),
    )
    consolidator = _build_consolidator(detector=detector)
    facts = [_fact(0)]

    result = await consolidator._filter_contradicting_facts(facts)

    assert result == []
    assert consolidator._contradiction_stats["contradictions"] == 1
    assert consolidator._contradiction_stats["flagged_for_pm"] == 1


@pytest.mark.asyncio
async def test_filter_keeps_newer_wins_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_CONTRADICTION_CHECK_ENABLED", "1")
    detector = MagicMock()
    detector.check = AsyncMock(
        return_value=DetectionResult(
            has_contradiction=True,
            resolution=Resolution.NEWER_WINS,
        ),
    )
    consolidator = _build_consolidator(detector=detector)
    fact = _fact(0)

    result = await consolidator._filter_contradicting_facts([fact])

    assert result == [fact]
    assert consolidator._contradiction_stats["contradictions"] == 1
    assert consolidator._contradiction_stats["newer_wins"] == 1


@pytest.mark.asyncio
async def test_filter_fails_open_on_detector_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flaky detector must not block fact persistence."""
    monkeypatch.setenv("BRAIN_CONTRADICTION_CHECK_ENABLED", "1")
    detector = MagicMock()
    detector.check = AsyncMock(side_effect=RuntimeError("LLM 500"))
    consolidator = _build_consolidator(detector=detector)
    fact = _fact(0)

    result = await consolidator._filter_contradicting_facts([fact])

    assert result == [fact]
    assert consolidator._contradiction_stats["checked"] == 1
    # Exception path skips contradiction counters entirely.
    assert consolidator._contradiction_stats["contradictions"] == 0


@pytest.mark.asyncio
async def test_filter_passes_fact_metadata_to_detector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_CONTRADICTION_CHECK_ENABLED", "1")
    detector = MagicMock()
    detector.check = AsyncMock(return_value=DetectionResult())
    consolidator = _build_consolidator(detector=detector)
    fact = _fact(0, "checkout is at 11 AM")

    await consolidator._filter_contradicting_facts([fact])

    detector.check.assert_awaited_once()
    call = detector.check.await_args
    assert call.kwargs["new_content"] == "checkout is at 11 AM"
    assert call.kwargs["property_id"] == "prop-1"
    assert call.kwargs["new_timestamp"]  # ISO timestamp non-empty
