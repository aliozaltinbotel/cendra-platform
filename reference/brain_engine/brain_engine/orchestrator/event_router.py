"""
Event Router - classifies intent and determines the execution flow.

Routes incoming events to the appropriate action pipeline based on
detected intent and current conversation state.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from brain_engine.orchestrator.action_executor import Action

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flow definition
# ---------------------------------------------------------------------------


@dataclass
class Flow:
    """
    Describes the execution plan for a single conversational turn.

    Produced by the router after intent classification, contains the
    actions to execute and optional response templates.
    """

    intent: str
    confidence: float = 0.0
    actions: list[Action] = field(default_factory=list)
    extracted_slots: dict[str, Any] = field(default_factory=dict)
    response_template: str | None = None
    requires_confirmation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Intent patterns (rule-based fallback; production would use the classifier)
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: list[tuple[str, list[str], float]] = [
    (
        "guest_checkin",
        [
            r"check.?in",
            r"access\s+code",
            r"door\s+code",
            r"unlock",
            r"key",
            r"arrive",
            r"arriving",
        ],
        0.85,
    ),
    (
        "guest_checkout",
        [
            r"check.?out",
            r"leaving",
            r"depart",
            r"check\s+out\s+time",
        ],
        0.85,
    ),
    (
        "damage_claim",
        [
            r"damage",
            r"broken",
            r"stain",
            r"scratch",
            r"claim",
            r"resolution",
            r"repair",
        ],
        0.80,
    ),
    (
        "cleaning_schedule",
        [
            r"clean",
            r"turnover",
            r"housekeep",
            r"schedule\s+clean",
            r"cleaner",
        ],
        0.80,
    ),
    (
        "call_guest",
        [
            r"call\s+guest",
            r"phone\s+call",
            r"call\s+them",
            r"ring\s+them",
            r"voice\s+call",
        ],
        0.85,
    ),
    (
        "send_message",
        [
            r"send\s+message",
            r"text\s+guest",
            r"whatsapp",
            r"telegram",
            r"notify\s+guest",
            r"message\s+them",
        ],
        0.85,
    ),
    (
        "reservation_lookup",
        [
            r"reservation",
            r"booking",
            r"guest\s+info",
            r"confirmation\s+code",
            r"look\s*up",
        ],
        0.80,
    ),
    (
        "photo_compare",
        [
            r"compare\s+photo",
            r"before.*after",
            r"photo\s+check",
            r"inspect\s+photo",
            r"damage\s+photo",
        ],
        0.80,
    ),
    (
        "general_inquiry",
        [
            r"help",
            r"what\s+can\s+you",
            r"how\s+do",
            r"tell\s+me",
        ],
        0.50,
    ),
]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class EventRouter:
    """
    Routes incoming events to execution flows.

    Uses pattern matching as a lightweight fallback; in production this
    would delegate to ``brain_engine.IntentClassifier`` for ML-based
    classification.

    Usage::

        router = EventRouter()
        flow = await router.route({
            "content": "Please send the guest their access code",
            "current_intent": None,
            "slots": {},
        })
        print(flow.intent, flow.actions)
    """

    def __init__(
        self,
        *,
        intent_patterns: list[tuple[str, list[str], float]] | None = None,
    ) -> None:
        self._patterns = intent_patterns or _INTENT_PATTERNS
        self._flow_builders: dict[str, Any] = {
            "guest_checkin": self._build_checkin_flow,
            "guest_checkout": self._build_checkout_flow,
            "damage_claim": self._build_damage_claim_flow,
            "cleaning_schedule": self._build_cleaning_flow,
            "call_guest": self._build_call_flow,
            "send_message": self._build_message_flow,
            "reservation_lookup": self._build_reservation_flow,
            "photo_compare": self._build_photo_compare_flow,
            "general_inquiry": self._build_general_flow,
        }

    async def route(self, event: dict[str, Any]) -> Flow:
        """Classify the event and produce an execution flow.

        Args:
            event: Dictionary with ``content``, ``current_intent``,
                ``slots``, and optionally ``memory``.

        Returns:
            A :class:`Flow` describing the actions and response plan.
        """
        content: str = event.get("content", "")
        current_intent = event.get("current_intent")
        slots: dict[str, Any] = event.get("slots", {})

        # Classify intent
        intent, confidence = self._classify(content)

        # If we have an ongoing intent and confidence is low, stay on it
        if current_intent and confidence < 0.6:
            intent = current_intent
            confidence = 0.6

        # Extract slots from the message
        extracted_slots = self._extract_slots(content, intent)

        # Build the flow
        builder = self._flow_builders.get(intent, self._build_general_flow)
        merged_slots = {**slots, **extracted_slots}
        flow = builder(intent, confidence, merged_slots)
        flow.extracted_slots = extracted_slots

        logger.info(
            "Routed to intent=%s (confidence=%.2f) with %d actions",
            flow.intent,
            flow.confidence,
            len(flow.actions),
        )
        return flow

    # ------------------------------------------------------------------
    # Intent classification (pattern-based)
    # ------------------------------------------------------------------

    def _classify(self, text: str) -> tuple[str, float]:
        """Match text against intent patterns and return best match."""
        text_lower = text.lower()
        best_intent = "general_inquiry"
        best_confidence = 0.0

        for intent, patterns, base_confidence in self._patterns:
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    if base_confidence > best_confidence:
                        best_intent = intent
                        best_confidence = base_confidence
                    break

        return best_intent, best_confidence

    # ------------------------------------------------------------------
    # Slot extraction
    # ------------------------------------------------------------------

    def _extract_slots(self, text: str, intent: str) -> dict[str, Any]:
        """Extract relevant slots from the user message."""
        slots: dict[str, Any] = {}

        # Phone number
        phone_match = re.search(r"\+?\d[\d\s\-]{8,15}\d", text)
        if phone_match:
            slots["phone"] = re.sub(r"[\s\-]", "", phone_match.group())

        # Reservation / confirmation code (pattern: letters + digits)
        res_match = re.search(r"\b[A-Z]{2,4}\d{4,10}[A-Z]?\b", text, re.IGNORECASE)
        if res_match:
            slots["reservation_id"] = res_match.group().upper()

        # Date patterns (simple ISO)
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if date_match:
            slots["date"] = date_match.group()

        # Dollar amount
        amount_match = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text)
        if amount_match:
            slots["amount"] = float(amount_match.group(1).replace(",", ""))

        return slots

    # ------------------------------------------------------------------
    # Flow builders
    # ------------------------------------------------------------------

    def _build_checkin_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions: list[Action] = []
        if slots.get("reservation_id"):
            actions.append(
                Action(
                    action_type="reservation_lookup",
                    params={"reservation_id": slots["reservation_id"]},
                )
            )
        actions.append(
            Action(
                action_type="generate_access_code",
                params={
                    "lock_id": slots.get("lock_id", ""),
                    "guest_name": slots.get("guest_name", "Guest"),
                },
            )
        )
        return Flow(
            intent=intent,
            confidence=confidence,
            actions=actions,
            response_template=(
                "Welcome! Your access code has been generated and sent. "
                "Please check your messages for check-in instructions."
            ),
        )

    def _build_checkout_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions = [
            Action(action_type="schedule_cleaning", params=slots),
            Action(action_type="lock_property", params=slots),
        ]
        return Flow(
            intent=intent,
            confidence=confidence,
            actions=actions,
            response_template=(
                "Thank you for staying with us! Checkout has been processed "
                "and cleaning has been scheduled."
            ),
        )

    def _build_damage_claim_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions: list[Action] = []
        if slots.get("reservation_id"):
            actions.append(
                Action(
                    action_type="reservation_lookup",
                    params={"reservation_id": slots["reservation_id"]},
                )
            )
        actions.append(
            Action(
                action_type="compare_photos",
                params=slots,
            )
        )
        actions.append(
            Action(
                action_type="submit_claim",
                params=slots,
                requires_confirmation=True,
            )
        )
        return Flow(
            intent=intent,
            confidence=confidence,
            actions=actions,
            requires_confirmation=True,
        )

    def _build_cleaning_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions = [
            Action(action_type="find_cleaners", params=slots),
            Action(
                action_type="schedule_cleaning",
                params=slots,
                requires_confirmation=True,
            ),
        ]
        return Flow(
            intent=intent,
            confidence=confidence,
            actions=actions,
            requires_confirmation=True,
        )

    def _build_call_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions = [
            Action(
                action_type="make_call",
                params={"phone": slots.get("phone", ""), "script": slots.get("script", "")},
                requires_confirmation=True,
            )
        ]
        return Flow(
            intent=intent,
            confidence=confidence,
            actions=actions,
            requires_confirmation=True,
        )

    def _build_message_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions = [
            Action(
                action_type="send_message",
                params={
                    "recipient": slots.get("phone", slots.get("chat_id", "")),
                    "text": slots.get("message_text", ""),
                },
            )
        ]
        return Flow(intent=intent, confidence=confidence, actions=actions)

    def _build_reservation_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions: list[Action] = []
        if slots.get("reservation_id"):
            actions.append(
                Action(
                    action_type="reservation_lookup",
                    params={"reservation_id": slots["reservation_id"]},
                )
            )
        return Flow(
            intent=intent,
            confidence=confidence,
            actions=actions,
            response_template=(
                "Here are the reservation details."
                if slots.get("reservation_id")
                else "Please provide the reservation confirmation code."
            ),
        )

    def _build_photo_compare_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        actions = [
            Action(
                action_type="compare_photos",
                params={
                    "before_path": slots.get("before_path", ""),
                    "after_path": slots.get("after_path", ""),
                },
            )
        ]
        return Flow(intent=intent, confidence=confidence, actions=actions)

    def _build_general_flow(
        self, intent: str, confidence: float, slots: dict[str, Any]
    ) -> Flow:
        return Flow(
            intent=intent,
            confidence=confidence,
            response_template=(
                "I can help you with check-ins, checkouts, cleaning schedules, "
                "damage claims, guest communication, and more. What would you like to do?"
            ),
        )
