"""Human Intervention Classifier — detects when PM review is needed.

Regex-based classifier that scans AI responses for deferral phrases,
apologetic patterns, and availability confirmations that should not
be auto-sent without PM review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class InterventionResult:
    """Result of human intervention classification.

    Attributes:
        needs_intervention: True if PM should review before sending.
        has_availability_confirmation: True if response confirms availability.
        matched_phrases: Which phrases triggered intervention.
    """

    needs_intervention: bool = False
    has_availability_confirmation: bool = False
    matched_phrases: list[str] = field(default_factory=list)


# Phrases indicating the AI deferred / couldn't answer
_DEFERRAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:i(?:'ll| will)|we(?:'ll| will)) (?:get back|check|find out|confirm|look into)", re.I),
    re.compile(r"(?:let me|allow me to) (?:check|verify|confirm|look into|find out)", re.I),
    re.compile(r"i(?:'m| am) not (?:sure|certain|able)", re.I),
    re.compile(r"(?:don'?t|do not) have (?:access|that information|this information)", re.I),
    re.compile(r"(?:unable|cannot) (?:to )?(?:confirm|verify|provide|access)", re.I),
    re.compile(r"(?:will|need to) (?:follow up|escalate|forward|contact)", re.I),
    re.compile(r"(?:unfortunately|regrettably),? (?:i|we) (?:can'?t|cannot)", re.I),
    re.compile(r"i(?:'ll| will) (?:ask|reach out|contact) (?:the|my|our)", re.I),
    re.compile(r"(?:as soon as|once) (?:i|we) (?:have|get|receive|hear)", re.I),
]

# Phrases indicating explicit availability confirmation (should not auto-send)
_AVAILABILITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:the property|it|we) (?:is|are) (?:available|free|open)", re.I),
    re.compile(r"(?:yes|sure),? (?:it'?s|the dates? (?:is|are)) available", re.I),
    re.compile(r"i can confirm (?:availability|the dates?|it'?s available)", re.I),
    re.compile(r"those dates? (?:are|is) (?:available|open|free)", re.I),
]

# Sensitive topics that need PM eyes
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:refund|reimburse|compensat|discount|credit)\b", re.I),
    re.compile(r"\b(?:legal|lawyer|lawsuit|sue|court)\b", re.I),
    re.compile(r"\b(?:police|authorities|report)\b", re.I),
    re.compile(r"\b(?:cancel(?:lation)?|terminate)\b", re.I),
]


class HumanInterventionClassifier:
    """Classifies AI responses for human review requirements.

    Uses regex pattern matching — no LLM calls needed.
    Fast and deterministic.
    """

    def __init__(self) -> None:
        self._deferral_patterns = list(_DEFERRAL_PATTERNS)
        self._availability_patterns = list(_AVAILABILITY_PATTERNS)
        self._sensitive_patterns = list(_SENSITIVE_PATTERNS)

    def classify(self, response: str) -> InterventionResult:
        """Classify an AI response for human intervention needs.

        Args:
            response: The AI-generated response text.

        Returns:
            InterventionResult with matched patterns.
        """
        matched: list[str] = []
        needs_intervention = False

        # Check deferrals
        for pattern in self._deferral_patterns:
            match = pattern.search(response)
            if match:
                matched.append(f"deferral: {match.group()}")
                needs_intervention = True

        # Check sensitive topics
        for pattern in self._sensitive_patterns:
            match = pattern.search(response)
            if match:
                matched.append(f"sensitive: {match.group()}")
                needs_intervention = True

        # Check availability confirmations
        has_avail = False
        for pattern in self._availability_patterns:
            match = pattern.search(response)
            if match:
                matched.append(f"availability_confirmation: {match.group()}")
                has_avail = True
                needs_intervention = True

        return InterventionResult(
            needs_intervention=needs_intervention,
            has_availability_confirmation=has_avail,
            matched_phrases=matched,
        )

    def classify_with_translation(
        self,
        response: str,
        english_translation: str = "",
    ) -> InterventionResult:
        """Classify using both original and English translation.

        For non-English responses, checks both the original
        and the English translation for broader coverage.

        Args:
            response: Original AI response.
            english_translation: English translation (if available).

        Returns:
            Combined InterventionResult.
        """
        result = self.classify(response)

        if english_translation and english_translation != response:
            translated_result = self.classify(english_translation)
            if translated_result.needs_intervention:
                result.needs_intervention = True
                result.matched_phrases.extend(translated_result.matched_phrases)
            if translated_result.has_availability_confirmation:
                result.has_availability_confirmation = True

        return result
