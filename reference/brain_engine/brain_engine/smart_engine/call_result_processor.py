"""CallResultProcessor — extract facts and trigger actions from call transcripts.

After every phone call (to guest, cleaner, vendor, manager), this
module analyzes the conversation transcript, extracts actionable
facts, saves them to guest/property memory, and triggers follow-up
actions automatically.

Example flow::

    Guest says: "I'm leaving at 2 PM, but the AC is broken"
    → extract: checkout_time=14:00, issue=AC_broken
    → save to guest memory: early_departure, maintenance_needed
    → action: schedule_cleaning(14:00), dispatch_vendor(AC)

Based on: Brain Engine Autonomous Property Manager design.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFact:
    """A fact extracted from a call transcript.

    Attributes:
        fact_type: Category (checkout_time, issue, price, availability, etc.).
        value: The extracted value.
        confidence: How sure we are (0.0-1.0).
        source: Who said it (guest, cleaner, vendor).
        raw_text: The original text that triggered extraction.
    """

    fact_type: str
    value: Any
    confidence: float = 0.8
    source: str = ""
    raw_text: str = ""


@dataclass
class ActionTrigger:
    """An action to take based on extracted facts.

    Attributes:
        action_type: What to do (schedule_cleaning, dispatch_vendor, etc.).
        params: Action parameters.
        urgency: How urgent (low, medium, high, critical).
        reason: Why this action was triggered.
    """

    action_type: str
    params: dict[str, Any] = field(default_factory=dict)
    urgency: str = "medium"
    reason: str = ""


class CallResultProcessor:
    """Processes call transcripts to extract facts and trigger actions.

    Works for all call types: guest, cleaner, vendor, manager.
    Extracts structured data from natural language and maps it
    to concrete property management actions.

    Args:
        memory: Memory system for storing guest/property facts.
        property_id: Property context for this call.
    """

    def __init__(
        self,
        memory: Any = None,
        property_id: str = "",
    ) -> None:
        self._memory = memory
        self._property_id = property_id

    async def process(
        self,
        transcript: str,
        call_type: str,
        contact_id: str = "",
        contact_name: str = "",
    ) -> dict[str, Any]:
        """Process a call transcript end-to-end.

        Extracts facts, saves to memory, determines actions.

        Args:
            transcript: Full call transcript text.
            call_type: Who was called (guest, cleaner, vendor, manager).
            contact_id: ID of the person called.
            contact_name: Name of the person called.

        Returns:
            Dict with extracted_facts, actions, and memory_updates.
        """
        facts = self._extract_facts(transcript, call_type)
        actions = self._determine_actions(facts, call_type)
        memory_updates = await self._save_to_memory(
            facts, contact_id, contact_name, call_type,
        )

        logger.info(
            "Processed %s call with %s: %d facts, %d actions",
            call_type, contact_name, len(facts), len(actions),
        )

        return {
            "extracted_facts": [_fact_to_dict(f) for f in facts],
            "actions": [_action_to_dict(a) for a in actions],
            "memory_updates": memory_updates,
        }

    def _extract_facts(
        self,
        transcript: str,
        call_type: str,
    ) -> list[ExtractedFact]:
        """Extract structured facts from transcript text.

        Args:
            transcript: Conversation text.
            call_type: Context for extraction rules.

        Returns:
            List of extracted facts.
        """
        facts: list[ExtractedFact] = []
        user_text = _extract_user_lines(transcript)

        facts.extend(_extract_time_facts(user_text, call_type))
        facts.extend(_extract_issue_facts(user_text))
        facts.extend(_extract_price_facts(user_text))
        facts.extend(_extract_availability_facts(user_text, call_type))
        facts.extend(_extract_sentiment_facts(user_text))

        return facts

    def _determine_actions(
        self,
        facts: list[ExtractedFact],
        call_type: str,
    ) -> list[ActionTrigger]:
        """Map extracted facts to concrete actions.

        Args:
            facts: Facts from the call.
            call_type: Who was called.

        Returns:
            List of actions to execute.
        """
        actions: list[ActionTrigger] = []

        for fact in facts:
            action = _fact_to_action(fact, call_type, self._property_id)
            if action:
                actions.append(action)

        return actions

    async def _save_to_memory(
        self,
        facts: list[ExtractedFact],
        contact_id: str,
        contact_name: str,
        call_type: str,
    ) -> list[dict[str, Any]]:
        """Save extracted facts to the memory system.

        Args:
            facts: Facts to store.
            contact_id: Who the facts are about.
            contact_name: Human-readable name.
            call_type: Context.

        Returns:
            List of memory update records.
        """
        if not self._memory:
            return []

        updates: list[dict[str, Any]] = []
        for fact in facts:
            record = {
                "contact_id": contact_id,
                "contact_name": contact_name,
                "call_type": call_type,
                "property_id": self._property_id,
                "fact_type": fact.fact_type,
                "value": fact.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            updates.append(record)

        return updates


# ── Fact extraction functions ────────────────────────────────────────── #


def _extract_user_lines(transcript: str) -> str:
    """Get only user/contact lines from transcript.

    Args:
        transcript: Full transcript.

    Returns:
        Concatenated user lines.
    """
    lines: list[str] = []
    for line in transcript.split("\n"):
        stripped = line.strip()
        if stripped.startswith("User:"):
            lines.append(stripped[5:].strip())
    return " ".join(lines)


def _extract_time_facts(
    text: str,
    call_type: str,
) -> list[ExtractedFact]:
    """Extract time-related facts (checkout time, arrival time, ETA).

    Args:
        text: User's words.
        call_type: Context.

    Returns:
        Time facts.
    """
    facts: list[ExtractedFact] = []
    lower = text.lower()

    # Match patterns like "at 2 PM", "at 14:00", "by 11"
    time_patterns = re.findall(
        r"(?:at|by|around|before|after)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)?)",
        text,
    )
    for time_str in time_patterns:
        fact_type = _infer_time_type(lower, call_type)
        facts.append(ExtractedFact(
            fact_type=fact_type,
            value=time_str.strip(),
            source=call_type,
            raw_text=text[:100],
        ))

    return facts


def _infer_time_type(text: str, call_type: str) -> str:
    """Determine what kind of time was mentioned.

    Args:
        text: Lowercased conversation text.
        call_type: Who was called.

    Returns:
        Fact type string.
    """
    if call_type == "guest":
        if "leav" in text or "check" in text or "out" in text:
            return "checkout_time"
        if "arriv" in text or "come" in text or "in" in text:
            return "arrival_time"
    if call_type == "cleaner":
        return "cleaner_eta"
    if call_type == "vendor":
        return "vendor_eta"
    return "mentioned_time"


def _extract_issue_facts(text: str) -> list[ExtractedFact]:
    """Extract reported issues (broken AC, dirty, leak, etc.).

    Args:
        text: User's words.

    Returns:
        Issue facts.
    """
    facts: list[ExtractedFact] = []
    lower = text.lower()

    issue_map = {
        "ac_broken": ["ac", "air condition", "cooling", "heating", "klimaanlage"],
        "tv_broken": ["tv", "television", "screen", "fernseher"],
        "water_issue": ["water", "leak", "plumbing", "tap", "faucet", "wasser"],
        "wifi_issue": ["wifi", "internet", "network", "wlan"],
        "lock_issue": ["lock", "key", "door", "schlüssel", "kilit"],
        "dirty": ["dirty", "clean", "filthy", "messy", "schmutzig", "kirli"],
        "noise": ["noise", "loud", "neighbor", "lärm", "gürültü"],
        "pest": ["bug", "insect", "cockroach", "mouse", "rat"],
    }

    for issue_type, keywords in issue_map.items():
        if any(kw in lower for kw in keywords):
            facts.append(ExtractedFact(
                fact_type="reported_issue",
                value=issue_type,
                confidence=0.8,
                raw_text=text[:100],
            ))

    return facts


def _extract_price_facts(text: str) -> list[ExtractedFact]:
    """Extract price/cost mentions.

    Args:
        text: User's words.

    Returns:
        Price facts.
    """
    facts: list[ExtractedFact] = []

    price_matches = re.findall(
        r"(\d+)\s*(?:euro|eur|€|\$|dollar|usd|tl|lira)",
        text, re.IGNORECASE,
    )
    for price in price_matches:
        facts.append(ExtractedFact(
            fact_type="quoted_price",
            value=int(price),
            confidence=0.9,
            raw_text=text[:100],
        ))

    return facts


def _extract_availability_facts(
    text: str,
    call_type: str,
) -> list[ExtractedFact]:
    """Extract availability status.

    Args:
        text: User's words.
        call_type: Context.

    Returns:
        Availability facts.
    """
    facts: list[ExtractedFact] = []
    lower = text.lower()

    # Check negative FIRST (contains "not available" which also has "available")
    negative = ["not available", "can't", "cannot", "busy", "not able",
                "sick", "unavailable", "sorry no", "i can't",
                "не могу", "занят", "нет",
                "hayır", "yapamam", "müsait değil",
                "no puedo", "no estoy"]
    positive = ["yes", "sure", "i can", "available", "can come",
                "i'll be", "ok", "okay", "alright",
                "да", "могу", "приду", "конечно",
                "evet", "gelirim", "tamam",
                "sí", "claro", "puedo"]

    if any(w in lower for w in negative):
        facts.append(ExtractedFact(
            fact_type="availability",
            value="unavailable",
            source=call_type,
            raw_text=text[:100],
        ))
    elif any(w in lower for w in positive):
        facts.append(ExtractedFact(
            fact_type="availability",
            value="available",
            source=call_type,
            raw_text=text[:100],
        ))

    return facts


def _extract_sentiment_facts(text: str) -> list[ExtractedFact]:
    """Extract overall sentiment from the conversation.

    Args:
        text: User's words.

    Returns:
        Sentiment fact.
    """
    lower = text.lower()

    angry = ["unacceptable", "terrible", "worst", "angry", "refund",
             "disgusting", "horrible", "complaint"]
    happy = ["wonderful", "great", "excellent", "perfect", "thank",
             "beautiful", "amazing", "love"]

    if any(w in lower for w in angry):
        return [ExtractedFact(
            fact_type="sentiment",
            value="negative",
            confidence=0.85,
            raw_text=text[:100],
        )]
    if any(w in lower for w in happy):
        return [ExtractedFact(
            fact_type="sentiment",
            value="positive",
            confidence=0.85,
            raw_text=text[:100],
        )]
    return []


# ── Fact → Action mapping ────────────────────────────────────────────── #


def _fact_to_action(
    fact: ExtractedFact,
    call_type: str,
    property_id: str,
) -> ActionTrigger | None:
    """Convert a fact into a concrete action.

    Args:
        fact: Extracted fact.
        call_type: Who was called.
        property_id: Property context.

    Returns:
        ActionTrigger or None if no action needed.
    """
    if fact.fact_type == "checkout_time":
        return ActionTrigger(
            action_type="schedule_cleaning",
            params={
                "property_id": property_id,
                "after_time": fact.value,
            },
            urgency="high",
            reason=f"Guest checkout at {fact.value}",
        )

    if fact.fact_type == "reported_issue":
        return ActionTrigger(
            action_type="dispatch_vendor",
            params={
                "property_id": property_id,
                "issue_type": fact.value,
            },
            urgency="high" if fact.value in ("water_issue", "lock_issue") else "medium",
            reason=f"Issue reported: {fact.value}",
        )

    if fact.fact_type == "quoted_price":
        return ActionTrigger(
            action_type="evaluate_cost",
            params={
                "property_id": property_id,
                "amount": fact.value,
                "source": call_type,
            },
            urgency="medium",
            reason=f"Cost quoted: {fact.value}",
        )

    if fact.fact_type == "availability" and fact.value == "unavailable":
        if call_type in ("cleaner", "vendor"):
            return ActionTrigger(
                action_type="try_next_contact",
                params={"property_id": property_id},
                urgency="high",
                reason=f"{call_type} unavailable — continue cascade",
            )

    if fact.fact_type == "cleaner_eta":
        return ActionTrigger(
            action_type="confirm_cleaner_schedule",
            params={
                "property_id": property_id,
                "eta": fact.value,
            },
            urgency="medium",
            reason=f"Cleaner arriving at {fact.value}",
        )

    if fact.fact_type == "vendor_eta":
        return ActionTrigger(
            action_type="notify_guest_vendor_eta",
            params={
                "property_id": property_id,
                "eta": fact.value,
            },
            urgency="medium",
            reason=f"Vendor arriving at {fact.value}",
        )

    if fact.fact_type == "sentiment" and fact.value == "negative":
        return ActionTrigger(
            action_type="escalate_complaint",
            params={"property_id": property_id},
            urgency="high",
            reason="Negative sentiment detected in call",
        )

    return None


def _fact_to_dict(fact: ExtractedFact) -> dict[str, Any]:
    """Convert ExtractedFact to serializable dict."""
    return {
        "fact_type": fact.fact_type,
        "value": fact.value,
        "confidence": fact.confidence,
        "source": fact.source,
    }


def _action_to_dict(action: ActionTrigger) -> dict[str, Any]:
    """Convert ActionTrigger to serializable dict."""
    return {
        "action_type": action.action_type,
        "params": action.params,
        "urgency": action.urgency,
        "reason": action.reason,
    }
