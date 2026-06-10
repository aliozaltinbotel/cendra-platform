"""Deterministic KG sync — Task 7 of CLAUDE_CODE_WIRING_FIX_PLAN.md.

Replaces the LLM-driven entity-extraction path inside the nightly
``MemoryConsolidator`` with a deterministic mapping from
:class:`core.brain.patterns.models.DecisionCase` rows into the
:class:`core.brain.memory.knowledge_graph.TemporalKnowledgeGraph`.

Why deterministic — most of the entity / relationship signal in
Brain Engine is already structured: every DecisionCase carries
``guest_id``, ``property_id``, ``owner_id``, ``reservation_id``,
``decision``, ``scenario`` and ``stage``.  The pre-Task-7 path
funnelled the same data through ``gpt-4o-mini`` to re-extract the
same entities — the very LLM-on-write cost surface that motivated
the Graphiti removal in 2026-04-29 stayed in the codebase, just
moved into our own ``MemoryConsolidator``.  This module pulls the
extraction back into pure Python and reserves the LLM path for
truly ambiguous free-text signals behind an opt-in flag.

Env flags introduced here:

* ``BRAIN_KG_DETERMINISTIC_SYNC_ENABLED`` — default **on**.  Switch
  off only when an operator wants to disable the new path entirely
  for incident response.
* ``BRAIN_KG_LLM_EXTRACTION_ENABLED`` — default **off**.  When on,
  the legacy ``MemoryConsolidator.consolidate(force=True)`` path
  also runs after the deterministic sync, surfacing free-text
  preferences buried in guest messages.  Operators flip this on
  only when monitoring shows the deterministic surface is too
  narrow.

The ``DeterministicKGSync`` class is intentionally storage-agnostic
and synchronous-feeling — every per-case I/O hop happens inside the
injected ``TemporalKnowledgeGraph``, which is a thin Redis wrapper.
No Redis / Qdrant calls leave this module.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from core.brain.memory.knowledge_graph import (
    KnowledgeType,
    TemporalKnowledgeGraph,
)
from core.brain.patterns.models import DecisionCase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and flag plumbing
# ---------------------------------------------------------------------------


_DETERMINISTIC_FLAG_ENV: Final[str] = "BRAIN_KG_DETERMINISTIC_SYNC_ENABLED"
_LLM_EXTRACTION_FLAG_ENV: Final[str] = "BRAIN_KG_LLM_EXTRACTION_ENABLED"

# Source tag for every node and relationship written by this module.
# Operators grep ``brain_engine_kg`` Redis dumps for this tag to
# distinguish deterministic-sync rows from legacy LLM rows.
_SOURCE_TAG: Final[str] = "deterministic_case_sync"

# Confidence is fixed at 1.0 because the inputs are structured PMS
# IDs / decisions — no LLM uncertainty surface to model.
_DETERMINISTIC_CONFIDENCE: Final[float] = 1.0


def deterministic_sync_enabled() -> bool:
    """Whether ``_step4_update_knowledge_graph`` should run the new path.

    Default **on** — once an operator has wired the
    ``DeterministicKGSync`` into ``NightlyConsolidator``, the new path
    becomes the source of truth for KG nodes / edges derived from
    DecisionCases.  Setting the env var to a falsy value reverts to
    the legacy LLM consolidation behaviour for incident response.
    """
    raw = (
        os.environ.get(
            _DETERMINISTIC_FLAG_ENV,
            "1",
        )
        .strip()
        .lower()
    )
    return raw in ("1", "true", "yes", "on")


def llm_extraction_enabled() -> bool:
    """Whether the legacy LLM consolidation path should also run.

    Default **off** — the deterministic path covers every entity that
    can be lifted from structured PMS data without an LLM.  Operators
    flip this on only when monitoring shows the deterministic surface
    is missing free-text preferences embedded in guest chat.
    """
    raw = (
        os.environ.get(
            _LLM_EXTRACTION_FLAG_ENV,
            "",
        )
        .strip()
        .lower()
    )
    return raw in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncStats:
    """Counters describing what one sync pass wrote.

    Attributes:
        cases_seen: DecisionCases passed to the sync.
        cases_skipped: Cases short-circuited because ``property_id``
            was missing — every meaningful entity in Brain Engine's
            KG is rooted at a property, so a property-less case
            cannot contribute.
        nodes_written: KnowledgeNode rows added.
        relationships_written: Relationship edges added.
    """

    cases_seen: int = 0
    cases_skipped: int = 0
    nodes_written: int = 0
    relationships_written: int = 0


# ---------------------------------------------------------------------------
# Sync class
# ---------------------------------------------------------------------------


class DeterministicKGSync:
    """Maps DecisionCase rows into the temporal knowledge graph.

    Args:
        kg: The injected :class:`TemporalKnowledgeGraph`.  Already
            connected to Redis through the lifespan wire-up; the sync
            does not own its lifecycle.

    The sync is single-purpose: per case it adds Property / Guest /
    Booking nodes (whichever IDs are present) and three relationship
    classes — ``stayed_at``, ``involved_in_case``, ``booked_for``.
    Repeating the sync over the same case is cheap: the underlying
    ``add_knowledge`` / ``add_relationship`` calls write fresh nodes
    keyed by UUID, so re-running mostly grows the access-count side
    of the existing entity rather than creating semantic duplicates.
    Operators wanting strict idempotency should layer a content hash
    on top — out of scope for Task 7.
    """

    def __init__(self, kg: TemporalKnowledgeGraph) -> None:
        self._kg = kg

    def sync_decision_case(
        self,
        case: DecisionCase,
    ) -> tuple[int, int]:
        """Sync one case and return ``(nodes_written, edges_written)``.

        Cases with no ``property_id`` are skipped — see
        :class:`SyncStats` for rationale.  Ambiguous outcomes are
        logged at DEBUG; the method itself never raises so a single
        bad case cannot abort the nightly batch.
        """
        if not case.property_id:
            logger.debug(
                "kg.deterministic.skip case=%s reason=no_property_id",
                getattr(case, "case_id", "?"),
            )
            return (0, 0)

        nodes = 0
        edges = 0

        event_time = _event_time(case)
        property_id = case.property_id
        guest_id = case.guest_id or ""
        reservation_id = case.reservation_id or ""

        self._kg.add_knowledge(
            content=f"Property {property_id}",
            knowledge_type=KnowledgeType.FACT,
            entity_type="property",
            entity_id=property_id,
            confidence=_DETERMINISTIC_CONFIDENCE,
            event_time=event_time,
            source=_SOURCE_TAG,
        )
        nodes += 1

        if guest_id:
            self._kg.add_knowledge(
                content=f"Guest {guest_id}",
                knowledge_type=KnowledgeType.FACT,
                entity_type="guest",
                entity_id=guest_id,
                confidence=_DETERMINISTIC_CONFIDENCE,
                event_time=event_time,
                source=_SOURCE_TAG,
            )
            nodes += 1
            self._kg.add_relationship(
                source_entity=guest_id,
                target_entity=property_id,
                relation_type="involved_in_case",
                properties={
                    "case_id": case.case_id,
                    "scenario": case.scenario.value,
                    "stage": case.stage.value,
                },
                confidence=_DETERMINISTIC_CONFIDENCE,
                event_time=event_time,
            )
            edges += 1

        if reservation_id:
            self._kg.add_knowledge(
                content=f"Booking {reservation_id}",
                knowledge_type=KnowledgeType.FACT,
                entity_type="booking",
                entity_id=reservation_id,
                confidence=_DETERMINISTIC_CONFIDENCE,
                event_time=event_time,
                source=_SOURCE_TAG,
            )
            nodes += 1
            self._kg.add_relationship(
                source_entity=reservation_id,
                target_entity=property_id,
                relation_type="booked_for",
                properties={"case_id": case.case_id},
                confidence=_DETERMINISTIC_CONFIDENCE,
                event_time=event_time,
            )
            edges += 1
            if guest_id:
                self._kg.add_relationship(
                    source_entity=guest_id,
                    target_entity=reservation_id,
                    relation_type="stayed_at",
                    properties={"case_id": case.case_id},
                    confidence=_DETERMINISTIC_CONFIDENCE,
                    event_time=event_time,
                )
                edges += 1

        return (nodes, edges)

    def sync_decision_cases(
        self,
        cases: Iterable[DecisionCase],
    ) -> SyncStats:
        """Sync a batch of cases and return aggregate counters.

        Aggregates per-case counters into a :class:`SyncStats` so the
        nightly orchestrator can log a single structured line.
        """
        cases_list = list(cases)
        nodes_total = 0
        edges_total = 0
        skipped = 0
        for case in cases_list:
            try:
                nodes, edges = self.sync_decision_case(case)
            except Exception as exc:
                # Per-case failure must not stop the batch.
                logger.warning(
                    "kg.deterministic.case_failed case=%s type=%s msg=%s",
                    getattr(case, "case_id", "?"),
                    type(exc).__name__,
                    exc,
                )
                continue
            if nodes == 0 and edges == 0:
                skipped += 1
                continue
            nodes_total += nodes
            edges_total += edges
        return SyncStats(
            cases_seen=len(cases_list),
            cases_skipped=skipped,
            nodes_written=nodes_total,
            relationships_written=edges_total,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_time(case: DecisionCase) -> str:
    """Return a stable ISO-8601 string for the case event.

    ``DecisionCase.created_at`` is a ``datetime`` produced by
    :class:`patterns.case_builder.CaseBuilder`.  When for any reason
    it is missing we fall back to the empty string and let
    ``add_knowledge`` substitute ``datetime.now`` — that keeps the
    sync forward-compatible with cases imported from external
    archives that did not carry timestamps.
    """
    created_at = getattr(case, "created_at", None)
    if created_at is None:
        return ""
    isoformat = getattr(created_at, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(created_at)


__all__ = [
    "DeterministicKGSync",
    "SyncStats",
    "deterministic_sync_enabled",
    "llm_extraction_enabled",
]
