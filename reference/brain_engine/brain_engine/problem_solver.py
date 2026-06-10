"""AI Problem Solver — LLM-powered reasoning for ANY property issue.

Instead of hardcoding every possible problem, this module uses GPT-4o
to analyze any situation and produce an action plan:
1. What is the problem?
2. How urgent is it (1-5)?
3. Can it be fixed remotely?
4. What type of vendor is needed?
5. What should we tell the guest right now?
6. What are the step-by-step actions?

Works with ANY problem description — from "the toilet is overflowing"
to "there's a strange smell in the bedroom" to "neighbor is too loud".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
You are an expert property manager AI for short-term rental apartments (Airbnb/Booking.com).

A problem has been reported at a rental property. Analyze it and produce an action plan.

PROPERTY CONTEXT:
{property_context}

PROBLEM REPORT:
{problem_description}

REPORTED BY: {reported_by}
CURRENT TIME: {current_time}
NEXT GUEST CHECK-IN: {next_checkin}

Respond in JSON with this exact structure:
{{
  "problem_summary": "one-line summary",
  "urgency": 1-5 (1=low, 5=emergency),
  "category": "plumbing|electrical|hvac|lock|appliance|pest|noise|structural|cleaning|safety|other",
  "is_safety_hazard": true/false,
  "can_fix_remotely": true/false,
  "remote_fix_steps": ["step1", "step2"] or [],
  "vendor_type_needed": "plumber|electrician|hvac_tech|locksmith|general_repair|pest_control|none",
  "guest_message": "what to tell the guest RIGHT NOW (be reassuring, give interim advice)",
  "owner_message": "what to tell the owner (brief, factual, what action is being taken)",
  "action_plan": [
    {{"step": 1, "action": "description", "who": "system|vendor|owner|guest", "priority": "immediate|soon|later"}},
    ...
  ],
  "interim_solution": "what guest can do while waiting for fix",
  "estimated_resolution_hours": number,
  "affects_checkin": true/false,
  "checkin_delay_minutes": 0 or estimated delay
}}
"""

FOLLOWUP_PROMPT = """\
The following problem was reported and partially handled:

ORIGINAL PROBLEM: {problem_summary}
ACTIONS TAKEN: {actions_taken}
CURRENT STATUS: {current_status}
REMAINING ISSUE: {remaining_issue}

What should we do next? Respond in JSON:
{{
  "next_action": "description of next step",
  "who_should_act": "system|vendor|owner|guest",
  "urgency": 1-5,
  "message_to_stakeholder": "what to tell them",
  "is_resolved": true/false
}}
"""


@dataclass(slots=True)
class ProblemAnalysis:
    """LLM-generated analysis of a property problem.

    Attributes:
        problem_summary: One-line summary.
        urgency: 1 (low) to 5 (emergency).
        category: Problem category for vendor matching.
        is_safety_hazard: Whether it's a safety issue.
        can_fix_remotely: Whether a remote fix is possible.
        remote_fix_steps: Steps for remote fix (if applicable).
        vendor_type_needed: Type of vendor to dispatch.
        guest_message: What to tell the guest immediately.
        owner_message: What to tell the owner.
        action_plan: Step-by-step action plan.
        interim_solution: What guest can do while waiting.
        estimated_resolution_hours: Estimated time to fix.
        affects_checkin: Whether it delays next guest.
        checkin_delay_minutes: Estimated delay for next guest.
        raw: Raw LLM response.
    """

    problem_summary: str = ""
    urgency: int = 3
    category: str = "other"
    is_safety_hazard: bool = False
    can_fix_remotely: bool = False
    remote_fix_steps: list[str] = field(default_factory=list)
    vendor_type_needed: str = "none"
    guest_message: str = ""
    owner_message: str = ""
    action_plan: list[dict[str, Any]] = field(default_factory=list)
    interim_solution: str = ""
    estimated_resolution_hours: float = 0
    affects_checkin: bool = False
    checkin_delay_minutes: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_summary": self.problem_summary,
            "urgency": self.urgency,
            "category": self.category,
            "is_safety_hazard": self.is_safety_hazard,
            "can_fix_remotely": self.can_fix_remotely,
            "remote_fix_steps": self.remote_fix_steps,
            "vendor_type_needed": self.vendor_type_needed,
            "guest_message": self.guest_message,
            "owner_message": self.owner_message,
            "action_plan": self.action_plan,
            "interim_solution": self.interim_solution,
            "estimated_resolution_hours": self.estimated_resolution_hours,
            "affects_checkin": self.affects_checkin,
            "checkin_delay_minutes": self.checkin_delay_minutes,
        }


