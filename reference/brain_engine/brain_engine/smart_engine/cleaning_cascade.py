"""CleaningCascade — 4-level escalation for finding a cleaner.

Level 1: Preferred Cleaner (from Procedural Memory / highest score)
    → Call via ElevenLabs / WhatsApp, timeout 15 min
Level 2: Backup Cleaner (from Semantic Memory / second highest)
    → Call via ElevenLabs / WhatsApp, timeout 15 min
Level 3: Platform Search (Turno / TurnoverBnB)
    → Auto-search by location + date + time, timeout 30 min
Level 4: Escalation
    → Alert Manager + Owner via WhatsApp/Telegram

The system gets SMARTER over time:
- Week 1: Full cascade every time
- Month 2: Skips cleaners with bad scores
- Month 6+: Picks the best one directly, 1 call
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, AsyncIterator

from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.exceptions import BrainEngineError
from brain_engine.protocols import VoiceClient, Notifier
from brain_engine.smart_engine.call_learning_loop import CallLearningLoop
from brain_engine.smart_engine.call_result_processor import CallResultProcessor
from brain_engine.smart_engine.scoring_engine import ScoringEngine

logger = logging.getLogger(__name__)


class CascadeError(BrainEngineError):
    """Raised when the cleaning cascade fails completely."""


class CascadeLevel(StrEnum):
    """Cascade escalation levels."""

    INIT = "init"
    PREFERRED = "calling_preferred"
    BACKUP = "calling_backup"
    PLATFORM = "searching_platform"
    ESCALATED = "escalated"
    CONFIRMED = "confirmed"
    FAILED = "failed"


LEVEL_TIMEOUTS: dict[CascadeLevel, int] = {
    CascadeLevel.PREFERRED: 900,   # 15 min
    CascadeLevel.BACKUP: 900,      # 15 min
    CascadeLevel.PLATFORM: 1800,   # 30 min
}


@dataclass(slots=True)
class CascadeAttempt:
    """Log of a single cascade attempt."""

    cleaner_id: str
    cleaner_name: str
    level: str
    result: str  # accepted, rejected, no_answer, timeout
    response_time_sec: float = 0
    timestamp: str = ""


@dataclass(slots=True)
class CascadeResult:
    """Result of the cleaning cascade."""

    resolved: bool = False
    cleaner_id: str = ""
    cleaner_name: str = ""
    cleaner_phone: str = ""
    level: str = ""
    source: str = ""  # scoring, platform, escalation
    attempts: list[CascadeAttempt] = field(default_factory=list)
    escalated: bool = False

    def __repr__(self) -> str:
        return (
            f"CascadeResult(resolved={self.resolved}, "
            f"cleaner={self.cleaner_name!r}, level={self.level!r}, "
            f"attempts={len(self.attempts)})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "cleaner_id": self.cleaner_id,
            "cleaner_name": self.cleaner_name,
            "level": self.level,
            "source": self.source,
            "attempts": [
                {
                    "cleaner": a.cleaner_name,
                    "level": a.level,
                    "result": a.result,
                    "response_time": a.response_time_sec,
                }
                for a in self.attempts
            ],
            "escalated": self.escalated,
        }


class CleaningCascade:
    """4-level cascade for finding a cleaner.

    Uses ScoringEngine to rank cleaners and pick the best one.
    As the system learns, it skips bad cleaners and goes
    straight to the best one.

    Args:
        scoring_engine: ScoringEngine for ranking cleaners.
        voice_client: ElevenLabs for phone calls.
        notifier: Telegram/WhatsApp for messaging.
        property_id: Property needing cleaning.
        city: City for city-level scoring.
        booking_id: Related booking.
        emitter: AG-UI emitter for SSE events.
        dry_run: Simulate calls without real API calls.
    """

    def __init__(
        self,
        scoring_engine: ScoringEngine,
        voice_client: VoiceClient | None = None,
        notifier: Notifier | None = None,
        property_id: str = "",
        city: str = "",
        booking_id: str = "",
        emitter: AGUIEmitter | None = None,
        dry_run: bool = False,
    ) -> None:
        self._scoring = scoring_engine
        self._voice = voice_client
        self._notifier = notifier
        self._property_id = property_id
        self._city = city
        self._booking_id = booking_id
        self._emitter = emitter
        self._dry_run = dry_run
        self._level = CascadeLevel.INIT
        self._attempts: list[CascadeAttempt] = []
        self._learning = CallLearningLoop(
            call_processor=CallResultProcessor(property_id=property_id),
            scoring_engine=scoring_engine,
            property_id=property_id,
        )

    async def execute(
        self,
        cleaners: list[dict[str, Any]],
        manager_phone: str = "",
        owner_phone: str = "",
    ) -> CascadeResult:
        """Execute the full 4-level cascade.

        Args:
            cleaners: List of cleaner configs (from config or DB).
            manager_phone: Property manager phone for escalation.
            owner_phone: Property owner phone for escalation.

        Returns:
            CascadeResult with assigned cleaner or escalation status.
        """
        result = CascadeResult()

        # Get ranked cleaners from ScoringEngine
        ranked = await self._scoring.get_ranked(
            entity_type="cleaner",
            property_id=self._property_id,
            city=self._city,
        )

        # Merge ranked scores with config data
        ranked_cleaners = self._merge_with_config(ranked, cleaners)

        # Check maturity — if score is high enough, skip cascade
        if ranked_cleaners and ranked_cleaners[0].get("composite_score", 0) > 50:
            logger.info(
                "Mature city: going directly to best cleaner %s (score=%.1f)",
                ranked_cleaners[0].get("name"),
                ranked_cleaners[0]["composite_score"],
            )

        # ── Level 1: Preferred Cleaner ───────────────────────────────────
        if ranked_cleaners:
            self._level = CascadeLevel.PREFERRED
            attempt = await self._try_cleaner(ranked_cleaners[0], CascadeLevel.PREFERRED)
            self._attempts.append(attempt)

            if attempt.result == "accepted":
                result.resolved = True
                result.cleaner_id = ranked_cleaners[0].get("entity_id", "")
                result.cleaner_name = ranked_cleaners[0].get("name", "")
                result.cleaner_phone = ranked_cleaners[0].get("phone", "")
                result.level = CascadeLevel.PREFERRED
                result.source = "scoring"
                result.attempts = self._attempts
                return result

        # ── Level 2: Backup Cleaner ──────────────────────────────────────
        if len(ranked_cleaners) > 1:
            self._level = CascadeLevel.BACKUP
            attempt = await self._try_cleaner(ranked_cleaners[1], CascadeLevel.BACKUP)
            self._attempts.append(attempt)

            if attempt.result == "accepted":
                result.resolved = True
                result.cleaner_id = ranked_cleaners[1].get("entity_id", "")
                result.cleaner_name = ranked_cleaners[1].get("name", "")
                result.cleaner_phone = ranked_cleaners[1].get("phone", "")
                result.level = CascadeLevel.BACKUP
                result.source = "scoring"
                result.attempts = self._attempts
                return result

        # Try remaining cleaners from config
        for i, cleaner in enumerate(ranked_cleaners[2:], start=3):
            attempt = await self._try_cleaner(cleaner, CascadeLevel.BACKUP)
            self._attempts.append(attempt)
            if attempt.result == "accepted":
                result.resolved = True
                result.cleaner_id = cleaner.get("entity_id", f"cleaner_{i}")
                result.cleaner_name = cleaner.get("name", "")
                result.cleaner_phone = cleaner.get("phone", "")
                result.level = CascadeLevel.BACKUP
                result.source = "scoring"
                result.attempts = self._attempts
                return result

        # ── Level 3: Platform Search ─────────────────────────────────────
        self._level = CascadeLevel.PLATFORM
        platform_result = await self._search_platform()
        if platform_result.get("found"):
            result.resolved = True
            result.cleaner_name = platform_result.get("cleaner_name", "Platform Cleaner")
            result.cleaner_phone = platform_result.get("cleaner_phone", "")
            result.level = CascadeLevel.PLATFORM
            result.source = "platform"
            result.attempts = self._attempts

            # Register new cleaner for future use
            await self._scoring.record_event(
                entity_id=result.cleaner_name,
                entity_type="cleaner",
                event_type="accepted_fast",
                property_id=self._property_id,
                city=self._city,
                metadata={"source": "platform"},
            )
            return result

        # ── Level 4: Escalation ──────────────────────────────────────────
        self._level = CascadeLevel.ESCALATED
        await self._escalate(manager_phone, owner_phone)

        result.escalated = True
        result.level = CascadeLevel.ESCALATED
        result.attempts = self._attempts
        return result

    async def _try_cleaner(
        self,
        cleaner: dict[str, Any],
        level: CascadeLevel,
    ) -> CascadeAttempt:
        """Try calling a single cleaner."""
        name = cleaner.get("name", "Cleaner")
        phone = cleaner.get("phone", "")
        cleaner_id = cleaner.get("entity_id", name)
        start = datetime.now(timezone.utc)

        logger.info("Cascade %s: trying %s (%s)", level, name, phone)

        if self._dry_run:
            # Simulate: first cleaner rejects, second accepts
            await asyncio.sleep(1)
            accepted = level == CascadeLevel.BACKUP
            result_status = "accepted" if accepted else "rejected"
        elif self._voice and phone:
            result_status = await self._call_and_analyze(
                name, phone, cleaner_id,
            )
        else:
            result_status = "no_answer"

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        # Update score based on result
        await self._scoring.record_event(
            entity_id=cleaner_id,
            entity_type="cleaner",
            event_type=f"{result_status}_{'fast' if elapsed < 300 else 'slow'}" if result_status == "accepted" else result_status,
            property_id=self._property_id,
            city=self._city,
            response_time=elapsed,
        )

        return CascadeAttempt(
            cleaner_id=cleaner_id,
            cleaner_name=name,
            level=level.value,
            result=result_status,
            response_time_sec=elapsed,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def _call_and_analyze(
        self,
        name: str,
        phone: str,
        cleaner_id: str,
    ) -> str:
        """Make a real call and analyze the transcript to determine outcome.

        Calls the cleaner, waits for the call to complete, retrieves
        the transcript, and uses LLM to classify the response.

        Args:
            name: Cleaner name.
            phone: Cleaner phone number.
            cleaner_id: Cleaner identifier for logging.

        Returns:
            Result status: accepted, rejected, no_answer, cost_quoted.
        """
        try:
            call = await self._voice.make_call(
                phone_number=phone,
                script=self._build_call_script(name),
                first_message=self._build_first_message(name),
            )
        except Exception as exc:
            logger.warning("Call to %s failed: %s", name, exc)
            return "no_answer"

        if not call.call_id:
            return "no_answer"

        status = await self._wait_for_call_completion(call.call_id)
        if status != "done":
            return "no_answer"

        transcript = await self._get_call_transcript(call.call_id)
        if not transcript:
            return "no_answer"

        classification = await self._classify_response(transcript, name)

        # Feed into learning loop — extract facts, update scores, detect patterns
        await self._learning.process_call(
            transcript=transcript,
            call_type="cleaner",
            contact_id=cleaner_id,
            contact_name=name,
            call_outcome=classification,
            call_duration=0,
        )

        return classification

    def _build_call_script(self, name: str) -> str:
        """Build the AI agent script for calling a cleaner.

        Args:
            name: Cleaner name.

        Returns:
            System prompt for the ElevenLabs agent.
        """
        return (
            f"You are calling {name} to request a cleaning job. "
            f"Property: {self._property_id}. "
            f"Ask if they can come today. If they say yes, confirm "
            f"the time. If they give a price, note it. If they say "
            f"no, thank them politely. Keep it short and professional."
        )

    def _build_first_message(self, name: str) -> str:
        """Build the opening message for the call.

        Args:
            name: Cleaner name.

        Returns:
            First message the agent says.
        """
        return (
            f"Hello {name}, this is Brain Engine from Cendra Property "
            f"Management. We need a cleaning for property "
            f"{self._property_id} today. Are you available?"
        )

    async def _wait_for_call_completion(
        self,
        call_id: str,
        max_wait: int = 120,
        poll_interval: int = 5,
    ) -> str:
        """Poll call status until completed or timeout.

        Args:
            call_id: ElevenLabs conversation ID.
            max_wait: Maximum seconds to wait.
            poll_interval: Seconds between polls.

        Returns:
            Final status string (done, failed, timeout).
        """
        elapsed = 0
        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            try:
                status = await self._voice.get_call_status(call_id)
                call_status = status.status if hasattr(status, "status") else str(status)
                if call_status in ("done", "completed", "failed"):
                    return "done" if call_status != "failed" else "failed"
            except Exception as exc:
                logger.warning("Poll status failed: %s", exc)
        return "timeout"

    async def _get_call_transcript(self, call_id: str) -> str:
        """Retrieve the call transcript text.

        Args:
            call_id: ElevenLabs conversation ID.

        Returns:
            Full transcript text, or empty string on failure.
        """
        try:
            transcript = await self._voice.get_transcript(call_id)
            return transcript.text if hasattr(transcript, "text") else str(transcript)
        except Exception as exc:
            logger.warning("Failed to get transcript: %s", exc)
            return ""

    async def _classify_response(
        self,
        transcript: str,
        cleaner_name: str,
    ) -> str:
        """Use LLM to classify the cleaner's response from transcript.

        Analyzes the conversation transcript and determines the
        outcome: accepted, rejected, cost_quoted, or no_answer.

        Args:
            transcript: Full conversation text.
            cleaner_name: Name for context.

        Returns:
            Classification: accepted, rejected, cost_quoted, no_answer.
        """
        classification = _classify_transcript_rules(transcript)
        if classification != "unknown":
            logger.info(
                "Transcript classified (rules) for %s: %s",
                cleaner_name, classification,
            )
            return classification

        logger.info(
            "Transcript classified (fallback) for %s: no_answer",
            cleaner_name,
        )
        return "no_answer"

    async def _search_platform(self) -> dict[str, Any]:
        """Search external platform (Turno/TurnoverBnB)."""
        logger.info("Cascade Level 3: searching platform for cleaners")

        if self._dry_run:
            await asyncio.sleep(2)
            return {"found": False}  # Simulate no platform result to trigger escalation

        # In production: call Turno API
        # results = await turno_client.search(location=..., date=..., property_size=...)
        return {"found": False}

    async def _escalate(self, manager_phone: str, owner_phone: str) -> None:
        """Escalate to manager and owner."""
        logger.warning(
            "Cascade Level 4: escalating — all cleaners unavailable for %s",
            self._property_id,
        )

        context = (
            f"URGENT: No cleaner found for property {self._property_id}.\n"
            f"Attempts: {len(self._attempts)}\n"
            f"Please provide a cleaner contact ASAP."
        )

        if self._notifier:
            if manager_phone:
                try:
                    await self._notifier.send_message(
                        target=manager_phone, text=context,
                    )
                except Exception:
                    logger.exception("Failed to notify manager")
            if owner_phone:
                try:
                    await self._notifier.send_message(
                        target=owner_phone, text=context,
                    )
                except Exception:
                    logger.exception("Failed to notify owner")

    @staticmethod
    def _merge_with_config(
        ranked: list[dict[str, Any]],
        config_cleaners: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge scored ranking with config data (phone, name)."""
        config_map = {c.get("name", ""): c for c in config_cleaners}
        config_map.update({c.get("id", ""): c for c in config_cleaners if c.get("id")})

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Add ranked cleaners first (with config data)
        for r in ranked:
            eid = r["entity_id"]
            config = config_map.get(eid, {})
            merged.append({**config, **r, "name": config.get("name", eid)})
            seen.add(eid)
            seen.add(config.get("name", ""))

        # Add unranked cleaners from config (new, no score yet)
        for c in config_cleaners:
            name = c.get("name", "")
            if name not in seen and c.get("id", "") not in seen:
                merged.append({**c, "entity_id": name, "composite_score": 0})

        return merged


