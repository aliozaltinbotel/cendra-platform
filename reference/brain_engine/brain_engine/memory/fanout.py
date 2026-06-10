"""Shared memory fan-out for every DecisionCase write path.

Mümin 2026-05-13 (PR #F): memory is Brain Engine's signature
feature.  Before this module, only the bootstrap path (PR #E)
fanned out persisted :class:`DecisionCase` rows to the three
high-level tiers (Episodic / Semantic / KnowledgeGraph).  Every
other write path — live conversation extraction, regenerate
flow, nightly consolidator — left the tiers silent, so
``/memory/timeline``, vector recall, and the KG entity panel
only saw bootstrap data.

This module centralises the fan-out so a single
:class:`MemoryFanOut` instance is constructed at lifespan and
injected wherever DecisionCases land.  Every write path emits a
``record_case`` call after a successful ``case_store.store``;
the fan-out propagates to all three tiers via best-effort writes
with structured logging on failure.

Architectural surface
---------------------

* :class:`MemoryFanOut` — one ``record_case`` entrypoint, three
  tier-specific helpers behind it.
* :class:`MemoryFanOutProtocol` — narrow Protocol the call sites
  depend on; lets tests bind a recording stub.
* :class:`NullMemoryFanOut` — silent no-op for environments where
  none of the high-level tiers are wired.

Honest scope
------------

* Writes are best-effort.  Persistence correctness lives on the
  Postgres case store; the fan-out feeds presentation surfaces
  (timeline, semantic recall, KG entity panel).
* The fan-out keys every entry by ``case.case_id`` so a
  re-extract of the same case overwrites the stale row instead
  of duplicating it.
* Every helper traps ``Exception`` and logs the failure with the
  ``source`` tag so the operator can spot tier-specific drift.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from enum import StrEnum
from typing import Final, Protocol

import structlog

from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.knowledge_graph import (
    KnowledgeType,
    TemporalKnowledgeGraph,
)
from brain_engine.memory.semantic_memory import SemanticMemory
from brain_engine.patterns.models import DecisionCase

__all__ = [
    "ALL_FANOUT_TIERS",
    "FanOutTier",
    "MemoryFanOut",
    "MemoryFanOutProtocol",
    "NullMemoryFanOut",
    "resolve_fanout_tiers",
]


_KG_CONTENT_CAP: Final[int] = 200


class FanOutTier(StrEnum):
    """The three memory tiers :class:`MemoryFanOut` writes into.

    Sprint 6 W2 — adds explicit names so the FL-04 routing slugs
    (13 :class:`brain_engine.analysis.models.MemoryTier` entries) can
    be mapped to concrete fan-out destinations without a magic
    string contract.
    """

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    KG = "kg"


ALL_FANOUT_TIERS: Final[frozenset[FanOutTier]] = frozenset(FanOutTier)


# Sprint 6 W2 — mapping from the FL-04 ``MemoryTier`` slug (the
# foundation catalog's ``memory_types`` labels normalised by
# :class:`brain_engine.analysis.models.MemoryTier`) to the concrete
# :class:`FanOutTier` set the fan-out should write to.
#
# Domain reasoning per slug:
#
# * Stable long-term facts (property knowledge, PM preference,
#   owner preference, vendor data, channel-specific behaviour) →
#   :attr:`FanOutTier.SEMANTIC`.
# * Time-bound interactions (reservation context, task / operational
#   workflow) → :attr:`FanOutTier.EPISODIC`.
# * Graph-style relationships (vendor links, guest-risk
#   relationships, task / SOP / missing-info structures) →
#   :attr:`FanOutTier.KG`.
# * Guest profile bridges the long-term + episodic split — needs
#   both tiers.
#
# Slugs the foundation may emit but the mapping does not yet cover
# (future expansions) fall through to the empty set; the resolver
# uses an "all tiers" safety net so an unrecognised slug never
# silences memory writes.
_ROUTE_TO_TIERS: Final[dict[str, frozenset[FanOutTier]]] = {
    "property_knowledge": frozenset({FanOutTier.SEMANTIC}),
    "pm_preference_memory": frozenset({FanOutTier.SEMANTIC}),
    "pm_behavior_memory": frozenset({FanOutTier.SEMANTIC}),
    "reservation_context_memory": frozenset({FanOutTier.EPISODIC}),
    "guest_profile_memory": frozenset(
        {FanOutTier.SEMANTIC, FanOutTier.EPISODIC},
    ),
    "guest_risk_memory": frozenset(
        {FanOutTier.SEMANTIC, FanOutTier.KG},
    ),
    "owner_preference_memory": frozenset({FanOutTier.SEMANTIC}),
    "vendor_memory": frozenset(
        {FanOutTier.SEMANTIC, FanOutTier.KG},
    ),
    "task_workflow_memory": frozenset(
        {FanOutTier.EPISODIC, FanOutTier.KG},
    ),
    "operational_workflow_memory": frozenset(
        {FanOutTier.EPISODIC, FanOutTier.KG},
    ),
    "channel_specific_behavior_memory": frozenset(
        {FanOutTier.SEMANTIC},
    ),
    "missing_info_registry": frozenset({FanOutTier.KG}),
    "sop_candidate_memory": frozenset(
        {FanOutTier.SEMANTIC, FanOutTier.KG},
    ),
}


def resolve_fanout_tiers(
    routes: Iterable[str],
) -> frozenset[FanOutTier]:
    """Translate FL-04 route slugs into the concrete fan-out tier set.

    Returns the union of mapped tiers across ``routes``.  Empty /
    unknown-only input collapses to the *all-tiers* set so the
    fan-out never silently drops a write when the catalog drifts
    or the caller hands in nothing.  The safety net mirrors the
    pre-W2 behaviour where every case landed on every tier.

    Args:
        routes: Any iterable of slug strings produced by FL-04
            (e.g. ``("property_knowledge",
            "guest_profile_memory")``).  Empty / ``None``-like
            falls through to the all-tiers safety net.

    Returns:
        :class:`frozenset` of :class:`FanOutTier` values.  Always
        non-empty so the fan-out caller can iterate without
        guarding for ``None``.
    """
    materialised = tuple(routes or ())
    if not materialised:
        return ALL_FANOUT_TIERS
    tiers: set[FanOutTier] = set()
    for route in materialised:
        mapped = _ROUTE_TO_TIERS.get(route)
        if mapped is None:
            continue
        tiers.update(mapped)
    if not tiers:
        # Every slug was unrecognised — safety net keeps the
        # caller on the legacy "write everywhere" path so we
        # never lose memory writes to a stale slug.
        return ALL_FANOUT_TIERS
    return frozenset(tiers)


logger = structlog.get_logger(__name__)


class MemoryFanOutProtocol(Protocol):
    """Narrow interface every write path depends on.

    The optional ``routes`` parameter (Sprint 6 W2) lets the
    caller restrict which fan-out tiers receive the case based on
    the FL-04 ``memory_routes`` produced by the orchestrator.
    Callers that do not pass ``routes`` keep the pre-W2 behaviour
    where the case lands on every wired tier.
    """

    async def record_case(
        self,
        case: DecisionCase,
        *,
        source: str = "live",
        routes: Iterable[str] = (),
    ) -> None:
        """Fan one persisted case out to wired memory tiers."""
        ...


class MemoryFanOut:
    """Concrete fan-out across Episodic + Semantic + Knowledge Graph.

    Args:
        episodic: Optional :class:`EpisodicMemory` instance.  When
            absent the episodic write is a no-op.
        semantic: Optional :class:`SemanticMemory` instance.  When
            absent the vector index is skipped.
        knowledge_graph: Optional :class:`TemporalKnowledgeGraph`.
            When absent the KG node creation is skipped.

    All three are optional so a single :class:`MemoryFanOut` can
    be constructed even on a minimal deployment; the call sites
    do not have to gate on backend availability.
    """

    def __init__(
        self,
        *,
        episodic: EpisodicMemory | None = None,
        semantic: SemanticMemory | None = None,
        knowledge_graph: TemporalKnowledgeGraph | None = None,
    ) -> None:
        self._episodic = episodic
        self._semantic = semantic
        self._kg = knowledge_graph
        self._log = logger.bind(component="memory_fanout")

    async def record_case(
        self,
        case: DecisionCase,
        *,
        source: str = "live",
        routes: Iterable[str] = (),
    ) -> None:
        """Fan one persisted case out to wired tiers.

        ``source`` tags the origin (``bootstrap``, ``live``,
        ``regenerate``, ``nightly_consolidator``, …) so operators
        can filter the resulting timeline / KG entries by where
        the case came from.

        ``routes`` (Sprint 6 W2) is the optional FL-04 routing
        decision — a tuple of :class:`brain_engine.analysis.models.
        MemoryTier` slug strings produced by the orchestrator.
        Empty (the default) preserves the pre-W2 behaviour where
        the case lands on every wired tier; non-empty restricts
        the fan-out to the tiers mapped by
        :func:`resolve_fanout_tiers`.  Unrecognised slugs fall
        through to the all-tiers safety net so a stale slug never
        silences memory writes.
        """
        tiers = resolve_fanout_tiers(routes)
        if FanOutTier.EPISODIC in tiers:
            await self._record_episodic(case=case, source=source)
        if FanOutTier.SEMANTIC in tiers:
            await self._record_semantic(case=case, source=source)
        if FanOutTier.KG in tiers:
            await self._record_kg(case=case, source=source)

    async def _record_episodic(
        self,
        *,
        case: DecisionCase,
        source: str,
    ) -> None:
        if self._episodic is None:
            return
        anchor = getattr(case, "decision_at", None) or case.created_at
        try:
            await self._episodic.add_episode(
                event=case.scenario.value,
                content=case.message_text or "",
                metadata={
                    "source": source,
                    "property_id": case.property_id,
                    "case_id": case.case_id,
                    "conversation_id": (
                        case.reservation_id or case.guest_id or ""
                    ),
                    "decision_type": case.decision.action_type.value,
                    "stage": case.stage.value,
                    "scenario": case.scenario.value,
                    "decision_at": (
                        anchor.isoformat() if anchor is not None else ""
                    ),
                    "owner_id": case.owner_id,
                    "is_learnable": case.is_learnable,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.warning(
                "fanout.episodic_failed",
                source=source,
                case_id=case.case_id,
                error=str(exc),
            )

    async def _record_semantic(
        self,
        *,
        case: DecisionCase,
        source: str,
    ) -> None:
        if self._semantic is None:
            return
        message = (case.message_text or "").strip()
        if not message:
            return
        anchor = getattr(case, "decision_at", None) or case.created_at
        try:
            await self._semantic.store(
                text=message,
                metadata={
                    "source": source,
                    "property_id": case.property_id,
                    # Guest/conversation scope key — mirrors the episodic
                    # record so memory recall can isolate one guest's facts
                    # from another's on the same property (a missing key
                    # here let a guest's WhatsApp number surface in a
                    # different guest's reply).
                    "conversation_id": (
                        case.reservation_id or case.guest_id or ""
                    ),
                    "case_id": case.case_id,
                    "scenario": case.scenario.value,
                    "decision_type": case.decision.action_type.value,
                    "stage": case.stage.value,
                    "decision_at": (
                        anchor.isoformat() if anchor is not None else ""
                    ),
                },
                record_id=case.case_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.warning(
                "fanout.semantic_failed",
                source=source,
                case_id=case.case_id,
                error=str(exc),
            )

    async def _record_kg(
        self,
        *,
        case: DecisionCase,
        source: str,
    ) -> None:
        if self._kg is None:
            return
        anchor = getattr(case, "decision_at", None) or case.created_at
        message = (case.message_text or "").strip()
        try:
            await self._kg.add_knowledge(
                content=(
                    f"[{case.scenario.value} → "
                    f"{case.decision.action_type.value}] "
                    f"{message[:_KG_CONTENT_CAP]}"
                ),
                knowledge_type=KnowledgeType.FACT,
                entity_type="property",
                entity_id=case.property_id,
                confidence=1.0,
                event_time=(
                    anchor.isoformat() if anchor is not None else ""
                ),
                keywords=[
                    case.scenario.value,
                    case.decision.action_type.value,
                    case.stage.value,
                ],
                tags=[source, "decision_case"],
                source=source,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.warning(
                "fanout.kg_failed",
                source=source,
                case_id=case.case_id,
                error=str(exc),
            )


class NullMemoryFanOut:
    """Silent no-op used when no high-level tier is wired.

    Accepts the W2 ``routes`` parameter for Protocol compatibility
    but ignores it — there is nothing to route to.
    """

    async def record_case(
        self,
        case: DecisionCase,
        *,
        source: str = "live",
        routes: Iterable[str] = (),
    ) -> None:
        del case, source, routes
        return None
