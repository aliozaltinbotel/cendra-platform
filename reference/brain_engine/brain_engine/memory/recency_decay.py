"""Recency decay multiplier for Mem0 retrieval (Sprint 5).

``timeline research.md`` §4.6 makes the case that pure dense
retrieval is no longer state-of-the-art for agent memory: every
top system in 2025-2026 (Zep, Hindsight, Supermemory, Mem0
itself in their April-2026 algorithm refresh) layers a recency /
decay term on top of the semantic similarity score before
ranking.  Without it the retriever happily surfaces a six-month-
old preference that has since shifted alongside last-week's
correction.

This module provides the decay term as a tiny, pure-Python
helper so the existing ``Mem0ExtractorService.search_facts``
pipeline can opt in without restructuring the retrieval layer.
The shape mirrors LangChain's ``TimeWeightedVectorStoreRetriever``
(arXiv 2304.03442): one knob (``halflife_days``) drives an
exponential decay multiplier applied to each candidate's
``confidence``.

Why exponential and not linear / Gaussian:

* The Park et al. paper (Generative Agents) and the Milvus 2.6
  decay reranker both default to exponential because the
  half-life parameter has the cleanest operational semantics
  ("how old before the item is worth half").
* Linear decay forces an arbitrary "zero day" cliff; Gaussian
  needs a sigma the operator has no intuition for.
* Exponential reduces to "no decay" when ``halflife_days <= 0``
  — cheap kill switch for environments that want raw semantic
  ranking back.

No LLM, no extra infra.  The cost is one ``math.exp`` per
returned fact.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Final

from brain_engine.memory.mem0_extractor import ExtractedFact

__all__ = [
    "DEFAULT_HALFLIFE_DAYS",
    "apply_recency_decay",
    "parse_iso_timestamp",
]


# Default tuned for property-management facts: PM preferences
# tend to shift on a quarterly cadence, so a 30-day half-life
# pulls a 60-day-old preference to ~25 %, a 90-day-old one to
# ~12.5 %.  Operators override per call site when they have
# better data.
DEFAULT_HALFLIFE_DAYS: Final[float] = 30.0
_LN2: Final[float] = math.log(2.0)


def parse_iso_timestamp(raw: str) -> datetime | None:
    """Best-effort parse of an ISO-8601 timestamp into aware UTC.

    Mirrors the conversation-side helper but lives here so
    ``apply_recency_decay`` stays import-self-contained.
    Returns ``None`` for empty / unparseable input — caller
    treats that as "no timestamp known" and skips decay.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def apply_recency_decay(
    facts: Iterable[ExtractedFact],
    *,
    halflife_days: float = DEFAULT_HALFLIFE_DAYS,
    now: datetime | None = None,
) -> list[ExtractedFact]:
    """Multiply each fact's ``confidence`` by an exp-decay factor.

    The decay factor is ``exp(-ln(2) * age_days / halflife_days)``
    so ``age_days == halflife_days`` exactly halves the
    confidence.  Facts without a parseable ``extracted_at``
    skip the multiplier (no fabricated penalty when the
    timestamp is missing).  After multiplication the list is
    re-sorted by ``confidence`` descending so callers that
    take the head of the list still see the most-relevant
    item first.

    Args:
        facts: Iterable of facts as returned by
            :meth:`Mem0ExtractorService.search_facts`.
        halflife_days: Days at which a fact's confidence is
            halved.  Non-positive values disable the decay
            entirely — the input list is returned unchanged.
        now: Reference instant for the age computation.
            Defaults to current UTC; tests inject a frozen
            value here.

    Returns:
        A new list of :class:`ExtractedFact` instances ordered
        by post-decay confidence descending.  The input
        ``facts`` is never mutated (the underlying dataclass is
        frozen — we emit ``dataclasses.replace`` copies).
    """
    materialised: Sequence[ExtractedFact] = (
        list(facts) if not isinstance(facts, Sequence) else facts
    )
    if halflife_days <= 0 or not materialised:
        return list(materialised)

    reference = now or datetime.now(UTC)
    decay_rate = _LN2 / halflife_days
    decayed: list[ExtractedFact] = []
    for fact in materialised:
        timestamp = parse_iso_timestamp(fact.extracted_at)
        if timestamp is None:
            decayed.append(fact)
            continue
        age_seconds = max(
            0.0, (reference - timestamp).total_seconds(),
        )
        age_days = age_seconds / 86400.0
        multiplier = math.exp(-decay_rate * age_days)
        decayed.append(
            replace(fact, confidence=fact.confidence * multiplier),
        )
    decayed.sort(key=lambda f: f.confidence, reverse=True)
    return decayed
