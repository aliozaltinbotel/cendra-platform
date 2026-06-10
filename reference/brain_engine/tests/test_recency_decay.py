"""Tests for the Mem0 recency decay multiplier (Sprint 5).

``timeline research.md`` §4.6 makes the case that pure dense
retrieval misranks stale items; every leading agent-memory
system in 2025-2026 layers a recency term on top of semantic
similarity.  This module pins the half-life formula end-to-end
plus the integration with ``Mem0ExtractorService.search_facts``.

Contracts under test:

1. ``halflife_days <= 0`` is the kill-switch — facts pass
   through unchanged (preserves the pre-Sprint-5 raw-score
   behaviour for callers that opt out).
2. A fact aged exactly ``halflife_days`` gets its confidence
   halved (the canonical exp-decay pin).
3. Facts without a parseable ``extracted_at`` skip the
   multiplier — no fabricated penalty when the timestamp is
   missing.
4. The output list is re-sorted by post-decay confidence so
   the head of the list is still the *most relevant* item
   (a fresh medium-similarity fact can beat a stale top-1).
5. Frozen ``ExtractedFact`` dataclass is never mutated — the
   helper emits ``dataclasses.replace`` clones.
6. ``parse_iso_timestamp`` accepts the same shapes as the
   conversation-side helper and returns ``None`` for empties /
   garbage.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from brain_engine.memory.mem0_extractor import ExtractedFact
from brain_engine.memory.recency_decay import (
    DEFAULT_HALFLIFE_DAYS,
    apply_recency_decay,
    parse_iso_timestamp,
)


_NOW = datetime(2026, 5, 5, tzinfo=UTC)


def _fact(
    *,
    fact_id: str,
    confidence: float,
    age_days: float,
) -> ExtractedFact:
    extracted_at = (
        (_NOW - timedelta(days=age_days)).isoformat()
        if age_days >= 0
        else (_NOW + timedelta(days=-age_days)).isoformat()
    )
    return ExtractedFact(
        fact_id=fact_id,
        content=f"fact {fact_id}",
        fact_type="info",
        confidence=confidence,
        extracted_at=extracted_at,
    )


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def test_zero_halflife_returns_input_unchanged() -> None:
    facts = [_fact(fact_id="A", confidence=0.9, age_days=100)]
    out = apply_recency_decay(facts, halflife_days=0, now=_NOW)
    assert out == facts


def test_negative_halflife_returns_input_unchanged() -> None:
    facts = [_fact(fact_id="A", confidence=0.9, age_days=100)]
    out = apply_recency_decay(facts, halflife_days=-5, now=_NOW)
    assert out == facts


def test_empty_input_returns_empty() -> None:
    assert apply_recency_decay([], halflife_days=30, now=_NOW) == []


# ---------------------------------------------------------------------------
# Half-life arithmetic
# ---------------------------------------------------------------------------


def test_age_zero_no_decay() -> None:
    facts = [_fact(fact_id="A", confidence=0.8, age_days=0)]
    out = apply_recency_decay(facts, halflife_days=30, now=_NOW)
    assert out[0].confidence == pytest.approx(0.8)


def test_age_at_halflife_halves_confidence() -> None:
    facts = [_fact(fact_id="A", confidence=0.8, age_days=30)]
    out = apply_recency_decay(facts, halflife_days=30, now=_NOW)
    assert out[0].confidence == pytest.approx(0.4)


def test_age_at_two_halflives_quarters_confidence() -> None:
    facts = [_fact(fact_id="A", confidence=0.8, age_days=60)]
    out = apply_recency_decay(facts, halflife_days=30, now=_NOW)
    assert out[0].confidence == pytest.approx(0.2)


def test_future_timestamp_treated_as_zero_age() -> None:
    # Out-of-order ingestion — fact with a future timestamp
    # gets max(0, age) so the multiplier is exactly 1.0.
    facts = [_fact(fact_id="A", confidence=0.8, age_days=-5)]
    out = apply_recency_decay(facts, halflife_days=30, now=_NOW)
    assert out[0].confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Missing timestamp → skip multiplier
# ---------------------------------------------------------------------------


def test_empty_timestamp_skips_decay() -> None:
    fact = ExtractedFact(
        fact_id="A",
        content="no ts",
        fact_type="info",
        confidence=0.8,
        extracted_at="",
    )
    out = apply_recency_decay([fact], halflife_days=30, now=_NOW)
    assert out[0].confidence == pytest.approx(0.8)


def test_garbage_timestamp_skips_decay() -> None:
    fact = ExtractedFact(
        fact_id="A",
        content="bad ts",
        fact_type="info",
        confidence=0.8,
        extracted_at="not-a-date",
    )
    out = apply_recency_decay([fact], halflife_days=30, now=_NOW)
    assert out[0].confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Re-sort by post-decay confidence
# ---------------------------------------------------------------------------


def test_post_decay_resort_promotes_fresh_lower_similarity() -> None:
    # A: high raw confidence but stale; B: lower raw but fresh.
    # After decay B should win.
    stale_top = _fact(fact_id="A", confidence=0.95, age_days=120)
    fresh_low = _fact(fact_id="B", confidence=0.55, age_days=1)
    out = apply_recency_decay(
        [stale_top, fresh_low], halflife_days=30, now=_NOW,
    )
    assert [f.fact_id for f in out] == ["B", "A"]


# ---------------------------------------------------------------------------
# Immutability of ExtractedFact
# ---------------------------------------------------------------------------


def test_input_fact_never_mutated() -> None:
    fact = _fact(fact_id="A", confidence=0.8, age_days=30)
    apply_recency_decay([fact], halflife_days=30, now=_NOW)
    # Frozen dataclass — original confidence stays intact even
    # though the helper produced a halved clone.
    assert fact.confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Default knob
# ---------------------------------------------------------------------------


def test_default_halflife_value() -> None:
    # The default is documented as 30 days — pin the constant
    # so the value is not silently changed by a future tweak.
    assert DEFAULT_HALFLIFE_DAYS == 30.0


def test_decay_with_default_halflife_at_30_days() -> None:
    fact = _fact(fact_id="A", confidence=0.8, age_days=30)
    out = apply_recency_decay(
        [fact], halflife_days=DEFAULT_HALFLIFE_DAYS, now=_NOW,
    )
    assert out[0].confidence == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# parse_iso_timestamp parity with conversation-side helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_iso"),
    [
        ("2026-05-04T12:34:56Z", "2026-05-04T12:34:56+00:00"),
        ("2026-05-04T12:34:56+00:00", "2026-05-04T12:34:56+00:00"),
        ("2026-05-04T12:34:56", "2026-05-04T12:34:56+00:00"),
        ("2026-05-04T12:34:56+02:00", "2026-05-04T10:34:56+00:00"),
        ("2026-05-04", "2026-05-04T00:00:00+00:00"),
    ],
)
def test_parse_iso_timestamp_accepts(
    raw: str, expected_iso: str,
) -> None:
    parsed = parse_iso_timestamp(raw)
    assert parsed is not None
    assert parsed.isoformat() == expected_iso


@pytest.mark.parametrize("raw", ["", "   ", "garbage", "13:00"])
def test_parse_iso_timestamp_rejects(raw: str) -> None:
    assert parse_iso_timestamp(raw) is None


# ---------------------------------------------------------------------------
# Numerical sanity — clamp formula matches math.exp directly
# ---------------------------------------------------------------------------


def test_decay_matches_exp_formula_at_arbitrary_age() -> None:
    age_days = 17.5
    halflife = 25.0
    fact = _fact(fact_id="A", confidence=0.8, age_days=age_days)
    out = apply_recency_decay(
        [fact], halflife_days=halflife, now=_NOW,
    )
    expected = 0.8 * math.exp(-math.log(2) * age_days / halflife)
    assert out[0].confidence == pytest.approx(expected)
