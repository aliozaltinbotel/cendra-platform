"""Sprint 6 W7 wiring tests for :class:`ContradictionDetector`.

Pins:

* :pyattr:`DetectionResult.workflow_freeze` — defaults to ``False``;
  populated from :func:`should_freeze_workflow` on the maximum
  contradiction confidence so high-confidence contradictions stay
  unfrozen while low-confidence ones halt the workflow.
* ``reliability_resolver`` tiebreaker — when wired, demotes
  ``NEWER_WINS`` to ``EXISTING_WINS`` whenever the new fact is
  classified as less reliable than every contradicting existing
  fact.
* Backward compatibility — without a resolver, behaviour is
  bit-for-bit identical to pre-W7.  A resolver that returns
  ``None`` on either side falls back to the temporal-precedence
  path.
"""

from __future__ import annotations

import pytest

from brain_engine.memory.contradiction_detector import (
    WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD,
    ContradictionDetector,
    DetectionResult,
    Resolution,
    SourceReliability,
)
from brain_engine.memory.fact_store import StoredFact

# ── fixtures ──────────────────────────────────────────────── #


class _StubFactStore:
    """Minimal :class:`FactStore` stub returning a fixed candidate list."""

    def __init__(self, candidates: list[StoredFact]) -> None:
        self._candidates = candidates

    async def search(
        self,
        *,
        query: str,
        property_id: str,
        top_k: int,
    ) -> list[StoredFact]:
        del query, property_id, top_k
        return list(self._candidates)

    async def delete(self, fact_id: str) -> bool:
        return True


class _AlwaysContradictsLLM:
    """LLM stub that flags every pair as a contradiction at a fixed score."""

    def __init__(self, confidence: float = 0.95) -> None:
        self._confidence = confidence

    async def check_contradiction(
        self,
        fact_a: str,
        fact_b: str,
    ) -> dict[str, object]:
        del fact_a, fact_b
        return {
            "is_contradiction": True,
            "explanation": "test contradiction",
            "confidence": self._confidence,
        }


def _make_fact(
    *,
    fact_id: str = "existing-1",
    content: str = "check-in at 14:00",
) -> StoredFact:
    """Build a minimal :class:`StoredFact` for the detector input."""
    return StoredFact(
        fact_id=fact_id,
        content=content,
        fact_type="info",
        property_id="prop-1",
        entity_id="",
        confidence=0.9,
        source="episode-1",
        created_at="2026-04-01T00:00:00Z",
        metadata={},
    )


# ── DetectionResult.workflow_freeze ───────────────────────── #


def test_detection_result_freeze_defaults_to_false() -> None:
    """Default :class:`DetectionResult` carries no freeze flag."""
    assert DetectionResult().workflow_freeze is False


@pytest.mark.asyncio
async def test_high_confidence_contradiction_does_not_freeze() -> None:
    """A high-confidence contradiction keeps the workflow live."""
    store = _StubFactStore(
        [_make_fact(content="check-in at 14:00")],
    )
    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.9),
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.has_contradiction is True
    assert result.workflow_freeze is False  # 0.9 >= 0.60


@pytest.mark.asyncio
async def test_low_confidence_contradiction_freezes_workflow() -> None:
    """A contradiction below 0.60 confidence demands a freeze.

    The detector also routes the resolution to ``FLAG_PM`` because
    the confidence is below ``_CONTRADICTION_CONFIDENCE_CUTOFF``,
    so the freeze flag travels alongside the existing PM-flag
    behaviour.
    """
    store = _StubFactStore(
        [_make_fact(content="check-in at 14:00")],
    )
    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.3),
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.has_contradiction is True
    assert result.workflow_freeze is True  # 0.3 < 0.60
    assert result.resolution == Resolution.FLAG_PM


def test_freeze_threshold_is_section_five_value() -> None:
    """The freeze cutoff still equals the §5 ``0.60`` constant."""
    assert WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD == 0.60


# ── reliability_resolver tiebreaker ───────────────────────── #


@pytest.mark.asyncio
async def test_no_resolver_preserves_legacy_newer_wins() -> None:
    """Without a resolver, NEWER_WINS path is unchanged."""
    store = _StubFactStore(
        [_make_fact(content="check-in at 14:00")],
    )
    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.95),
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.resolution == Resolution.NEWER_WINS
    assert "existing-1" in result.superseded_fact_ids


