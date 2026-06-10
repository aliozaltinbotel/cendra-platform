"""ConceptNet API — Commonsense reasoning via semantic relationships.

Layer 2 of the Neuro-Symbolic 4-layer validation cascade.
Uses the ConceptNet 5.7 API to check semantic relationships between concepts.

Examples in property management:
- "birthday" + "funeral home" → CONFLICT (ConceptNet: birthday RelatedTo celebration,
  funeral RelatedTo sadness → opposing sentiment)
- "early checkin" + "late checkout previous guest" → CONFLICT
- "swimming pool" + "toddler" + "no fence" → SAFETY WARNING

Latency: ~100-200ms per query (external HTTP).
Cost: Free (open API).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CONCEPTNET_API_URL = "https://api.conceptnet.io"


@dataclass(frozen=True, slots=True)
class ConceptRelation:
    """A semantic relationship between two concepts.

    Attributes:
        start: Source concept.
        end: Target concept.
        relation: Relationship type (e.g. RelatedTo, Antonym, HasContext).
        weight: Confidence weight from ConceptNet.
        surface_text: Human-readable relationship description.
    """

    start: str
    end: str
    relation: str
    weight: float
    surface_text: str = ""


@dataclass(slots=True)
class CommonsenseResult:
    """Result of a commonsense check between two concepts.

    Attributes:
        concept_a: First concept.
        concept_b: Second concept.
        is_conflict: Whether the concepts are semantically conflicting.
        confidence: Confidence in the assessment (0-1).
        relations: Relevant relations found.
        explanation: Human-readable explanation.
    """

    concept_a: str
    concept_b: str
    is_conflict: bool = False
    confidence: float = 0.0
    relations: list[ConceptRelation] | None = None
    explanation: str = ""


# Relationship types that indicate potential conflict
CONFLICT_RELATIONS: frozenset[str] = frozenset({
    "Antonym",
    "DistinctFrom",
    "NotDesires",
    "NotHasProperty",
    "NotCapableOf",
    "ObstructedBy",
})

# Relationship types that indicate compatibility
COMPATIBLE_RELATIONS: frozenset[str] = frozenset({
    "RelatedTo",
    "Synonym",
    "SimilarTo",
    "IsA",
    "PartOf",
    "HasContext",
    "UsedFor",
    "CapableOf",
    "Desires",
})

# Domain-specific concept pairs known to conflict in property management
DOMAIN_CONFLICTS: list[tuple[set[str], set[str], str]] = [
    (
        {"early checkin", "early arrival"},
        {"late checkout", "extended stay"},
        "Early checkin conflicts with previous guest's late checkout",
    ),
    (
        {"quiet", "silence", "peaceful"},
        {"party", "loud", "noise", "music"},
        "Quiet property rules conflict with party/noise activity",
    ),
    (
        {"pet-free", "no pets", "allergy"},
        {"pet", "dog", "cat", "animal"},
        "Pet-free property conflicts with pet presence",
    ),
    (
        {"non-smoking", "smoke-free"},
        {"smoking", "cigarette", "vape"},
        "Non-smoking property conflicts with smoking activity",
    ),
    (
        {"discount", "free", "complimentary"},
        {"peak season", "high demand", "holiday"},
        "Discounts during peak season may reduce revenue",
    ),
    (
        {"checked in", "arrived", "staying"},
        {"checked out", "departed", "left"},
        "Guest cannot be both checked in and checked out",
    ),
    (
        {"available", "confirmed", "ready"},
        {"unavailable", "cancelled", "declined"},
        "Resource cannot be both available and unavailable",
    ),
    (
        {"clean", "inspected", "ready"},
        {"damaged", "dirty", "needs repair"},
        "Property cannot be both clean and damaged",
    ),
]


class ConceptNetClient:
    """Client for querying ConceptNet API for commonsense relationships.

    Provides both online (HTTP API) and offline (domain-specific rules)
    commonsense checking.

    Args:
        http_client: Optional async HTTP client (aiohttp session).
        timeout: Request timeout in seconds.
        use_api: Whether to make real API calls (False = offline mode only).
    """

    def __init__(
        self,
        http_client: Any | None = None,
        timeout: float = 2.0,
        use_api: bool = True,
    ) -> None:
        self._http = http_client
        self._timeout = timeout
        self._use_api = use_api
        self._cache: dict[str, list[ConceptRelation]] = {}

    async def check_commonsense(
        self,
        concept_a: str,
        concept_b: str,
    ) -> CommonsenseResult:
        """Check if two concepts have a commonsense conflict.

        First checks domain-specific rules (instant), then queries
        ConceptNet API if enabled.

        Args:
            concept_a: First concept (e.g. "birthday").
            concept_b: Second concept (e.g. "funeral home").

        Returns:
            CommonsenseResult with conflict assessment.
        """
        result = CommonsenseResult(
            concept_a=concept_a,
            concept_b=concept_b,
        )

        # Layer 1: Domain-specific checks (instant)
        domain_check = self._check_domain_conflicts(concept_a, concept_b)
        if domain_check:
            result.is_conflict = True
            result.confidence = 0.9
            result.explanation = domain_check
            return result

        # Layer 2: ConceptNet API (100-200ms)
        if self._use_api:
            relations = await self._query_relations(concept_a, concept_b)
            result.relations = relations

            conflict_rels = [r for r in relations if r.relation in CONFLICT_RELATIONS]
            compat_rels = [r for r in relations if r.relation in COMPATIBLE_RELATIONS]

            if conflict_rels and not compat_rels:
                result.is_conflict = True
                best = max(conflict_rels, key=lambda r: r.weight)
                result.confidence = min(best.weight / 5.0, 1.0)
                result.explanation = (
                    f"ConceptNet: {concept_a} {best.relation} {concept_b} "
                    f"(weight: {best.weight:.1f})"
                )

        return result

    async def get_related_concepts(
        self,
        concept: str,
        limit: int = 10,
    ) -> list[ConceptRelation]:
        """Get related concepts for a given concept.

        Args:
            concept: The concept to look up.
            limit: Max number of relations to return.

        Returns:
            List of ConceptRelation objects.
        """
        cache_key = f"related:{concept}"
        if cache_key in self._cache:
            return self._cache[cache_key][:limit]

        relations = await self._query_concept(concept, limit)
        self._cache[cache_key] = relations
        return relations

    @staticmethod
    def _check_domain_conflicts(concept_a: str, concept_b: str) -> str:
        """Check domain-specific conflict rules (instant)."""
        a_lower = concept_a.lower()
        b_lower = concept_b.lower()

        for set_a, set_b, explanation in DOMAIN_CONFLICTS:
            a_match = any(term in a_lower for term in set_a)
            b_match = any(term in b_lower for term in set_b)
            if a_match and b_match:
                return explanation

            # Check reverse
            a_match_rev = any(term in a_lower for term in set_b)
            b_match_rev = any(term in b_lower for term in set_a)
            if a_match_rev and b_match_rev:
                return explanation

        return ""

    async def _query_relations(
        self,
        concept_a: str,
        concept_b: str,
    ) -> list[ConceptRelation]:
        """Query ConceptNet for relations between two concepts."""
        cache_key = f"{concept_a}:{concept_b}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self._http:
            return []

        slug_a = concept_a.lower().replace(" ", "_")
        slug_b = concept_b.lower().replace(" ", "_")
        url = f"{CONCEPTNET_API_URL}/query?start=/c/en/{slug_a}&end=/c/en/{slug_b}&limit=10"

        try:
            async with self._http.get(url, timeout=self._timeout) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                relations = self._parse_edges(data.get("edges", []))
                self._cache[cache_key] = relations
                return relations
        except asyncio.TimeoutError:
            logger.warning("ConceptNet API timeout for %s ↔ %s", concept_a, concept_b)
            return []
        except Exception:
            logger.exception("ConceptNet API error")
            return []

    async def _query_concept(
        self,
        concept: str,
        limit: int = 10,
    ) -> list[ConceptRelation]:
        """Query ConceptNet for all relations of a concept."""
        if not self._http:
            return []

        slug = concept.lower().replace(" ", "_")
        url = f"{CONCEPTNET_API_URL}/c/en/{slug}?limit={limit}"

        try:
            async with self._http.get(url, timeout=self._timeout) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return self._parse_edges(data.get("edges", []))
        except asyncio.TimeoutError:
            logger.warning("ConceptNet API timeout for %s", concept)
            return []
        except Exception:
            logger.exception("ConceptNet API error")
            return []

    @staticmethod
    def _parse_edges(edges: list[dict[str, Any]]) -> list[ConceptRelation]:
        """Parse ConceptNet API edges into ConceptRelation objects."""
        relations: list[ConceptRelation] = []
        for edge in edges:
            rel = edge.get("rel", {}).get("label", "")
            start = edge.get("start", {}).get("label", "")
            end = edge.get("end", {}).get("label", "")
            weight = edge.get("weight", 0.0)
            surface = edge.get("surfaceText", "")

            if rel and start and end:
                relations.append(ConceptRelation(
                    start=start,
                    end=end,
                    relation=rel,
                    weight=weight,
                    surface_text=surface,
                ))

        return relations
