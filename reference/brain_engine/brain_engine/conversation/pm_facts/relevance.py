"""Lightweight relevance metrics for PM-confirmed knowledge facts.

Pure measurement utilities used by the diagnostic logging in
:meth:`ConversationService._append_pm_facts`.  No retrieval or
filtering decisions are taken here — the goal is to gather data over
a few days so the team can decide, on evidence, whether to move from
"dump every PM fact into the system prompt" to a topic-relevant
top-K retrieval.

The metric used is Jaccard overlap on tokenised text.  It is
deliberately cheap (no embedder, no network), runs in-process, and
has no hardcoded vocabularies or keyword lists — values are purely a
function of the input strings.

The module also owns the log emission so the call site in the
already-large ``service.py`` stays a one-line invocation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "PmFactRelevanceStats",
    "compute_pm_fact_relevance_stats",
    "log_pm_fact_relevance",
]


_RELEVANCE_LOG_TEMPLATE = (
    "pm_facts.relevance count=%d total_chars=%d "
    "message_chars=%d jaccard_max=%.3f "
    "jaccard_mean=%.3f jaccard_min=%.3f "
    "property=%s customer=%s"
)


_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class PmFactRelevanceStats:
    """Aggregate per-batch relevance statistics for the diagnostic.

    All Jaccard values lie in ``[0.0, 1.0]``.  When ``count`` is zero
    (no facts to score, or an empty message), every Jaccard field is
    ``0.0`` — callers should rely on ``count`` to know whether the
    Jaccard fields carry meaningful signal.
    """

    count: int
    total_chars: int
    message_chars: int
    jaccard_max: float
    jaccard_mean: float
    jaccard_min: float


def _tokenise(text: str) -> set[str]:
    """Return the lower-cased token set of ``text``.

    Splits on Unicode word boundaries (``\\w+``), which keeps both
    Latin and Cyrillic / Turkish letters intact.  Returns an empty
    set for empty input.
    """
    if not text:
        return set()
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text)}


def _jaccard(left: set[str], right: set[str]) -> float:
    """Jaccard similarity on token sets.

    Returns ``0.0`` when either side is empty — the diagnostic must
    not surface a misleading ``1.0`` for the empty/empty case.
    """
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def compute_pm_fact_relevance_stats(
    fact_texts: Sequence[str],
    message: str,
) -> PmFactRelevanceStats:
    """Compute per-batch relevance stats for diagnostic logging.

    Args:
        fact_texts: PM-confirmed knowledge entries about to be
            merged into the system prompt.  Whitespace-only entries
            are ignored — they would not appear in the rendered
            bulleted list either.
        message: Cleaned guest message of the current turn.

    Returns:
        Aggregate Jaccard statistics across every (fact, message)
        pair.  When the message has no tokens, or no facts survive
        whitespace filtering, every Jaccard field is ``0.0`` and the
        caller should treat them as "no signal".

    Pure function — no I/O, no globals, no hardcoded keyword lists.
    """
    message_tokens = _tokenise(message)
    message_chars = len(message)

    cleaned = [text for text in fact_texts if text.strip()]
    count = len(cleaned)
    total_chars = sum(len(text) for text in cleaned)

    if count == 0 or not message_tokens:
        return PmFactRelevanceStats(
            count=count,
            total_chars=total_chars,
            message_chars=message_chars,
            jaccard_max=0.0,
            jaccard_mean=0.0,
            jaccard_min=0.0,
        )

    scores = [_jaccard(_tokenise(text), message_tokens) for text in cleaned]
    return PmFactRelevanceStats(
        count=count,
        total_chars=total_chars,
        message_chars=message_chars,
        jaccard_max=max(scores),
        jaccard_mean=sum(scores) / len(scores),
        jaccard_min=min(scores),
    )


def log_pm_fact_relevance(
    fact_texts: Sequence[str],
    message: str,
    *,
    property_id: str,
    customer_id: str,
    logger: logging.Logger | None = None,
) -> PmFactRelevanceStats:
    """Compute the relevance stats and emit one INFO log line.

    Centralises the diagnostic so the conversation pipeline keeps a
    one-line call site — the service module is already large, and
    the diagnostic does not need to live next to the merge logic to
    be understood.

    Args:
        fact_texts: PM-confirmed knowledge entries about to be merged
            into the system prompt.
        message: Cleaned guest message of the current turn.
        property_id: Property scope (logged for triage).
        customer_id: Customer scope (logged for triage).
        logger: Optional caller-supplied logger.  Defaults to a
            module-local one so direct callers (tests, scripts) still
            see the diagnostic without injecting a logger.

    Returns:
        The :class:`PmFactRelevanceStats` that were logged, so the
        caller can re-use the values (e.g. for emitting structured
        events later) without recomputing.
    """
    stats = compute_pm_fact_relevance_stats(fact_texts, message)
    target_logger = (
        logger if logger is not None else logging.getLogger(__name__)
    )
    target_logger.info(
        _RELEVANCE_LOG_TEMPLATE,
        stats.count,
        stats.total_chars,
        stats.message_chars,
        stats.jaccard_max,
        stats.jaccard_mean,
        stats.jaccard_min,
        property_id,
        customer_id,
    )
    return stats
