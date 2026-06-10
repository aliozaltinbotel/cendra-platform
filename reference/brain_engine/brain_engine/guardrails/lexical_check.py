"""LexicalCheck — Filters bureaucratic language, jargon, and tone issues.

Layer 4 of the guardrail system. Ensures agent responses are:
- Natural and conversational (not robotic or overly formal)
- Free of corporate jargon and bureaucratic filler
- Appropriate in tone for the context (guest vs cleaner vs owner)
- Culturally sensitive and polite
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LexicalIssue:
    """A detected lexical issue in agent output.

    Attributes:
        issue_type: Type of issue.
        severity: LOW, MEDIUM, HIGH.
        original: The problematic text fragment.
        suggestion: Suggested replacement.
        position: Character position in the text.
    """

    issue_type: str
    severity: str
    original: str
    suggestion: str = ""
    position: int = 0


@dataclass(slots=True)
class LexicalResult:
    """Result of lexical analysis.

    Attributes:
        issues: List of detected issues.
        cleaned_text: Text with issues auto-corrected (if fixable).
        tone_score: Overall tone score (0=robotic, 10=natural).
    """

    issues: list[LexicalIssue] = field(default_factory=list)
    cleaned_text: str = ""
    tone_score: float = 7.0

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0

    @property
    def high_severity_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "HIGH")


# Bureaucratic phrases to replace with natural alternatives
BUREAUCRATIC_REPLACEMENTS: dict[str, str] = {
    r"\bplease be advised that\b": "just so you know",
    r"\bwe would like to inform you that\b": "",
    r"\bkindly note that\b": "please note",
    r"\bit has come to our attention\b": "we noticed",
    r"\bin accordance with\b": "following",
    r"\bwith regard to\b": "about",
    r"\bat this point in time\b": "now",
    r"\bin the event that\b": "if",
    r"\bprior to\b": "before",
    r"\bsubsequent to\b": "after",
    r"\bnotwithstanding\b": "despite",
    r"\bin lieu of\b": "instead of",
    r"\bpursuant to\b": "following",
    r"\bfor the purpose of\b": "to",
    r"\bin order to\b": "to",
    r"\bat your earliest convenience\b": "when you can",
    r"\bdo not hesitate to\b": "feel free to",
    r"\bwe regret to inform you\b": "unfortunately",
    r"\bplease do not hesitate\b": "feel free",
    r"\bI am writing to\b": "",
    r"\bas per our\b": "as we",
    r"\beffectuate\b": "make",
    r"\butilize\b": "use",
    r"\bfacilitate\b": "help",
    r"\bcommence\b": "start",
    r"\bterminate\b": "end",
    r"\bascertain\b": "find out",
    r"\bendeavor\b": "try",
}

# Phrases that sound robotic / AI-like
ROBOTIC_PATTERNS: list[tuple[str, str]] = [
    (r"\bI understand your concern\b", "tone:robotic"),
    (r"\bI apologize for any inconvenience\b", "tone:generic_apology"),
    (r"\bthank you for your patience\b", "tone:filler"),
    (r"\bI'm here to help\b", "tone:filler"),
    (r"\bplease let me know if you need anything else\b", "tone:filler"),
    (r"\bIs there anything else I can assist you with\b", "tone:filler"),
    (r"\bI hope this helps\b", "tone:filler"),
    (r"\bgreat question\b", "tone:condescending"),
    (r"\bthat's a great question\b", "tone:condescending"),
    (r"\babsolutely!\b", "tone:overly_enthusiastic"),
    (r"\bof course!\b", "tone:overly_enthusiastic"),
]

# Tone-inappropriate terms for specific audiences
AUDIENCE_FILTERS: dict[str, list[str]] = {
    "guest": [
        r"\bliability\b",
        r"\bpenalty\b",
        r"\bviolation\b",
        r"\btermination\b",
        r"\bforfeiture\b",
    ],
    "cleaner": [
        r"\binsubordination\b",
        r"\bperformance review\b",
        r"\bdisciplinary\b",
    ],
    "owner": [],  # Owners can handle professional language
}


class LexicalCheck:
    """Checks and cleans agent responses for lexical quality.

    Detects and optionally auto-corrects:
    - Bureaucratic/corporate jargon
    - Robotic AI-sounding phrases
    - Tone-inappropriate language for the audience
    - Overly long or complex sentences

    Args:
        audience: Target audience (guest, cleaner, owner).
        auto_fix: Whether to auto-correct fixable issues.
        max_sentence_words: Flag sentences longer than this.
    """

    def __init__(
        self,
        audience: str = "guest",
        auto_fix: bool = True,
        max_sentence_words: int = 35,
    ) -> None:
        self._audience = audience
        self._auto_fix = auto_fix
        self._max_sentence_words = max_sentence_words

    def check(self, text: str) -> LexicalResult:
        """Run all lexical checks on agent output.

        Args:
            text: The agent's proposed response.

        Returns:
            LexicalResult with issues and optionally cleaned text.
        """
        result = LexicalResult(cleaned_text=text)

        self._check_bureaucratic(text, result)
        self._check_robotic(text, result)
        self._check_audience(text, result)
        self._check_sentence_complexity(text, result)

        # Compute tone score
        result.tone_score = self._compute_tone_score(result)

        if self._auto_fix and result.issues:
            result.cleaned_text = self._auto_correct(text)

        if result.issues:
            logger.info(
                "Lexical check: %d issues (tone=%.1f)",
                len(result.issues), result.tone_score,
            )

        return result

    def _check_bureaucratic(self, text: str, result: LexicalResult) -> None:
        """Detect bureaucratic/corporate jargon."""
        for pattern, replacement in BUREAUCRATIC_REPLACEMENTS.items():
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for match in matches:
                result.issues.append(LexicalIssue(
                    issue_type="bureaucratic",
                    severity="MEDIUM",
                    original=match.group(),
                    suggestion=replacement or "(remove)",
                    position=match.start(),
                ))

    def _check_robotic(self, text: str, result: LexicalResult) -> None:
        """Detect robotic/AI-like phrases."""
        for pattern, issue_type in ROBOTIC_PATTERNS:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for match in matches:
                result.issues.append(LexicalIssue(
                    issue_type=issue_type,
                    severity="LOW",
                    original=match.group(),
                    suggestion="Consider more natural phrasing",
                    position=match.start(),
                ))

    def _check_audience(self, text: str, result: LexicalResult) -> None:
        """Check for tone-inappropriate terms for the target audience."""
        filters = AUDIENCE_FILTERS.get(self._audience, [])
        for pattern in filters:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for match in matches:
                result.issues.append(LexicalIssue(
                    issue_type="audience_inappropriate",
                    severity="HIGH",
                    original=match.group(),
                    suggestion=f"Avoid '{match.group()}' when communicating with {self._audience}",
                    position=match.start(),
                ))

    def _check_sentence_complexity(self, text: str, result: LexicalResult) -> None:
        """Flag overly long/complex sentences."""
        sentences = re.split(r"[.!?]+", text)
        for sentence in sentences:
            words = sentence.strip().split()
            if len(words) > self._max_sentence_words:
                result.issues.append(LexicalIssue(
                    issue_type="complex_sentence",
                    severity="LOW",
                    original=sentence.strip()[:80] + "...",
                    suggestion=f"Sentence has {len(words)} words — consider splitting",
                ))

    def _auto_correct(self, text: str) -> str:
        """Apply automatic corrections for fixable issues."""
        corrected = text
        for pattern, replacement in BUREAUCRATIC_REPLACEMENTS.items():
            corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)

        # Clean up double spaces and leading spaces from removals
        corrected = re.sub(r"  +", " ", corrected)
        corrected = re.sub(r"\n +", "\n", corrected)
        return corrected.strip()

    @staticmethod
    def _compute_tone_score(result: LexicalResult) -> float:
        """Compute overall tone naturalness score (0-10)."""
        score = 10.0

        for issue in result.issues:
            match issue.severity:
                case "HIGH":
                    score -= 2.0
                case "MEDIUM":
                    score -= 1.0
                case "LOW":
                    score -= 0.5

        return max(score, 0.0)
