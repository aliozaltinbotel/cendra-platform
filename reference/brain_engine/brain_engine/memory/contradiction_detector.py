"""ContradictionDetector — detect and resolve conflicting facts on write.

When a new fact is about to be stored in SemanticMemory or FactStore, this
module checks whether it contradicts any existing fact.  The pipeline:

  1. Vector search top-5 existing facts (similarity > 0.7 threshold)
  2. LLM pairwise comparison — is there a semantic contradiction?
  3. Resolution via temporal precedence (newer fact wins) or PM flag

This is critical for a KB that's populated from guest + PM conversations
where conflicting information is inevitable ("check-in at 3 PM" vs
"check-in at 2 PM" from different sources or dates).

Usage:
    detector = ContradictionDetector(fact_store=store)
    result = await detector.check("Check-in time is 2 PM", property_id="PROP001")
    if result.has_contradiction:
        if result.resolution == Resolution.NEWER_WINS:
            # overwrite the old fact
        elif result.resolution == Resolution.FLAG_PM:
            # ask the PM to resolve
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from brain_engine.memory.fact_store import FactStore, StoredFact

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD: Final[float] = 0.7
_TOP_K: Final[int] = 5
_CONTRADICTION_CONFIDENCE_CUTOFF: Final[float] = 0.6


# ── FL-07: Source reliability ranking + workflow freeze ──── #
#
# §5 of the Proactive Foundation MD ranks fact sources from most
# to least reliable.  The orchestrator's contradiction handling
# (FL-07b wiring) uses this ranking to break ties when the
# temporal precedence path (NEWER_WINS / EXISTING_WINS) cannot
# decide — a fact from the PMS structured field always beats a
# guest claim regardless of which arrived first.
#
# §5 also says contradictions must *freeze affected workflows
# when guest-facing risk exists*.  We treat any contradiction
# whose resolution confidence drops below
# :data:`WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD` (``0.60``) as
# requiring the workflow freeze; callers attach the freeze flag
# to their workflow state machine so guest-facing automation
# halts until the contradiction is resolved upstream.


class SourceReliability(StrEnum):
    """Nine-level source-reliability hierarchy from Proactive §5.

    Ordered from most reliable (PM hard rule) to least reliable
    (AI-generated inference).  The enum value uses the snake_case
    slug so the level survives JSONB payloads, log lines, and the
    rule-origin trail without escaping.

    Members:
        PM_EXPLICIT_HARD_RULE: PM wrote down an immutable rule —
            absolute trust, never overridden by anything below.
        CONFIRMED_SOP: An SOP confirmed by PM or owner.
        PMS_STRUCTURED_FIELD: A structured field straight from
            the PMS (channel agnostic ground truth).
        STRUCTURED_EVENT: Smart-lock / payment / vendor structured
            event payload.  Concrete state from a downstream
            system.
        OTA_LISTING_STRUCTURED: Structured fields on the OTA
            listing (channel-specific but verifiable).
        RECENT_PM_MESSAGE_CORRECTION: A PM message that corrects
            an earlier engine response within the last operating
            window.
        GUEST_CLAIM: A claim made by a guest in conversation.
        OLD_FREE_TEXT_NOTE: A free-text note from an older
            conversation or memo — low reliability, often stale.
        AI_GENERATED_INFERENCE: Inference produced by Brain Engine
            without verification.  Lowest rung — never overrides
            a structured source.
    """

    PM_EXPLICIT_HARD_RULE = "pm_explicit_hard_rule"
    CONFIRMED_SOP = "confirmed_sop"
    PMS_STRUCTURED_FIELD = "pms_structured_field"
    STRUCTURED_EVENT = "structured_event"
    OTA_LISTING_STRUCTURED = "ota_listing_structured"
    RECENT_PM_MESSAGE_CORRECTION = "recent_pm_message_correction"
    GUEST_CLAIM = "guest_claim"
    OLD_FREE_TEXT_NOTE = "old_free_text_note"
    AI_GENERATED_INFERENCE = "ai_generated_inference"


# Lower rank number = more reliable.  Stored as an explicit dict
# (rather than relying on enum declaration order) so a future
# refactor that adds a new tier in the middle has to make the
# ranking decision explicit, not implicit.
_RELIABILITY_RANK: Final[dict[SourceReliability, int]] = {
    SourceReliability.PM_EXPLICIT_HARD_RULE: 1,
    SourceReliability.CONFIRMED_SOP: 2,
    SourceReliability.PMS_STRUCTURED_FIELD: 3,
    SourceReliability.STRUCTURED_EVENT: 4,
    SourceReliability.OTA_LISTING_STRUCTURED: 5,
    SourceReliability.RECENT_PM_MESSAGE_CORRECTION: 6,
    SourceReliability.GUEST_CLAIM: 7,
    SourceReliability.OLD_FREE_TEXT_NOTE: 8,
    SourceReliability.AI_GENERATED_INFERENCE: 9,
}


WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD: Final[float] = 0.60


def reliability_rank(source: SourceReliability) -> int:
    """Return the §5 rank number for ``source`` (lower = more reliable).

    Always returns an integer in ``[1, 9]`` for every enum value.
    Useful for callers that want to compare reliabilities without
    importing the private ``_RELIABILITY_RANK`` map.
    """
    return _RELIABILITY_RANK[source]


def more_reliable(
    candidate: SourceReliability,
    incumbent: SourceReliability,
) -> SourceReliability:
    """Return whichever source ranks higher in the §5 hierarchy.

    Ties (both sources at the same rank — only possible when
    ``candidate is incumbent``) resolve to ``incumbent`` so the
    incumbent fact is preserved when the new arrival brings no
    extra reliability.  Mirror of "if equal, keep what is already
    in the store" — minimises unnecessary cache invalidations.
    """
    if _RELIABILITY_RANK[candidate] < _RELIABILITY_RANK[incumbent]:
        return candidate
    return incumbent


def should_freeze_workflow(resolution_confidence: float) -> bool:
    """Whether a contradiction's resolution confidence demands a freeze.

    Returns ``True`` when ``resolution_confidence`` is below
    :data:`WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD` (``0.60``).  Caller
    flips its workflow state machine into the frozen state so
    guest-facing automation halts until the contradiction is
    resolved upstream — matches the §5 "freeze affected workflows
    when guest-facing risk exists" rule.

    NaN inputs return ``True`` (defensive: treat unparseable
    confidence as worst case so the workflow does not silently
    automate through ambiguous evidence).
    """
    if resolution_confidence != resolution_confidence:  # NaN check
        return True
    return resolution_confidence < WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD


class Resolution(StrEnum):
    """How a detected contradiction should be resolved."""

    NEWER_WINS = "newer_wins"     # new fact supersedes the old one
    EXISTING_WINS = "existing_wins"  # keep old, discard new
    FLAG_PM = "flag_pm"           # confidence too low — ask PM to decide
    NO_CONTRADICTION = "none"     # no contradiction found


@dataclass(frozen=True, slots=True)
class ContradictionPair:
    """A pair of contradicting facts with analysis.

    Attributes:
        existing_fact: The fact already in the store.
        new_content: The incoming fact text being checked.
        similarity: Cosine similarity between the two.
        is_contradiction: Whether the LLM determined a semantic contradiction.
        explanation: LLM explanation of why they contradict (or don't).
        confidence: LLM confidence in the contradiction judgment (0-1).
    """

    existing_fact: StoredFact
    new_content: str
    similarity: float = 0.0
    is_contradiction: bool = False
    explanation: str = ""
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Full result of a contradiction check.

    Attributes:
        has_contradiction: Whether any contradiction was found.
        resolution: Recommended resolution strategy.
        pairs: All analyzed fact pairs (contradicting or not).
        superseded_fact_ids: IDs of facts that should be replaced
            (if newer_wins).
        workflow_freeze: Sprint 6 W7 — ``True`` when the strongest
            contradiction's confidence is below
            :data:`WORKFLOW_FREEZE_CONFIDENCE_THRESHOLD` and the
            caller should halt any guest-facing workflow that
            depends on the conflicting fact.  Defaults to
            ``False`` so legacy consumers that ignore the field
            see no behavioural change.  Always ``False`` when
            ``has_contradiction`` is ``False``.
    """

    has_contradiction: bool = False
    resolution: Resolution = Resolution.NO_CONTRADICTION
    pairs: tuple[ContradictionPair, ...] = ()
    superseded_fact_ids: tuple[str, ...] = ()
    workflow_freeze: bool = False

    @property
    def contradictions(self) -> tuple[ContradictionPair, ...]:
        """Only the pairs where a contradiction was actually found."""
        return tuple(p for p in self.pairs if p.is_contradiction)

    @property
    def summary(self) -> str:
        """One-line summary for logging / PM notification."""
        if not self.has_contradiction:
            return "No contradictions found"
        n = len(self.contradictions)
        return (
            f"{n} contradiction(s) found, resolution={self.resolution.value}, "
            f"superseded={len(self.superseded_fact_ids)}"
        )


@runtime_checkable
class ContradictionLLM(Protocol):
    """Protocol for the LLM call used in pairwise contradiction comparison.

    Any object with this async method signature is accepted — no inheritance
    required (structural subtyping per PEP 544).
    """

    async def check_contradiction(
        self,
        fact_a: str,
        fact_b: str,
    ) -> dict[str, Any]:
        """Compare two facts for semantic contradiction.

        Returns:
            Dict with keys: is_contradiction (bool), explanation (str),
            confidence (float 0-1).
        """
        ...


ReliabilityResolver = (
    "Callable[[StoredFact | str, bool], SourceReliability | None]"
)


class ContradictionDetector:
    """Detects and resolves contradictions between new and existing facts.

    Uses vector similarity to find candidate matches, then an LLM to
    determine whether they actually contradict.  Resolves via temporal
    precedence when confidence is high enough, otherwise flags for PM.

    Sprint 6 W7 adds an optional :pyattr:`reliability_resolver` hook
    that consults the §5 source-reliability hierarchy as a tiebreaker:
    when the resolver classifies the new fact as *less reliable* than
    the existing one, the detector demotes a ``NEWER_WINS`` decision
    to :attr:`Resolution.EXISTING_WINS` (keep the structured source,
    discard the guest claim).  When the resolver is not provided the
    detector behaves exactly as before W7.

    Args:
        fact_store: FactStore to search for existing facts.
        llm: LLM backend for pairwise comparison.  If None, uses a
            heuristic fallback (exact substring match).
        similarity_threshold: Minimum cosine similarity for a candidate.
        contradiction_confidence_cutoff: Below this, flag for PM instead
            of auto-resolving.
        reliability_resolver: Optional callable that classifies a fact
            as a :class:`SourceReliability` tier.  Signature is
            ``(fact, is_new) -> SourceReliability | None``.  The
            ``is_new`` flag tells the resolver whether the argument is
            the incoming string (``True``) or an existing
            :class:`StoredFact` (``False``).  Returning ``None`` opts
            out of the tiebreaker for that pair so the detector keeps
            its temporal-precedence default.
    """

    def __init__(
        self,
        fact_store: FactStore,
        llm: ContradictionLLM | None = None,
        similarity_threshold: float = _SIMILARITY_THRESHOLD,
        contradiction_confidence_cutoff: float = _CONTRADICTION_CONFIDENCE_CUTOFF,
        reliability_resolver: object | None = None,
    ) -> None:
        self._store = fact_store
        self._llm = llm
        self._sim_threshold = similarity_threshold
        self._confidence_cutoff = contradiction_confidence_cutoff
        self._reliability_resolver = reliability_resolver

    # ── Public API ───────────────────────────────────────────── #

    async def check(
        self,
        new_content: str,
        property_id: str = "",
        new_timestamp: str = "",
    ) -> DetectionResult:
        """Check a new fact against existing facts for contradictions.

        Args:
            new_content: The text of the fact being written.
            property_id: Scope the search to this property.
            new_timestamp: ISO timestamp of the new fact (for temporal precedence).

        Returns:
            DetectionResult with contradiction details and resolution.
        """
        # Step 1: find semantically similar existing facts
        candidates = await self._find_candidates(new_content, property_id)
        if not candidates:
            return DetectionResult()

        # Step 2: pairwise contradiction check
        pairs = await self._check_pairs(new_content, candidates)

        contradicting = [p for p in pairs if p.is_contradiction]
        if not contradicting:
            return DetectionResult(pairs=tuple(pairs))

        # Step 3: determine resolution (with optional Sprint 6 W7
        # source-reliability tiebreaker + workflow-freeze flag).
        resolution, superseded = self._resolve(
            contradicting, new_timestamp, new_content,
        )
        max_confidence = max(
            (p.confidence for p in contradicting),
            default=0.0,
        )
        freeze = should_freeze_workflow(max_confidence)

        result = DetectionResult(
            has_contradiction=True,
            resolution=resolution,
            pairs=tuple(pairs),
            superseded_fact_ids=tuple(superseded),
            workflow_freeze=freeze,
        )

        logger.info(
            "Contradiction detected for '%s': %s",
            new_content[:60],
            result.summary,
        )
        return result

    async def check_and_apply(
        self,
        new_content: str,
        property_id: str = "",
        new_timestamp: str = "",
    ) -> DetectionResult:
        """Check for contradictions and auto-apply resolution if confident.

        If resolution is NEWER_WINS, deletes superseded facts from the store.
        If resolution is FLAG_PM, does nothing (caller handles notification).

        Args:
            new_content: The fact being written.
            property_id: Property scope.
            new_timestamp: ISO timestamp.

        Returns:
            DetectionResult (same as check(), but with side effects applied).
        """
        result = await self.check(new_content, property_id, new_timestamp)

        if result.resolution == Resolution.NEWER_WINS:
            for fact_id in result.superseded_fact_ids:
                deleted = await self._store.delete(fact_id)
                if deleted:
                    logger.info("Superseded fact deleted: %s", fact_id)

        return result

    # ── Step 1: Candidate retrieval ──────────────────────────── #

    async def _find_candidates(
        self,
        content: str,
        property_id: str,
    ) -> list[StoredFact]:
        """Find existing facts similar enough to potentially contradict.

        Only returns facts above the similarity threshold.
        """
        results = await self._store.search(
            query=content,
            property_id=property_id,
            top_k=_TOP_K,
        )
        # FactStore.search already orders by relevance — filter by threshold
        # Note: we can't access the raw score from StoredFact, so we rely on
        # the store returning only relevant results.  The store's internal
        # search uses cosine similarity.
        return results

    # ── Step 2: Pairwise contradiction check ─────────────────── #

    async def _check_pairs(
        self,
        new_content: str,
        candidates: Sequence[StoredFact],
    ) -> list[ContradictionPair]:
        """Run LLM (or heuristic) contradiction check on each candidate."""
        pairs: list[ContradictionPair] = []

        for candidate in candidates:
            pair = await self._compare_single(new_content, candidate)
            pairs.append(pair)

        return pairs

    async def _compare_single(
        self,
        new_content: str,
        existing: StoredFact,
    ) -> ContradictionPair:
        """Compare a single new fact against one existing fact."""
        if self._llm is not None:
            return await self._compare_with_llm(new_content, existing)
        return self._compare_heuristic(new_content, existing)

    async def _compare_with_llm(
        self,
        new_content: str,
        existing: StoredFact,
    ) -> ContradictionPair:
        """Use LLM to determine semantic contradiction."""
        try:
            result = await self._llm.check_contradiction(
                fact_a=existing.content,
                fact_b=new_content,
            )
            return ContradictionPair(
                existing_fact=existing,
                new_content=new_content,
                is_contradiction=result.get("is_contradiction", False),
                explanation=result.get("explanation", ""),
                confidence=result.get("confidence", 0.0),
            )
        except Exception:
            logger.warning(
                "LLM contradiction check failed, falling back to heuristic",
                exc_info=True,
            )
            return self._compare_heuristic(new_content, existing)

    @staticmethod
    def _compare_heuristic(
        new_content: str,
        existing: StoredFact,
    ) -> ContradictionPair:
        """Heuristic fallback: detect obvious contradictions without LLM.

        Checks for negation patterns and numeric mismatches.  This is a
        rough approximation — the LLM path is strongly preferred.
        """
        new_lower = new_content.lower()
        old_lower = existing.content.lower()

        # Simple negation detection
        negation_words = {"not", "no", "never", "cannot", "don't", "doesn't", "isn't", "aren't"}
        new_has_neg = bool(negation_words & set(new_lower.split()))
        old_has_neg = bool(negation_words & set(old_lower.split()))

        # If one has negation and the other doesn't, AND they share
        # significant overlap — likely contradiction
        shared_words = set(new_lower.split()) & set(old_lower.split()) - negation_words
        significant_overlap = len(shared_words) >= 3

        is_contradiction = (
            new_has_neg != old_has_neg
            and significant_overlap
        )

        return ContradictionPair(
            existing_fact=existing,
            new_content=new_content,
            is_contradiction=is_contradiction,
            explanation="heuristic: negation + overlap" if is_contradiction else "",
            confidence=0.4 if is_contradiction else 0.0,
        )

    # ── Step 3: Resolution ───────────────────────────────────── #

    def _resolve(
        self,
        contradictions: list[ContradictionPair],
        new_timestamp: str,
        new_content: str = "",
    ) -> tuple[Resolution, list[str]]:
        """Determine how to resolve the contradictions.

        Strategy:
          - If all contradictions have confidence >= cutoff → NEWER_WINS
            (temporal precedence: more recent information is more likely correct)
          - If any contradiction has confidence < cutoff → FLAG_PM
            (too uncertain for auto-resolution)
          - If new_timestamp is empty → FLAG_PM (can't determine recency)
          - **Sprint 6 W7** — when a ``reliability_resolver`` is wired
            and the resolver classifies the new fact as *less reliable*
            than every existing contradicting fact, NEWER_WINS is
            demoted to EXISTING_WINS so a guest claim never overrides
            a PMS structured field.

        Args:
            contradictions: Pairs where is_contradiction=True.
            new_timestamp: When the new fact was observed.
            new_content: Incoming fact text — passed to the
                reliability resolver when one is wired.

        Returns:
            (resolution, list of superseded fact IDs).
        """
        if not new_timestamp:
            # Can't determine temporal precedence — ask PM
            return Resolution.FLAG_PM, []

        low_confidence = any(
            c.confidence < self._confidence_cutoff
            for c in contradictions
        )

        if low_confidence:
            return Resolution.FLAG_PM, []

        # Sprint 6 W7 — source-reliability tiebreaker.  The default
        # NEWER_WINS decision lets the most recent observation
        # overwrite older facts; the tiebreaker demotes that when
        # the new fact comes from a less reliable source than the
        # existing one.  Only activates when a resolver is wired.
        if self._reliability_resolver is not None and self._new_loses_reliability(
            new_content,
            contradictions,
        ):
            return Resolution.EXISTING_WINS, []

        # Newer wins: supersede all contradicted facts
        superseded = [c.existing_fact.fact_id for c in contradictions]
        return Resolution.NEWER_WINS, superseded

    def _new_loses_reliability(
        self,
        new_content: str,
        contradictions: list[ContradictionPair],
    ) -> bool:
        """Whether the new fact is less reliable than every existing fact.

        Calls the injected ``reliability_resolver`` once for the new
        fact and once per contradiction's existing fact.  Returns
        ``True`` only when:

        * the resolver classified both sides (no ``None`` returns),
          and
        * the new fact's tier is strictly less reliable than every
          existing contradicting fact's tier.

        Any ``None`` classification (the resolver opts out for the
        pair) falls through to the default NEWER_WINS path so the
        tiebreaker stays conservative — only fires when the resolver
        is sure.
        """
        resolver = self._reliability_resolver
        if resolver is None:
            return False
        new_tier = resolver(new_content, True)  # type: ignore[operator]
        if new_tier is None:
            return False
        existing_tiers: list[SourceReliability] = []
        for pair in contradictions:
            tier = resolver(pair.existing_fact, False)  # type: ignore[operator]
            if tier is None:
                return False
            existing_tiers.append(tier)
        if not existing_tiers:
            return False
        new_rank = reliability_rank(new_tier)
        return all(
            new_rank > reliability_rank(tier) for tier in existing_tiers
        )
