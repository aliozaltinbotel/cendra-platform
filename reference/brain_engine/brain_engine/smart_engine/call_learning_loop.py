"""CallLearningLoop — learn from every voice call and message.

Connects call transcripts to the full learning pipeline:
1. Record interaction → EpisodicMemory
2. Update scores → ScoringEngine
3. Extract patterns → ProceduralMemory
4. Evolve skills → SkillEvolutionEngine
5. Build guest/cleaner profiles over time

The system gets SMARTER with every call:
- "Maria always says no on Mondays" → don't call her on Mondays
- "Property X always has AC issues" → preemptive vendor check
- "Guest prefers Spanish" → call in Spanish next time
- "Cleaner Sofia is fastest for this property" → call her first

Based on: Brain Engine Continual Learning Pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from brain_engine.smart_engine.call_result_processor import (
    CallResultProcessor,
    ExtractedFact,
)

logger = logging.getLogger(__name__)


@dataclass
class LearnedPattern:
    """A pattern learned from repeated call interactions.

    Attributes:
        pattern_type: Category (availability, preference, recurring_issue).
        entity_id: Who/what this pattern is about.
        description: Human-readable pattern description.
        confidence: How sure we are (0.0-1.0).
        evidence_count: Number of interactions supporting this.
        first_seen: When pattern was first detected.
        last_seen: When pattern was last confirmed.
    """

    pattern_type: str
    entity_id: str
    description: str
    confidence: float = 0.5
    evidence_count: int = 1
    first_seen: str = ""
    last_seen: str = ""


class CallLearningLoop:
    """Connects every call to the learning pipeline.

    After each call, this module:
    1. Processes transcript → extract facts
    2. Records to episodic memory (what happened)
    3. Updates scoring engine (how they performed)
    4. Detects patterns (recurring behaviors)
    5. Generates procedural rules (if pattern is strong)

    Args:
        call_processor: Transcript fact extractor.
        scoring_engine: For updating entity scores.
        memory: Cognitive memory system.
        property_id: Property context.
    """

    def __init__(
        self,
        call_processor: CallResultProcessor | None = None,
        scoring_engine: Any = None,
        memory: Any = None,
        property_id: str = "",
    ) -> None:
        self._processor = call_processor or CallResultProcessor(
            memory=memory, property_id=property_id,
        )
        self._scoring = scoring_engine
        self._memory = memory
        self._property_id = property_id
        self._patterns: dict[str, LearnedPattern] = {}
        self._interaction_count: int = 0

    async def process_call(
        self,
        transcript: str,
        call_type: str,
        contact_id: str,
        contact_name: str,
        call_outcome: str = "",
        call_duration: float = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Full learning loop for a completed call.

        Args:
            transcript: Full call transcript.
            call_type: Who was called (guest, cleaner, vendor, manager).
            contact_id: Unique ID of the person.
            contact_name: Human-readable name.
            call_outcome: Result if already classified.
            call_duration: Call duration in seconds.
            metadata: Additional context.

        Returns:
            Dict with facts, actions, patterns, memory updates.
        """
        self._interaction_count += 1

        # Step 1: Extract facts from transcript
        result = await self._processor.process(
            transcript, call_type, contact_id, contact_name,
        )

        # Step 2: Record to episodic memory
        await self._record_to_memory(
            transcript, call_type, contact_id,
            contact_name, call_outcome, call_duration,
            result.get("extracted_facts", []),
        )

        # Step 3: Update scoring
        await self._update_scores(
            contact_id, call_type, call_outcome,
            call_duration, result.get("extracted_facts", []),
        )

        # Step 4: Detect patterns
        new_patterns = self._detect_patterns(
            contact_id, call_type,
            result.get("extracted_facts", []),
        )

        # Step 5: Generate rules if patterns are strong
        rules = self._generate_rules(new_patterns)

        result["patterns"] = [_pattern_to_dict(p) for p in new_patterns]
        result["rules_generated"] = rules
        result["total_interactions"] = self._interaction_count

        return result

    async def _record_to_memory(
        self,
        transcript: str,
        call_type: str,
        contact_id: str,
        contact_name: str,
        outcome: str,
        duration: float,
        facts: list[dict[str, Any]],
    ) -> None:
        """Save call interaction to episodic memory.

        Args:
            transcript: Full transcript text.
            call_type: Type of contact.
            contact_id: Contact identifier.
            contact_name: Contact name.
            outcome: Call result.
            duration: Duration in seconds.
            facts: Extracted facts.
        """
        if not self._memory:
            return

        episode = {
            "type": "voice_call",
            "call_type": call_type,
            "contact_id": contact_id,
            "contact_name": contact_name,
            "property_id": self._property_id,
            "outcome": outcome,
            "duration_seconds": duration,
            "transcript_summary": _summarize_transcript(transcript),
            "facts": facts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            if hasattr(self._memory, "episodic") and self._memory.episodic:
                await self._memory.episodic.record(
                    session_id=f"call_{contact_id}_{self._interaction_count}",
                    event_type="voice_call",
                    data=episode,
                    property_id=self._property_id,
                )
            elif hasattr(self._memory, "record_interaction"):
                await self._memory.record_interaction(episode)
        except Exception as exc:
            logger.warning("Failed to record to memory: %s", exc)

    async def _update_scores(
        self,
        contact_id: str,
        call_type: str,
        outcome: str,
        duration: float,
        facts: list[dict[str, Any]],
    ) -> None:
        """Update scoring engine based on call outcome.

        Args:
            contact_id: Who was scored.
            call_type: Entity type for scoring.
            outcome: Call result (accepted, rejected, etc.).
            duration: Call duration.
            facts: Extracted facts for context.
        """
        if not self._scoring:
            return

        entity_type = _call_type_to_entity(call_type)
        event_type = _outcome_to_event(outcome, duration)

        try:
            await self._scoring.record_event(
                entity_id=contact_id,
                entity_type=entity_type,
                event_type=event_type,
                property_id=self._property_id,
                response_time=duration,
                metadata={"facts": facts},
            )
        except Exception as exc:
            logger.warning("Failed to update score: %s", exc)

    def _detect_patterns(
        self,
        contact_id: str,
        call_type: str,
        facts: list[dict[str, Any]],
    ) -> list[LearnedPattern]:
        """Detect recurring patterns from accumulated interactions.

        Args:
            contact_id: Entity to check patterns for.
            call_type: Context.
            facts: Current call facts.

        Returns:
            List of newly detected or strengthened patterns.
        """
        patterns: list[LearnedPattern] = []
        now = datetime.now(timezone.utc).isoformat()

        for fact in facts:
            pattern_key = f"{contact_id}:{fact.get('fact_type', '')}:{fact.get('value', '')}"
            existing = self._patterns.get(pattern_key)

            if existing:
                existing.evidence_count += 1
                existing.last_seen = now
                existing.confidence = min(
                    0.95,
                    0.5 + existing.evidence_count * 0.1,
                )
                if existing.evidence_count >= 3:
                    patterns.append(existing)
            else:
                pattern = LearnedPattern(
                    pattern_type=fact.get("fact_type", "unknown"),
                    entity_id=contact_id,
                    description=_build_pattern_desc(
                        contact_id, call_type, fact,
                    ),
                    first_seen=now,
                    last_seen=now,
                )
                self._patterns[pattern_key] = pattern

        return patterns

    def _generate_rules(
        self,
        patterns: list[LearnedPattern],
    ) -> list[dict[str, Any]]:
        """Generate procedural rules from strong patterns.

        Only generates rules when confidence is high enough
        (>= 0.7 and evidence_count >= 3).

        Args:
            patterns: Detected patterns.

        Returns:
            List of generated rule dicts.
        """
        rules: list[dict[str, Any]] = []

        for pattern in patterns:
            if pattern.confidence < 0.7:
                continue
            if pattern.evidence_count < 3:
                continue

            rule = {
                "rule_type": "learned_from_calls",
                "condition": pattern.pattern_type,
                "entity_id": pattern.entity_id,
                "action": _pattern_to_rule_action(pattern),
                "confidence": pattern.confidence,
                "evidence_count": pattern.evidence_count,
                "description": pattern.description,
            }
            rules.append(rule)
            logger.info(
                "New rule generated: %s (confidence=%.2f, evidence=%d)",
                pattern.description,
                pattern.confidence,
                pattern.evidence_count,
            )

        return rules

    @property
    def learned_patterns(self) -> list[LearnedPattern]:
        """All detected patterns."""
        return list(self._patterns.values())

    @property
    def strong_patterns(self) -> list[LearnedPattern]:
        """Patterns with high confidence (>= 0.7)."""
        return [
            p for p in self._patterns.values()
            if p.confidence >= 0.7
        ]


# ── Helpers ──────────────────────────────────────────────────────────── #


def _summarize_transcript(transcript: str) -> str:
    """Create a brief summary of the transcript.

    Args:
        transcript: Full transcript text.

    Returns:
        Summary (first and last user lines).
    """
    user_lines = [
        line.strip()[5:].strip()
        for line in transcript.split("\n")
        if line.strip().startswith("User:")
    ]
    if not user_lines:
        return "No user response"
    if len(user_lines) == 1:
        return user_lines[0][:200]
    return f"{user_lines[0][:100]} ... {user_lines[-1][:100]}"


def _call_type_to_entity(call_type: str) -> str:
    """Map call type to scoring entity type.

    Args:
        call_type: Who was called.

    Returns:
        Scoring entity type string.
    """
    mapping = {
        "cleaner": "cleaner",
        "vendor": "vendor",
        "guest": "guest",
        "manager": "manager",
    }
    return mapping.get(call_type, call_type)


def _outcome_to_event(outcome: str, duration: float) -> str:
    """Map call outcome to scoring event type.

    Args:
        outcome: Call result.
        duration: Call duration in seconds.

    Returns:
        Scoring event type.
    """
    if outcome == "accepted":
        return "accepted_fast" if duration < 300 else "accepted_slow"
    if outcome == "rejected":
        return "rejected"
    if outcome == "no_answer":
        return "no_answer"
    if outcome == "cost_quoted":
        return "confirmed_slow"
    return "no_answer"


def _build_pattern_desc(
    contact_id: str,
    call_type: str,
    fact: dict[str, Any],
) -> str:
    """Build a human-readable pattern description.

    Args:
        contact_id: Who the pattern is about.
        call_type: Context.
        fact: The recurring fact.

    Returns:
        Description string.
    """
    fact_type = fact.get("fact_type", "")
    value = fact.get("value", "")

    if fact_type == "availability" and value == "unavailable":
        return f"{call_type} '{contact_id}' is frequently unavailable"
    if fact_type == "reported_issue":
        return f"Property frequently has '{value}' issues"
    if fact_type == "sentiment" and value == "negative":
        return f"{call_type} '{contact_id}' often has negative interactions"
    if fact_type == "quoted_price":
        return f"{call_type} '{contact_id}' typically quotes around {value}"

    return f"Pattern: {contact_id} → {fact_type}={value}"


def _pattern_to_rule_action(pattern: LearnedPattern) -> str:
    """Convert a pattern to a rule action recommendation.

    Args:
        pattern: The learned pattern.

    Returns:
        Action recommendation string.
    """
    if "unavailable" in pattern.description:
        return "skip_contact_or_deprioritize"
    if "issue" in pattern.description:
        return "preemptive_vendor_check"
    if "negative" in pattern.description:
        return "escalate_to_manager"
    if "quotes" in pattern.description:
        return "set_cost_expectation"
    return "monitor_and_log"


def _pattern_to_dict(pattern: LearnedPattern) -> dict[str, Any]:
    """Serialize a pattern for API response."""
    return {
        "pattern_type": pattern.pattern_type,
        "entity_id": pattern.entity_id,
        "description": pattern.description,
        "confidence": pattern.confidence,
        "evidence_count": pattern.evidence_count,
    }
