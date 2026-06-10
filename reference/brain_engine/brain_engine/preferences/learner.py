"""PreferenceLearner — Generates follow-up questions after approval decisions.

After an owner approves or denies an action, the learner generates
contextual questions to understand the owner's preferences:
- Scope: just this time, or always?
- Property: just this property, or all?
- Conditions: only for certain guests/situations?

These answers are converted into PreferenceRules and stored.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from brain_engine.approval.models import ApprovalRequest, ApprovalStatus
from brain_engine.preferences.models import LearningQuestion, RuleScope
from brain_engine.preferences.store import PreferenceStore

logger = logging.getLogger(__name__)

# Question templates per action type
QUESTION_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "late_checkout": [
        {
            "type": "scope",
            "text": (
                "You {decision} the late checkout. "
                "Should I handle it the same way in the future?"
            ),
            "options": [
                "Only this time",
                "Always for this property",
                "Always for all properties",
                "Only for guests with rating above 4.5",
            ],
        },
        {
            "type": "condition",
            "text": "Should I consider the checkout fee amount when deciding?",
            "options": [
                "No, always ask me",
                "Auto-approve if fee is under $50",
                "Auto-approve if fee is under $100",
                "Auto-approve any fee",
            ],
        },
    ],
    "call_cleaner": [
        {
            "type": "scope",
            "text": (
                "You {decision} calling the cleaner. "
                "Should I call cleaners automatically in the future?"
            ),
            "options": [
                "Always ask me first",
                "Call automatically for this property",
                "Call automatically for all properties",
            ],
        },
    ],
    "dispatch_cleaner": [
        {
            "type": "scope",
            "text": (
                "You {decision} dispatching the cleaner. "
                "Should I dispatch automatically next time?"
            ),
            "options": [
                "Always ask me first",
                "Dispatch automatically for scheduled cleanings",
                "Dispatch automatically always",
            ],
        },
    ],
    "submit_damage_claim": [
        {
            "type": "scope",
            "text": (
                "Damage claims are critical. Should I always ask your approval, "
                "or can I auto-submit for damages above a certain threshold?"
            ),
            "options": [
                "Always ask me (recommended)",
                "Auto-submit if damage < $100",
                "Auto-submit if damage < $500",
            ],
        },
    ],
    "call_vendor": [
        {
            "type": "scope",
            "text": (
                "You {decision} calling the vendor. "
                "Should I contact vendors automatically for repairs?"
            ),
            "options": [
                "Always ask me first",
                "Auto-call for urgent repairs only",
                "Auto-call for any repair needed",
            ],
        },
    ],
}

# Maps option text to (scope, conditions) for rule creation
OPTION_MAPPINGS: dict[str, tuple[RuleScope, dict[str, Any]]] = {
    "Only this time": (RuleScope.THIS_TIME, {}),
    "Always for this property": (RuleScope.THIS_PROPERTY, {}),
    "Always for all properties": (RuleScope.ALL_PROPERTIES, {}),
    "Only for guests with rating above 4.5": (
        RuleScope.CONDITIONAL,
        {"guest_rating_min": 4.5},
    ),
    "Always ask me first": (RuleScope.THIS_TIME, {}),
    "Call automatically for this property": (RuleScope.THIS_PROPERTY, {}),
    "Call automatically for all properties": (RuleScope.ALL_PROPERTIES, {}),
    "Dispatch automatically for scheduled cleanings": (
        RuleScope.CONDITIONAL,
        {"is_scheduled": True},
    ),
    "Dispatch automatically always": (RuleScope.ALWAYS, {}),
    "Always ask me (recommended)": (RuleScope.THIS_TIME, {}),
    "Auto-submit if damage < $100": (
        RuleScope.CONDITIONAL,
        {"estimated_cost_max": 100},
    ),
    "Auto-submit if damage < $500": (
        RuleScope.CONDITIONAL,
        {"estimated_cost_max": 500},
    ),
    "Auto-call for urgent repairs only": (
        RuleScope.CONDITIONAL,
        {"urgency_min": 4},
    ),
    "Auto-call for any repair needed": (RuleScope.ALWAYS, {}),
    "No, always ask me": (RuleScope.THIS_TIME, {}),
    "Auto-approve if fee is under $50": (
        RuleScope.CONDITIONAL,
        {"fee_max": 50},
    ),
    "Auto-approve if fee is under $100": (
        RuleScope.CONDITIONAL,
        {"fee_max": 100},
    ),
    "Auto-approve any fee": (RuleScope.ALWAYS, {}),
}


class PreferenceLearner:
    """Generates and processes follow-up questions for learning owner preferences.

    After each approval decision, generates 1-3 contextual questions.
    Owner answers are converted to PreferenceRules and stored.

    Args:
        preference_store: Store for saving learned rules.
        notifier: Notification backend for sending questions.
    """

    def __init__(
        self,
        preference_store: PreferenceStore,
        notifier: Any | None = None,
    ) -> None:
        self._store = preference_store
        self._notifier = notifier
        self._pending_questions: dict[str, LearningQuestion] = {}
        self._request_context: dict[str, ApprovalRequest] = {}

    def generate_questions(
        self,
        request: ApprovalRequest,
        approved: bool,
    ) -> list[LearningQuestion]:
        """Generate follow-up questions after an approval decision.

        Args:
            request: The original approval request.
            approved: Whether it was approved or denied.

        Returns:
            List of LearningQuestion objects to send to the owner.
        """
        action = request.action_type.value
        templates = QUESTION_TEMPLATES.get(action, [])
        decision_word = "approved" if approved else "denied"

        questions: list[LearningQuestion] = []
        for template in templates:
            question_id = f"Q-{uuid.uuid4().hex[:8].upper()}"
            text = template["text"].format(decision=decision_word)

            question = LearningQuestion(
                question_id=question_id,
                request_id=request.request_id,
                question_text=text,
                question_type=template["type"],
                options=template["options"],
            )
            questions.append(question)
            self._pending_questions[question_id] = question
            self._request_context[question_id] = request

        logger.info(
            "Generated %d learning questions for %s (request=%s)",
            len(questions), action, request.request_id,
        )
        return questions

    async def process_answer(
        self,
        question_id: str,
        answer: str,
        approved: bool,
    ) -> dict[str, Any]:
        """Process an owner's answer to a learning question.

        Converts the answer to a PreferenceRule and saves it.

        Args:
            question_id: ID of the question being answered.
            answer: The owner's selected option.
            approved: Whether the original action was approved.

        Returns:
            Dict with rule details or empty if no rule created.
        """
        question = self._pending_questions.get(question_id)
        request = self._request_context.get(question_id)

        if not question or not request:
            logger.warning("Question %s not found in pending", question_id)
            return {}

        question.answer = answer

        # Map answer to scope and conditions
        mapping = OPTION_MAPPINGS.get(answer)
        if not mapping:
            logger.warning("No mapping for answer: %s", answer)
            return {}

        scope, conditions = mapping

        # Don't create a rule for "this_time" scope
        if scope == RuleScope.THIS_TIME:
            self._cleanup_question(question_id)
            return {"scope": "this_time", "rule_created": False}

        # Create and save the rule
        rule = await self._store.save_rule(
            owner_id=request.owner_id,
            property_id=request.property_id,
            action_type=request.action_type.value,
            auto_approve=approved,
            scope=scope.value,
            conditions=conditions,
            created_from=request.request_id,
        )

        self._cleanup_question(question_id)

        logger.info(
            "Created rule %s from question %s: scope=%s, conditions=%s",
            rule.rule_id, question_id, scope, conditions,
        )
        return {
            "rule_id": rule.rule_id,
            "scope": scope.value,
            "conditions": conditions,
            "auto_approve": approved,
            "rule_created": True,
        }

    async def send_questions(
        self,
        questions: list[LearningQuestion],
        owner_id: str,
    ) -> None:
        """Send learning questions to the owner via notification channel."""
        if not self._notifier:
            return

        for question in questions:
            options_text = "\n".join(
                f"  {i + 1}. {opt}" for i, opt in enumerate(question.options)
            )
            message = (
                f"Quick question:\n\n"
                f"{question.question_text}\n\n"
                f"{options_text}\n\n"
                f"Reply with the number (1-{len(question.options)}) "
                f"or /skip to skip."
            )
            try:
                await self._notifier.send_message(
                    owner_id=owner_id,
                    text=message,
                )
            except Exception:
                logger.exception(
                    "Failed to send learning question %s",
                    question.question_id,
                )

    def get_pending_questions(self, request_id: str = "") -> list[LearningQuestion]:
        """Get pending questions, optionally filtered by request_id."""
        if request_id:
            return [
                q for q in self._pending_questions.values()
                if q.request_id == request_id
            ]
        return list(self._pending_questions.values())

    def _cleanup_question(self, question_id: str) -> None:
        """Remove a processed question from pending tracking."""
        self._pending_questions.pop(question_id, None)
        self._request_context.pop(question_id, None)