class ProblemSolver:
    """LLM-powered problem solver for ANY property issue.

    Doesn't rely on hardcoded categories — sends the problem
    description to the tenant's Azure GPT-4o deployment and gets
    back a structured action plan.  No public-OpenAI fallback.

    Args:
        model: Model family to use (default: gpt-4o).  Maps to the
            tenant's matching Azure deployment at call time.
        knowledge_base: Optional property-specific knowledge.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        knowledge_base: str = "",
    ) -> None:
        self._model = model
        self._knowledge_base = knowledge_base

    def _has_credentials(self) -> bool:
        """Return ``True`` when the tenant Azure OpenAI config is live.

        No public-OpenAI fallback exists — the deterministic
        keyword-based analysis in :meth:`_fallback_analysis` covers
        environments where Azure is not configured.
        """
        try:
            from brain_engine.models.azure_routing import load_azure_openai_config
            return load_azure_openai_config().is_complete()
        except Exception:  # noqa: BLE001 — defensive
            return False

    def _build_async_client(self) -> Any:
        """Construct the Azure OpenAI client used for chat completions.

        Callers must guard with :meth:`_has_credentials` first — this
        method assumes the tenant's Azure OpenAI config is complete
        and raises through the routing helper otherwise.
        """
        from brain_engine.models.azure_routing import (
            build_async_azure_openai_client,
            load_azure_openai_config,
        )
        azure_config = load_azure_openai_config()
        # When the deployment slot was unset on the model field,
        # snap it to the tenant's chat deployment so callers
        # quoting "gpt-4o" land on the actual Azure resource.
        if not self._model or self._model in {"gpt-4o", "gpt-4o-mini"}:
            self._model = (
                azure_config.chat_deployment
                if self._model == "gpt-4o"
                else azure_config.chat_mini_deployment
            )
        return build_async_azure_openai_client(azure_config)

    async def analyze(
        self,
        problem_description: str,
        *,
        property_context: str = "",
        reported_by: str = "guest",
        current_time: str = "",
        next_checkin: str = "",
    ) -> ProblemAnalysis:
        """Analyze ANY problem and produce an action plan.

        Args:
            problem_description: Free-text description of the problem.
            property_context: Property details (address, amenities, etc.).
            reported_by: Who reported (guest, cleaner, sensor, neighbor).
            current_time: Current time for urgency calculation.
            next_checkin: When next guest arrives.

        Returns:
            ProblemAnalysis with structured action plan.
        """
        if not self._has_credentials():
            return self._fallback_analysis(problem_description)

        prompt = ANALYSIS_PROMPT.format(
            property_context=property_context or "Standard rental apartment",
            problem_description=problem_description,
            reported_by=reported_by,
            current_time=current_time or "now",
            next_checkin=next_checkin or "unknown",
        )

        try:
            client = self._build_async_client()

            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are an expert property management AI."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            data = json.loads(content)

            analysis = ProblemAnalysis(
                problem_summary=data.get("problem_summary", problem_description[:100]),
                urgency=data.get("urgency", 3),
                category=data.get("category", "other"),
                is_safety_hazard=data.get("is_safety_hazard", False),
                can_fix_remotely=data.get("can_fix_remotely", False),
                remote_fix_steps=data.get("remote_fix_steps", []),
                vendor_type_needed=data.get("vendor_type_needed", "none"),
                guest_message=data.get("guest_message", ""),
                owner_message=data.get("owner_message", ""),
                action_plan=data.get("action_plan", []),
                interim_solution=data.get("interim_solution", ""),
                estimated_resolution_hours=data.get("estimated_resolution_hours", 0),
                affects_checkin=data.get("affects_checkin", False),
                checkin_delay_minutes=data.get("checkin_delay_minutes", 0),
                raw=data,
            )

            logger.info(
                "Problem analyzed: '%s' → urgency=%d, category=%s, vendor=%s",
                analysis.problem_summary, analysis.urgency,
                analysis.category, analysis.vendor_type_needed,
            )
            return analysis

        except Exception:
            logger.exception("LLM problem analysis failed, using fallback")
            return self._fallback_analysis(problem_description)

    async def get_next_action(
        self,
        problem_summary: str,
        actions_taken: str,
        current_status: str,
        remaining_issue: str = "",
    ) -> dict[str, Any]:
        """Ask LLM what to do next for an ongoing problem.

        Args:
            problem_summary: Original problem.
            actions_taken: What's been done so far.
            current_status: Current state.
            remaining_issue: What's still unresolved.

        Returns:
            Dict with next_action, who_should_act, urgency, message.
        """
        if not self._has_credentials():
            return {
                "next_action": "Notify owner and wait for instructions",
                "who_should_act": "owner",
                "urgency": 3,
                "message_to_stakeholder": f"Issue update: {current_status}",
                "is_resolved": False,
            }

        prompt = FOLLOWUP_PROMPT.format(
            problem_summary=problem_summary,
            actions_taken=actions_taken,
            current_status=current_status,
            remaining_issue=remaining_issue or "waiting for resolution",
        )

        try:
            client = self._build_async_client()

            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are an expert property management AI."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"},
            )

            return json.loads(response.choices[0].message.content)

        except Exception:
            logger.exception("LLM follow-up analysis failed")
            return {
                "next_action": "Escalate to owner",
                "who_should_act": "owner",
                "urgency": 3,
                "is_resolved": False,
            }

    @staticmethod
    def _fallback_analysis(description: str) -> ProblemAnalysis:
        """Simple keyword-based fallback when LLM is unavailable."""
        desc_lower = description.lower()

        # Keyword-based urgency detection
        urgency = 3
        vendor = "general_repair"
        category = "other"

        emergency_words = {"flood", "fire", "gas", "leak", "smoke", "sparks", "electrical fire"}
        high_words = {"no water", "no electricity", "locked out", "broken lock", "no heat"}
        medium_words = {"ac", "air conditioning", "hot water", "wifi", "internet", "noise"}

        if any(w in desc_lower for w in emergency_words):
            urgency = 5
            category = "safety"
        elif any(w in desc_lower for w in high_words):
            urgency = 4
        elif any(w in desc_lower for w in medium_words):
            urgency = 2

        # Vendor type detection
        if any(w in desc_lower for w in ("water", "pipe", "leak", "toilet", "drain", "plumb")):
            vendor = "plumber"
            category = "plumbing"
        elif any(w in desc_lower for w in ("electric", "power", "outlet", "light", "switch")):
            vendor = "electrician"
            category = "electrical"
        elif any(w in desc_lower for w in ("ac", "heating", "air condition", "hvac", "cold", "hot")):
            vendor = "hvac_tech"
            category = "hvac"
        elif any(w in desc_lower for w in ("lock", "door", "key", "access")):
            vendor = "locksmith"
            category = "lock"
        elif any(w in desc_lower for w in ("pest", "bug", "cockroach", "mouse", "rat")):
            vendor = "pest_control"
            category = "pest"

        return ProblemAnalysis(
            problem_summary=description[:100],
            urgency=urgency,
            category=category,
            vendor_type_needed=vendor,
            guest_message="We're aware of the issue and working on it. We'll update you shortly.",
            owner_message=f"Issue reported: {description[:200]}. Urgency: {urgency}/5.",
            action_plan=[
                {"step": 1, "action": "Notify owner", "who": "system", "priority": "immediate"},
                {"step": 2, "action": f"Find {vendor}", "who": "system", "priority": "immediate"},
                {"step": 3, "action": "Dispatch vendor", "who": "vendor", "priority": "soon"},
                {"step": 4, "action": "Verify fix", "who": "system", "priority": "later"},
            ],
            interim_solution="We're looking into this. Please stay safe and we'll update you soon.",
        )
