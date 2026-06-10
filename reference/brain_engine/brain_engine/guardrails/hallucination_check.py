"""Hallucination Check - Verifies agent responses against known facts.

Validates that claims in agent responses are grounded in facts from
semantic memory, slot values, and the knowledge base. Detects fabricated
numbers, names, dates, and other specific claims that are not supported
by the available evidence.
"""

from __future__ import annotations

import logging
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from brain_engine.memory.semantic_memory import SemanticMemory

logger = logging.getLogger(__name__)


class HallucinationCheck:
    """Checks agent responses for potential hallucinations.

    Validates specific claims (numbers, names, dates, URLs) against
    known facts from slots and semantic memory. Can operate in strict
    mode (flag all unverifiable claims) or lenient mode (flag only
    contradictions).

    Args:
        strict: If True, flag any specific claim that cannot be verified
            against known facts. If False, only flag clear contradictions.
        semantic_memory: Optional SemanticMemory instance for fact verification.
            When provided, claims are checked against stored knowledge.
        similarity_threshold: Minimum similarity score for semantic memory
            matches to count as verification (0.0-1.0).
    """

    def __init__(
        self,
        strict: bool = False,
        semantic_memory: "SemanticMemory | None" = None,
        similarity_threshold: float = 0.7,
    ) -> None:
        self._strict = strict
        self._semantic_memory = semantic_memory
        self._similarity_threshold = similarity_threshold

    def check(
        self,
        response: str,
        known_facts: dict[str, Any] | None = None,
        knowledge_base: str = "",
    ) -> list[str]:
        """Check a response for potential hallucinations.

        Performs synchronous checks against known_facts and knowledge_base
        text. For async semantic memory verification, use check_async().

        Args:
            response: The agent's proposed response.
            known_facts: Dictionary of verified facts (slot values, etc.).
            knowledge_base: Relevant knowledge base text to check against.

        Returns:
            List of warning strings. Empty list means no issues found.
        """
        warnings: list[str] = []
        facts = known_facts or {}

        warnings.extend(self._check_numbers(response, facts, knowledge_base))
        warnings.extend(self._check_known_entities(response, facts))
        warnings.extend(self._check_urls(response, knowledge_base))
        warnings.extend(self._check_dates(response, facts, knowledge_base))

        if warnings:
            logger.warning("Hallucination warnings: %s", warnings)

        return warnings

    async def check_async(
        self,
        response: str,
        known_facts: dict[str, Any] | None = None,
        knowledge_base: str = "",
    ) -> list[str]:
        """Check response with async semantic memory verification.

        Extends the synchronous checks with semantic similarity search
        against the vector store to verify claims.

        Args:
            response: The agent's proposed response.
            known_facts: Dictionary of verified facts.
            knowledge_base: Relevant knowledge base text.

        Returns:
            List of warning strings.
        """
        warnings = self.check(response, known_facts, knowledge_base)

        if self._semantic_memory is not None:
            warnings.extend(
                await self._verify_against_memory(response)
            )

        return warnings

    def _check_numbers(
        self,
        response: str,
        facts: dict[str, Any],
        knowledge_base: str,
    ) -> list[str]:
        """Check for fabricated numbers and prices."""
        warnings: list[str] = []

        # Match currency amounts and standalone numbers
        numbers = re.findall(
            r"\$[\d,]+(?:\.\d{2})?|\b\d{3,}\b",
            response,
        )
        known_values = {
            str(v).replace(",", "")
            for v in facts.values()
            if v is not None
        }

        for num in numbers:
            clean = num.replace("$", "").replace(",", "")
            if clean in known_values:
                continue
            if clean in knowledge_base.replace(",", ""):
                continue
            if self._strict:
                warnings.append(f"Unverified number in response: {num}")

        return warnings

    def _check_known_entities(
        self,
        response: str,
        facts: dict[str, Any],
    ) -> list[str]:
        """Check that mentioned entity names match known values."""
        warnings: list[str] = []
        response_lower = response.lower()

        # Check named entity slots
        entity_keys = [
            k for k in facts
            if any(
                term in k.lower()
                for term in ("name", "title", "address", "email", "phone")
            )
        ]

        for key in entity_keys:
            value = facts.get(key)
            if not value:
                continue
            known_value = str(value).lower()

            # Check if the response discusses this entity type but uses
            # a different value
            entity_type = key.replace("_", " ").lower()
            if entity_type in response_lower and known_value not in response_lower:
                warnings.append(
                    f"Response references '{entity_type}' but does not use "
                    f"the known value: {value}"
                )

        return warnings

    @staticmethod
    def _check_urls(response: str, knowledge_base: str) -> list[str]:
        """Check for potentially fabricated URLs."""
        warnings: list[str] = []

        urls = re.findall(
            r"https?://[^\s\)\]\"']+",
            response,
        )

        for url in urls:
            if url not in knowledge_base:
                warnings.append(f"Unverified URL in response: {url}")

        return warnings

    def _check_dates(
        self,
        response: str,
        facts: dict[str, Any],
        knowledge_base: str,
    ) -> list[str]:
        """Check for potentially fabricated dates."""
        warnings: list[str] = []

        # Match common date formats
        dates = re.findall(
            r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b"
            r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s*\d{4}\b",
            response,
            re.IGNORECASE,
        )

        known_dates = {
            str(v) for v in facts.values()
            if v is not None and re.search(r"\d{2,4}[/\-]\d", str(v))
        }

        for date in dates:
            if date in knowledge_base or date in known_dates:
                continue
            # Check if any known date contains this date
            if any(date in kd for kd in known_dates):
                continue
            if self._strict:
                warnings.append(f"Unverified date in response: {date}")

        return warnings

    async def _verify_against_memory(self, response: str) -> list[str]:
        """Verify factual claims against semantic memory.

        Extracts key sentences from the response and checks if they
        have supporting evidence in the vector store.
        """
        warnings: list[str] = []

        if self._semantic_memory is None:
            return warnings

        # Split response into sentences for individual verification
        sentences = re.split(r"[.!?]\s+", response.strip())

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue

            # Skip questions and hedged statements
            if sentence.endswith("?") or sentence.lower().startswith(
                ("i think", "maybe", "perhaps", "possibly", "it seems")
            ):
                continue

            # Check for factual claims (contains specific details)
            has_specifics = bool(
                re.search(r"\d+|[$%]|\b(?:always|never|exactly|must)\b", sentence)
            )
            if not has_specifics:
                continue

            results = await self._semantic_memory.search(
                sentence, top_k=1, score_threshold=self._similarity_threshold
            )

            if not results:
                warnings.append(
                    f"Claim not supported by knowledge base: "
                    f"'{sentence[:80]}...'"
                )

        return warnings

    def is_safe(
        self,
        response: str,
        known_facts: dict[str, Any] | None = None,
        knowledge_base: str = "",
    ) -> bool:
        """Check whether a response is free of hallucination warnings.

        Args:
            response: The response to check.
            known_facts: Known facts to verify against.
            knowledge_base: Knowledge base text.

        Returns:
            True if no hallucination warnings were found.
        """
        return len(self.check(response, known_facts, knowledge_base)) == 0
