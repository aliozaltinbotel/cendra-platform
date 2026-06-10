"""RepeatQuestionCheck — Prevents agent from re-asking for already-filled slots.

Detects when the agent is about to ask the user for information that is
already available in the SlotManager. This is different from RepeatCheck
(which catches repeated responses) — this specifically catches questions
about data that's already been collected.

Examples:
- Agent asks "What is the guest's checkout time?" but slot john_checkout_time = "3:00 PM"
- Agent asks "Who is the cleaner?" but slot cleaner_name = "Maria"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RepeatQuestionIssue:
    """A detected repeat question issue.

    Attributes:
        slot_name: The slot that already has a value.
        slot_value: The current value of the slot.
        question_fragment: The part of the response that asks about this slot.
        suggestion: Suggested alternative phrasing.
    """

    slot_name: str
    slot_value: Any
    question_fragment: str
    suggestion: str


# Maps slot names to question patterns that would be asking for that data
SLOT_QUESTION_PATTERNS: dict[str, list[str]] = {
    "guest_name": [
        r"what(?:'s| is) (?:the )?guest(?:'s)? name",
        r"who is the guest",
        r"can (?:you|I) (?:get|have) the guest(?:'s)? name",
    ],
    "departing_guest_name": [
        r"what(?:'s| is) the departing guest(?:'s)? name",
        r"who is (?:leaving|departing|checking out)",
    ],
    "incoming_guest_name": [
        r"what(?:'s| is) the (?:incoming|arriving|new) guest(?:'s)? name",
        r"who is (?:arriving|coming|checking in)",
    ],
    "cleaner_name": [
        r"who is the cleaner",
        r"which cleaner",
        r"what(?:'s| is) the cleaner(?:'s)? name",
        r"do we have a cleaner",
    ],
    "cleaner_phone": [
        r"what(?:'s| is) the cleaner(?:'s)? (?:phone|number|contact)",
        r"how (?:do|can) (?:we|I) (?:reach|contact|call) the cleaner",
    ],
    "property_address": [
        r"what(?:'s| is) the (?:property )?address",
        r"where is the (?:property|apartment|unit)",
    ],
    "checkout_time": [
        r"what(?:'s| is) the checkout time",
        r"when (?:is|does) (?:checkout|check.out)",
        r"what time (?:is|does) checkout",
    ],
    "john_checkout_time": [
        r"what time (?:is|does|will) (?:john|the guest) (?:checkout|check out|leave)",
        r"when (?:is|will) (?:john|the guest) (?:leaving|departing|checking out)",
    ],
    "checkin_date": [
        r"what(?:'s| is) the check.?in date",
        r"when (?:is|does) check.?in",
    ],
    "checkout_date": [
        r"what(?:'s| is) the check.?out date",
        r"when (?:is|does) check.?out",
    ],
    "reservation_id": [
        r"what(?:'s| is) the (?:reservation|booking) (?:id|number)",
    ],
    "property_id": [
        r"which property",
        r"what(?:'s| is) the property (?:id|identifier)",
    ],
    "damage_description": [
        r"what(?:'s| is) the damage",
        r"what damage (?:was|is) (?:found|detected)",
        r"describe the damage",
    ],
    "cleaning_time": [
        r"what time (?:is|should) (?:the )?cleaning",
        r"when (?:is|should) (?:the )?cleaning (?:start|happen)",
    ],
    "late_checkout_fee": [
        r"what(?:'s| is) the (?:late checkout )?fee",
        r"how much (?:is|does) (?:the )?late checkout (?:cost|fee)",
    ],
}


class RepeatQuestionCheck:
    """Detects when agent asks for data already in slots.

    Compares the agent's response against known slot values and
    flags any questions that redundantly request already-available data.

    Args:
        custom_patterns: Additional slot->patterns mappings.
    """

    def __init__(
        self,
        custom_patterns: dict[str, list[str]] | None = None,
    ) -> None:
        self._patterns = dict(SLOT_QUESTION_PATTERNS)
        if custom_patterns:
            self._patterns.update(custom_patterns)

        # Pre-compile all patterns
        self._compiled: dict[str, list[re.Pattern[str]]] = {
            slot: [re.compile(p, re.IGNORECASE) for p in patterns]
            for slot, patterns in self._patterns.items()
        }

    def check(
        self,
        response: str,
        filled_slots: dict[str, Any],
    ) -> list[RepeatQuestionIssue]:
        """Check if the response asks about already-filled slots.

        Args:
            response: The agent's proposed response.
            filled_slots: Current slot values from SlotManager.

        Returns:
            List of RepeatQuestionIssue objects. Empty = no issues.
        """
        issues: list[RepeatQuestionIssue] = []

        for slot_name, patterns in self._compiled.items():
            slot_value = filled_slots.get(slot_name)

            # Only flag if the slot has a meaningful value
            if slot_value is None or slot_value == "":
                continue

            for pattern in patterns:
                match = pattern.search(response)
                if match:
                    issues.append(RepeatQuestionIssue(
                        slot_name=slot_name,
                        slot_value=slot_value,
                        question_fragment=match.group(),
                        suggestion=(
                            f"'{slot_name}' is already known: {slot_value}. "
                            f"Use the known value instead of asking."
                        ),
                    ))
                    break  # One match per slot is enough

        if issues:
            logger.warning(
                "RepeatQuestionCheck: %d issue(s) — agent re-asking for: %s",
                len(issues),
                [i.slot_name for i in issues],
            )

        return issues

    def build_correction_prompt(self, issues: list[RepeatQuestionIssue]) -> str:
        """Build a correction prompt for the LLM to fix repeat questions.

        Args:
            issues: Detected repeat question issues.

        Returns:
            Correction prompt string.
        """
        if not issues:
            return ""

        lines = [
            "Do NOT ask for the following information — it's already known:",
        ]
        for issue in issues:
            lines.append(f"  - {issue.slot_name} = {issue.slot_value}")
        lines.append("Use these values directly in your response.")

        return "\n".join(lines)
