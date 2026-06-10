"""Base Intent definitions for the Brain Engine.

Provides a standard set of intents that can be extended per-project.
The universal chassis uses these as the baseline; domain-specific intents
should subclass or compose with these.
"""

from enum import StrEnum


class Intent(StrEnum):
    """Universal intent categories recognized by the Brain Engine.

    These represent the fundamental conversational intents that appear
    across all domains. Project-specific intents should extend this enum
    or create a parallel enum that maps back to these base categories.
    """

    UNKNOWN = "unknown"
    GREETING = "greeting"
    FAREWELL = "farewell"
    COMPLAINT = "complaint"
    REQUEST = "request"
    INFO = "info"
    ACTION = "action"
    CONFIRMATION = "confirmation"
    CANCELLATION = "cancellation"
    CLARIFICATION = "clarification"
    FEEDBACK = "feedback"

    @classmethod
    def from_string(cls, value: str) -> "Intent":
        """Parse an intent from a raw string, falling back to UNKNOWN.

        Args:
            value: Raw string to parse (case-insensitive).

        Returns:
            The matched Intent, or Intent.UNKNOWN if no match is found.
        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.UNKNOWN

    @classmethod
    def all_values(cls) -> list[str]:
        """Return all intent values as a list of strings."""
        return [member.value for member in cls]