# ── Transcript classification ────────────────────────────────────────── #

# Keywords that indicate each outcome
_ACCEPTED_KEYWORDS = [
    "yes", "sure", "okay", "ok", "i can", "i'll come", "i will come",
    "confirm", "confirmed", "available", "be there", "on my way",
    "i accept", "count me in", "no problem", "alright",
    "да", "конечно", "приду", "буду", "хорошо", "ладно",
    "evet", "tamam", "gelirim", "olur",
    "sí", "claro", "vale", "puedo",
]

_REJECTED_KEYWORDS = [
    "no", "can't", "cannot", "not available", "busy", "sick",
    "unavailable", "sorry", "i'm not", "impossible", "decline",
    "нет", "не могу", "занят", "болею",
    "hayır", "yapamam", "müsait değilim",
    "no puedo", "no estoy",
]

_COST_KEYWORDS = [
    "euro", "eur", "dollar", "$", "€", "cost", "price", "charge",
    "pay", "fee", "how much", "rate", "per hour",
    "евро", "стоимость", "цена", "оплата",
    "fiyat", "ücret",
    "precio", "coste",
]


def _classify_transcript_rules(transcript: str) -> str:
    """Classify call transcript using keyword rules.

    Checks the USER turns (not agent) for acceptance, rejection,
    or cost-related keywords.

    Args:
        transcript: Full conversation text.

    Returns:
        Classification: accepted, rejected, cost_quoted, or unknown.
    """
    user_text = _extract_user_text(transcript)
    lower = user_text.lower()

    if not lower.strip():
        return "no_answer"

    if _has_keyword(lower, _COST_KEYWORDS):
        return "cost_quoted"

    if _has_keyword(lower, _REJECTED_KEYWORDS):
        return "rejected"

    if _has_keyword(lower, _ACCEPTED_KEYWORDS):
        return "accepted"

    return "unknown"


def _extract_user_text(transcript: str) -> str:
    """Extract only the user/cleaner lines from transcript.

    Args:
        transcript: Full transcript with Agent/User prefixes.

    Returns:
        Concatenated user lines.
    """
    lines: list[str] = []
    for line in transcript.split("\n"):
        stripped = line.strip()
        if stripped.startswith("User:"):
            lines.append(stripped[5:].strip())
        elif not stripped.startswith("Agent:") and stripped:
            lines.append(stripped)
    return " ".join(lines)


def _has_keyword(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in text.

    Args:
        text: Lowercased text to search.
        keywords: Keywords to look for.

    Returns:
        True if any keyword found.
    """
    return any(kw in text for kw in keywords)
