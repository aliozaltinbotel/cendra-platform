"""Repeat Check - Detects when the agent is repeating itself.

Monitors recent agent responses and flags new responses that are too
similar to previous ones within a sliding window. Uses both Jaccard
word similarity and sequence-based matching for robust detection.
"""

from __future__ import annotations

import logging
from collections import deque
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class RepeatCheck:
    """Detects repetitive agent responses within a sliding window.

    Maintains a bounded window of recent responses and compares new
    candidates against them using a combination of Jaccard word overlap
    and SequenceMatcher ratio for robust similarity detection.

    Args:
        window_size: Number of recent responses to compare against.
        similarity_threshold: Similarity ratio (0.0-1.0) above which
            a response is considered a repeat. Default 0.85.
    """

    def __init__(
        self,
        window_size: int = 5,
        similarity_threshold: float = 0.85,
    ) -> None:
        self._window: deque[str] = deque(maxlen=window_size)
        self._similarity_threshold = similarity_threshold

    def is_repeat(self, response: str) -> bool:
        """Check if a response is too similar to any recent response.

        Args:
            response: The proposed agent response.

        Returns:
            True if the response is a repeat of a recent response.
        """
        normalized = response.strip().lower()

        for prev in self._window:
            similarity = self._compute_similarity(normalized, prev)
            if similarity >= self._similarity_threshold:
                logger.warning(
                    "Repeat detected (similarity=%.2f): %s",
                    similarity,
                    response[:80],
                )
                return True

        return False

    def record(self, response: str) -> None:
        """Record a response in the sliding window.

        Args:
            response: The response to record.
        """
        self._window.append(response.strip().lower())

    def check_and_record(self, response: str) -> bool:
        """Check for repeat and record the response if it is not one.

        Args:
            response: The response to check and potentially record.

        Returns:
            True if the response is OK (not a repeat).
            False if the response is a repeat.
        """
        if self.is_repeat(response):
            return False
        self.record(response)
        return True

    @staticmethod
    def _jaccard_similarity(a: str, b: str) -> float:
        """Compute Jaccard similarity on word sets."""
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    @staticmethod
    def _sequence_similarity(a: str, b: str) -> float:
        """Compute sequence-based similarity using SequenceMatcher."""
        return SequenceMatcher(None, a, b).ratio()

    @classmethod
    def _compute_similarity(cls, a: str, b: str) -> float:
        """Compute combined similarity score.

        Uses the maximum of Jaccard word overlap and SequenceMatcher
        ratio to catch both word-reordered repeats and near-exact copies.

        Args:
            a: First text (normalized).
            b: Second text (normalized).

        Returns:
            Similarity score between 0.0 and 1.0.
        """
        jaccard = cls._jaccard_similarity(a, b)
        sequence = cls._sequence_similarity(a, b)
        return max(jaccard, sequence)

    def get_recent_responses(self) -> list[str]:
        """Return the current window of recent responses."""
        return list(self._window)

    def reset(self) -> None:
        """Clear the response history window."""
        self._window.clear()

    def __repr__(self) -> str:
        return (
            f"RepeatCheck(window={self._window.maxlen}, "
            f"threshold={self._similarity_threshold}, "
            f"buffered={len(self._window)})"
        )
