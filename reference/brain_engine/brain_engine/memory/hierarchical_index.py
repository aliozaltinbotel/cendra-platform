"""Pre-routing layer that picks which memory tiers to query.

The cascade router (ADR-0020) walks every relevant tier in cost order;
this module is the *planner* that decides which tiers are relevant in
the first place.  A naive engine fans out to all six tiers on every
turn — at V1 RPS that wastes capacity on queries that demonstrably
return nothing useful (a greeting hitting the Knowledge Graph, a
guest-history query hitting Working memory).

Reference: ``brain_engine_advisory.md`` §7.1.

The selection rules are deliberate and easy to audit:

* L1 instinct (chit-chat, greetings) → working + procedural.
* L2 reflex → working + episodic + procedural.
* L3 experience → all tiers except KG.
* L4 deliberative → all tiers, including KG.
* Intent overrides bump in domain-specific tiers (e.g. a complaint
  always pulls episodic so the agent can cite the prior incident).

A ``force_full=True`` flag bypasses the planner — used when a
caller suspects mis-prediction and wants the full fan-out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class MemoryTier(str, Enum):
    """The six memory tiers from ADR-0003."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    GUEST_HISTORY = "guest_history"
    KNOWLEDGE_GRAPH = "knowledge_graph"


class CognitiveLevel(str, Enum):
    """Mirrors ``brain_engine.reasoning.complexity_router``.

    Defined locally to keep this module import-independent of the
    routing layer (avoids a cycle when the router imports the index).
    """

    L1_INSTINCT = "L1"
    L2_REFLEX = "L2"
    L3_EXPERIENCE = "L3"
    L4_DELIBERATIVE = "L4"


class Intent(str, Enum):
    """Coarse-grained intent classes the router emits."""

    GREETING = "greeting"
    SMALL_TALK = "small_talk"
    BOOKING_QUERY = "booking_query"
    COMPLAINT = "complaint"
    GUEST_HISTORY_QUERY = "guest_history_query"
    PRICE_NEGOTIATION = "price_negotiation"
    OPS_REQUEST = "ops_request"
    UNKNOWN = "unknown"


# ── Selection rules ────────────────────────────────────────────────

_LEVEL_BASELINE: Final[dict[CognitiveLevel, frozenset[MemoryTier]]] = {
    CognitiveLevel.L1_INSTINCT: frozenset(
        {MemoryTier.WORKING, MemoryTier.PROCEDURAL},
    ),
    CognitiveLevel.L2_REFLEX: frozenset(
        {
            MemoryTier.WORKING,
            MemoryTier.EPISODIC,
            MemoryTier.PROCEDURAL,
        },
    ),
    CognitiveLevel.L3_EXPERIENCE: frozenset(
        {
            MemoryTier.WORKING,
            MemoryTier.EPISODIC,
            MemoryTier.SEMANTIC,
            MemoryTier.PROCEDURAL,
            MemoryTier.GUEST_HISTORY,
        },
    ),
    CognitiveLevel.L4_DELIBERATIVE: frozenset(MemoryTier),
}

_INTENT_BUMPS: Final[dict[Intent, frozenset[MemoryTier]]] = {
    Intent.COMPLAINT: frozenset(
        {MemoryTier.EPISODIC, MemoryTier.GUEST_HISTORY},
    ),
    Intent.GUEST_HISTORY_QUERY: frozenset(
        {MemoryTier.GUEST_HISTORY, MemoryTier.EPISODIC},
    ),
    Intent.PRICE_NEGOTIATION: frozenset(
        {MemoryTier.SEMANTIC, MemoryTier.GUEST_HISTORY},
    ),
    Intent.OPS_REQUEST: frozenset(
        {MemoryTier.PROCEDURAL, MemoryTier.SEMANTIC},
    ),
}


@dataclass(frozen=True, slots=True)
class IndexDecision:
    """The planner's selection plus the reason for observability."""

    tiers: frozenset[MemoryTier]
    reason: str

    def includes(self, tier: MemoryTier) -> bool:
        return tier in self.tiers


class HierarchicalIndex:
    """Tier selector — pure function, no I/O.

    Determinism is a contract: same ``(intent, level, force_full)``
    must produce identical ``IndexDecision`` so observability can
    diff plans across versions.
    """

    def select(
        self,
        *,
        intent: Intent,
        level: CognitiveLevel,
        force_full: bool = False,
    ) -> IndexDecision:
        if force_full:
            return IndexDecision(
                tiers=frozenset(MemoryTier),
                reason="force_full",
            )
        baseline = _LEVEL_BASELINE[level]
        bump = _INTENT_BUMPS.get(intent, frozenset())
        return IndexDecision(
            tiers=baseline | bump,
            reason=self._explain(level, intent, bump),
        )

    @staticmethod
    def _explain(
        level: CognitiveLevel,
        intent: Intent,
        bump: frozenset[MemoryTier],
    ) -> str:
        if not bump:
            return f"baseline:{level.value}"
        names = sorted(t.value for t in bump)
        return (
            f"baseline:{level.value}+intent:{intent.value}"
            f"->bump:{','.join(names)}"
        )