@pytest.mark.asyncio
async def test_resolver_demotes_when_new_less_reliable() -> None:
    """A less-reliable new fact triggers EXISTING_WINS via tiebreaker.

    Models the §5 hierarchy in action: a guest claim (rank 7) must
    not overwrite a PMS structured field (rank 3) just because the
    guest claim arrived later.  The resolver classifies both
    sides; the detector demotes the resolution accordingly.
    """
    existing = _make_fact(content="check-in at 14:00")
    store = _StubFactStore([existing])

    def resolver(
        fact: StoredFact | str,
        is_new: bool,
    ) -> SourceReliability | None:
        return (
            SourceReliability.GUEST_CLAIM
            if is_new
            else SourceReliability.PMS_STRUCTURED_FIELD
        )

    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.95),
        reliability_resolver=resolver,
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.resolution == Resolution.EXISTING_WINS
    assert result.superseded_fact_ids == ()


@pytest.mark.asyncio
async def test_resolver_keeps_newer_wins_when_new_more_reliable() -> None:
    """A more-reliable new fact still supersedes existing via NEWER_WINS."""
    existing = _make_fact(content="check-in at 14:00")
    store = _StubFactStore([existing])

    def resolver(
        fact: StoredFact | str,
        is_new: bool,
    ) -> SourceReliability | None:
        # New: PMS field; existing: guest claim — new wins on both
        # axes (temporal + reliability).
        return (
            SourceReliability.PMS_STRUCTURED_FIELD
            if is_new
            else SourceReliability.GUEST_CLAIM
        )

    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.95),
        reliability_resolver=resolver,
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.resolution == Resolution.NEWER_WINS


@pytest.mark.asyncio
async def test_resolver_returning_none_falls_back_to_temporal() -> None:
    """An opt-out from the resolver keeps the legacy NEWER_WINS decision.

    A safety net for resolvers that cannot classify every fact
    (e.g. metadata missing for the existing fact).  Returning
    ``None`` from either side short-circuits the tiebreaker so the
    temporal-precedence default still applies.
    """
    existing = _make_fact(content="check-in at 14:00")
    store = _StubFactStore([existing])

    def resolver(
        fact: StoredFact | str,
        is_new: bool,
    ) -> SourceReliability | None:
        del fact, is_new
        return None

    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.95),
        reliability_resolver=resolver,
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.resolution == Resolution.NEWER_WINS


@pytest.mark.asyncio
async def test_resolver_does_not_demote_on_tie() -> None:
    """Equal-reliability classification keeps NEWER_WINS.

    The §5 hierarchy says the tiebreaker only fires when one side
    is strictly less reliable.  A tie ⇒ fall through to temporal
    precedence (NEWER_WINS).
    """
    existing = _make_fact(content="check-in at 14:00")
    store = _StubFactStore([existing])

    def resolver(
        fact: StoredFact | str,
        is_new: bool,
    ) -> SourceReliability | None:
        del fact, is_new
        return SourceReliability.PMS_STRUCTURED_FIELD

    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.95),
        reliability_resolver=resolver,
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.resolution == Resolution.NEWER_WINS


# ── workflow_freeze + reliability interaction ─────────────── #


@pytest.mark.asyncio
async def test_existing_wins_can_still_carry_freeze_flag() -> None:
    """When the resolver demotes to EXISTING_WINS, freeze still tracks confidence.

    A low-confidence contradiction routes through ``FLAG_PM`` so
    the demotion path never fires (the resolver runs only on the
    high-confidence path).  Here we hold confidence above the
    cutoff and verify that ``workflow_freeze`` stays
    ``False`` — the contradiction itself is well-understood, the
    detector simply preferred the structured source.
    """
    existing = _make_fact(content="check-in at 14:00")
    store = _StubFactStore([existing])

    def resolver(
        fact: StoredFact | str,
        is_new: bool,
    ) -> SourceReliability | None:
        return (
            SourceReliability.GUEST_CLAIM
            if is_new
            else SourceReliability.PMS_STRUCTURED_FIELD
        )

    detector = ContradictionDetector(
        fact_store=store,
        llm=_AlwaysContradictsLLM(confidence=0.9),
        reliability_resolver=resolver,
    )
    result = await detector.check(
        new_content="check-in at 15:00",
        property_id="prop-1",
        new_timestamp="2026-05-13T00:00:00Z",
    )
    assert result.resolution == Resolution.EXISTING_WINS
    assert result.workflow_freeze is False
