"""Guest-scoped memory recall for the guest conversation.

The conversation pipeline historically retrieved long-term memory with
a single semantic search whose metadata filter required *both*
``customer_id`` and ``property_id``.  The :class:`MemoryFanOut` write
path, however, tags records with ``property_id`` only (a
``DecisionCase`` carries no ``customer_id``), so that filter could never
match a fact written from a live conversation — recall silently
returned nothing.

This module assembles the guest-facing recall directly from the
scoped memory tiers, which is both correct and richer than the legacy
single search:

* **Temporal knowledge graph** — ``get_facts`` / ``get_beliefs`` for the
  property entity.  Each :class:`KnowledgeNode` carries ``event_time``,
  so a recalled fact can be dated ("From an earlier interaction
  (2026-06-07): …").
* **Semantic memory** — a relevance search scoped to ``property_id``
  *and*, when a ``conversation_id`` is known, to that conversation
  thread.  Property scope alone isolates one listing from another but
  still mixed every guest of the *same* listing — one guest's shared
  fact (e.g. a WhatsApp number) then surfaced in another guest's
  reply.  Adding the conversation scope isolates one guest's facts
  from another's.  (The knowledge-graph tier remains property-scoped;
  guest-scoping its nodes is tracked as follow-up work.)

It deliberately does **not** route through
:meth:`CognitiveController.remember`: that bundles an *unscoped*
semantic search and an *unscoped* ``episodic.get_recent`` (workspace
wide, not property scoped), either of which could surface a different
listing's data into a guest reply.  Reading the property-scoped tier
methods directly keeps the isolation guarantee intact.

Every retrieval is wrapped in a timeout and fails open to an empty
list — memory recall must never slow down or break the reply.  All
rendered lines pass through :func:`redact_sensitive_for_status`, the
same status-aware filter that protects the property-knowledge block,
so a WiFi password or door code shared earlier never leaks before a
booking is confirmed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Final

from brain_engine.conversation.prompt_redaction import (
    redact_sensitive_for_status,
)
from brain_engine.memory.knowledge_graph import KnowledgeType

logger = logging.getLogger(__name__)

# Hard ceiling on the whole recall so the memory backends can never
# stall the guest reply (the sandbox has a 504/timeout history).  The
# knowledge-graph read is the cost driver — ``get_entity_knowledge``
# does one ``smembers`` plus a sequential ``get_node`` per node, so a
# heavily-bootstrapped property is N round-trips against Azure Redis.
# Tunable via ``BRAIN_RECALL_TIMEOUT_S`` without a redeploy.
_RECALL_TIMEOUT_ENV: Final[str] = "BRAIN_RECALL_TIMEOUT_S"
_DEFAULT_RECALL_TIMEOUT_S: Final[float] = 6.0

# Per-tier caps and the overall fact budget injected into the prompt.
_KG_FACTS_CAP: Final[int] = 6
_KG_BELIEFS_CAP: Final[int] = 3
_SEMANTIC_CAP: Final[int] = 6
_MAX_FACTS: Final[int] = 12

# Confidence floors — a low-confidence belief is noise, not signal.
_MIN_FACT_CONFIDENCE: Final[float] = 0.5
_MIN_BELIEF_CONFIDENCE: Final[float] = 0.6

# Relevance floor for the semantic tier (mirrors the value the
# cognitive controller uses for its own relevance strategy).
_SEMANTIC_SCORE_THRESHOLD: Final[float] = 0.3


def _resolve_timeout(explicit: float | None) -> float:
    """Pick the recall timeout: an explicit arg wins, else the env
    override, else :data:`_DEFAULT_RECALL_TIMEOUT_S`."""
    if explicit is not None:
        return explicit
    raw = os.environ.get(_RECALL_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_RECALL_TIMEOUT_S
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_RECALL_TIMEOUT_S


def _date_prefix(event_time: Any) -> str:
    """Return the ``YYYY-MM-DD`` slice of an ISO ``event_time``.

    ``KnowledgeNode.event_time`` is an ISO-8601 string (or empty).
    Anything unparseable yields an empty prefix so the caller falls
    back to an undated phrasing.
    """
    text = str(event_time or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


# Provenance of a recalled fact, derived from the write-path ``source``
# tag (``MemoryFanOut.record_case(source=...)``).  Lets the agent tell
# the guest WHERE a value came from — a number the guest typed in chat
# vs one carried on the booking record harvested from the PMS — so two
# different values for the same field can be presented with their
# origin instead of silently merged (tester 2026-06-10: +90 was typed
# in chat, +39 came from the reservation).  ``source`` is a small
# code-internal set (CaseSource), not a free-text taxonomy.
_LIVE_SOURCES: Final[frozenset[str]] = frozenset({"live", "regenerate"})
_BOOKING_SOURCES: Final[frozenset[str]] = frozenset(
    {"bootstrap", "harvest", "historical"},
)


def _provenance(source: Any) -> str:
    """Map a write-path ``source`` to a guest-facing origin phrase.

    Returns ``""`` for unknown / empty sources so the caller keeps the
    neutral "earlier interaction" phrasing.
    """
    normalised = str(source or "").strip().lower()
    if normalised in _LIVE_SOURCES:
        return "your messages with us"
    if normalised in _BOOKING_SOURCES:
        return "your booking records"
    return ""


def _render_recall_line(content: str, date: str, provenance: str) -> str:
    """Compose one recall line from content, date, and provenance.

    With no provenance the wording is byte-for-byte the legacy phrasing
    so existing prompt expectations are unchanged; a known provenance
    names the origin instead.
    """
    if not provenance:
        if date:
            return f"From an earlier interaction ({date}): {content}"
        return f"Known from an earlier interaction: {content}"
    if date:
        return f"From {provenance} ({date}): {content}"
    return f"From {provenance}: {content}"


def format_fact_node(node: Any) -> str | None:
    """Render a knowledge-graph FACT node as one prompt line.

    Returns ``None`` when the node has no content or its confidence
    is below :data:`_MIN_FACT_CONFIDENCE`.
    """
    content = str(getattr(node, "content", "") or "").strip()
    if not content:
        return None
    if float(getattr(node, "confidence", 1.0) or 0.0) < _MIN_FACT_CONFIDENCE:
        return None
    date = _date_prefix(getattr(node, "event_time", ""))
    provenance = _provenance(getattr(node, "source", ""))
    return _render_recall_line(content, date, provenance)


def format_belief_node(node: Any) -> str | None:
    """Render a knowledge-graph BELIEF node as one prompt line.

    Beliefs are inferred, so they are phrased tentatively and held to
    a higher confidence floor than facts.
    """
    content = str(getattr(node, "content", "") or "").strip()
    if not content:
        return None
    if float(getattr(node, "confidence", 1.0) or 0.0) < _MIN_BELIEF_CONFIDENCE:
        return None
    return f"Likely (from past behaviour): {content}"


def format_semantic_record(record: Any) -> str | None:
    """Render a semantic-memory record as one prompt line.

    Prefixes the text with its origin when the record's metadata
    carries a recognised ``source`` so the agent can tell the guest
    where the value came from; otherwise returns the bare text
    (legacy behaviour).
    """
    text = str(getattr(record, "text", "") or "").strip()
    if not text:
        return None
    metadata = getattr(record, "metadata", None) or {}
    provenance = _provenance(metadata.get("source", ""))
    if provenance:
        return f"From {provenance}: {text}"
    return text


def _is_question(line: str) -> bool:
    """Whether a recalled line is itself a question rather than a fact.

    The fan-out persists *every* guest message — including the guest's
    own questions ("what is the WhatsApp number?") — so a relevance
    search for that very topic returns the question echoes far above the
    one declarative answer, burying it.  A line ending in a question
    mark is the guest asking, not knowledge to recall, so it is dropped.
    """
    return line.rstrip().endswith("?")


def assemble_facts(
    *,
    fact_nodes: list[Any],
    belief_nodes: list[Any],
    semantic_records: list[Any],
    status: str,
    max_facts: int = _MAX_FACTS,
) -> list[str]:
    """Flatten the scoped tier results into prompt-ready fact lines.

    Deterministic order — dated KG facts first (the strongest signal),
    then beliefs, then semantic relevance.  Lines are de-duplicated on
    their normalised text, capped at ``max_facts``, and the whole block
    is run through :func:`redact_sensitive_for_status` so sensitive
    values never surface in a pre-booking conversation.
    """
    lines: list[str] = []
    seen: set[str] = set()

    def _add(line: str | None) -> None:
        if not line or _is_question(line):
            return
        key = " ".join(line.split()).lower()
        if key in seen:
            return
        seen.add(key)
        lines.append(line)

    for node in fact_nodes[:_KG_FACTS_CAP]:
        _add(format_fact_node(node))
    for node in belief_nodes[:_KG_BELIEFS_CAP]:
        _add(format_belief_node(node))
    for record in semantic_records[:_SEMANTIC_CAP]:
        _add(format_semantic_record(record))

    lines = lines[:max_facts]
    if not lines:
        return []

    redacted = redact_sensitive_for_status("\n".join(lines), status)
    return [line for line in redacted.split("\n") if line.strip()]


def _semantic_filter(property_id: str, conversation_id: str) -> dict[str, str]:
    """Build the semantic-tier metadata filter.

    Always scopes to ``property_id``.  When a ``conversation_id`` is
    known it is added as a second ``must`` condition so the search
    returns only **this** guest's facts — without it the property-only
    filter mixed every guest's records and a fact one guest shared
    (e.g. a WhatsApp number) surfaced in another guest's reply.  The
    key is omitted when empty so non-sandbox callers keep the legacy
    property-only behaviour rather than matching nothing.
    """
    metadata_filter = {"property_id": property_id}
    if conversation_id:
        metadata_filter["conversation_id"] = conversation_id
    return metadata_filter


async def _gather_scoped(
    *,
    memory_system: Any,
    property_id: str,
    conversation_id: str,
    query: str,
    status: str,
) -> list[str]:
    """Read the scoped memory tiers concurrently and assemble facts.

    Each tier read is independent: a failure in one (or a tier that is
    not wired) degrades to empty rather than aborting the others.
    """
    kg = getattr(memory_system, "knowledge_graph", None)
    semantic = getattr(memory_system, "semantic", None)

    async def _knowledge() -> tuple[list[Any], list[Any]]:
        # Single KG pass: ``get_facts`` + ``get_beliefs`` each re-scan
        # every node of the entity, so calling both doubles the Redis
        # round-trips.  One ``get_entity_knowledge`` read, split by
        # type, halves the cost on the critical path.
        return await _entity_knowledge(kg, property_id)

    async def _semantic() -> list[Any]:
        if semantic is None or not query:
            return []
        return list(
            await semantic.search(
                query=query,
                top_k=_SEMANTIC_CAP,
                score_threshold=_SEMANTIC_SCORE_THRESHOLD,
                metadata_filter=_semantic_filter(property_id, conversation_id),
            )
        )

    knowledge, semantic_records = await asyncio.gather(
        _knowledge(), _semantic(), return_exceptions=True,
    )
    fact_nodes, belief_nodes = (
        knowledge if isinstance(knowledge, tuple) else ([], [])
    )
    records = _drop_errors(semantic_records)

    logger.info(
        "memory_recall.tiers facts=%d beliefs=%d semantic=%d",
        len(fact_nodes), len(belief_nodes), len(records),
    )
    return assemble_facts(
        fact_nodes=fact_nodes,
        belief_nodes=belief_nodes,
        semantic_records=records,
        status=status,
    )


async def _entity_knowledge(
    kg: Any, property_id: str,
) -> tuple[list[Any], list[Any]]:
    """Read the property's knowledge nodes once and split into
    ``(facts, beliefs)``.

    Prefers the single ``get_entity_knowledge`` scan; falls back to the
    separate ``get_facts`` / ``get_beliefs`` accessors for minimal
    knowledge-graph implementations that lack it.
    """
    if kg is None:
        return [], []
    getter = getattr(kg, "get_entity_knowledge", None)
    if getter is not None:
        nodes = list(await getter(property_id))
        facts = [
            n for n in nodes
            if getattr(n, "knowledge_type", "") == KnowledgeType.FACT
        ]
        beliefs = [
            n for n in nodes
            if getattr(n, "knowledge_type", "") == KnowledgeType.BELIEF
        ]
        return facts, beliefs
    return (
        list(await kg.get_facts(property_id)),
        list(await kg.get_beliefs(property_id)),
    )


def _drop_errors(result: Any) -> list[Any]:
    """Normalise a ``gather`` slot: a list passes through, an
    exception (a failed/unwired tier) becomes an empty list."""
    return result if isinstance(result, list) else []


async def recall_property_scoped(
    *,
    memory_system: Any,
    property_id: str,
    query: str,
    status: str,
    conversation_id: str = "",
    timeout_s: float | None = None,
) -> list[str]:
    """Return dated, guest-scoped recall lines for the guest reply.

    Fails open to ``[]`` on timeout or any backend error so the
    conversation pipeline can never be slowed or broken by memory.
    Logs the elapsed time so a slow knowledge-graph read shows up as a
    diagnosable signal rather than a silent empty.

    Args:
        memory_system: Object exposing ``.knowledge_graph`` and
            ``.semantic`` (the live ``MemorySystem``).
        property_id: The ``property_channel_id`` scoping every read.
        query: The guest's cleaned message (drives semantic relevance).
        status: Reservation status, for status-aware PII redaction.
        conversation_id: The conversation thread id.  When supplied it
            narrows the semantic tier to this one guest's facts so
            another guest's records on the same property never surface;
            empty keeps the legacy property-only scope.
        timeout_s: Hard ceiling on the whole recall; ``None`` resolves
            to ``BRAIN_RECALL_TIMEOUT_S`` or the built-in default.

    Returns:
        Up to :data:`_MAX_FACTS` prompt-ready lines, redacted for the
        reservation status; empty when nothing relevant is recalled.
    """
    if memory_system is None or not property_id:
        return []
    budget = _resolve_timeout(timeout_s)
    started = time.monotonic()
    try:
        facts = await asyncio.wait_for(
            _gather_scoped(
                memory_system=memory_system,
                property_id=property_id,
                conversation_id=conversation_id,
                query=query,
                status=status,
            ),
            timeout=budget,
        )
        logger.info(
            "memory_recall.ok facts=%d elapsed_ms=%d budget_s=%.1f",
            len(facts), int((time.monotonic() - started) * 1000), budget,
        )
        return facts
    except Exception as exc:  # fail open (covers asyncio timeout)
        logger.warning(
            "memory_recall.failed (%s) after %dms budget_s=%.1f: %s",
            type(exc).__name__,
            int((time.monotonic() - started) * 1000),
            budget,
            exc,
        )
        return []
