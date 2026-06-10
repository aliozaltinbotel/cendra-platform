"""Skill Evolution Engine — Memento-Skills approach for Brain Engine.

Based on: Memento-Skills (arXiv:2603.18743).
Result: +116% on HLE with frozen weights.

Cycle: READ -> REFLECT -> WRITE -> VERIFY

Skills are stored in ProceduralMemory as structured Procedure objects.
LLM weights stay FROZEN. Only skills (ProceduralMemory) evolve.
Zero catastrophic forgetting. Zero training cost.

Evolution triggers:
    FAILURE: owner rejected, guest unsatisfied, low grader score
        -> READ closest skill -> REFLECT via LLM -> WRITE updated skill -> VERIFY
    SUCCESS: resolved without escalation, high grader score
        -> Reinforce: success_count += 1, confidence += 0.05
    NIGHTLY: aggregate day's failures into batch evolution
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────── #


@dataclass
class SkillEvolutionResult:
    """Result of a skill evolution attempt.

    Attributes:
        status: 'evolved', 'reinforced', 'created', 'blocked', or 'no_match'.
        skill_id: ID of the affected skill.
        skill_name: Name of the affected skill.
        change_description: What changed and why.
        new_confidence: Updated confidence value.
        requires_human_review: Whether a human should verify.
        contradiction_details: Details if blocked by contradiction.
    """

    status: str
    skill_id: str = ""
    skill_name: str = ""
    change_description: str = ""
    new_confidence: float = 0.0
    requires_human_review: bool = False
    contradiction_details: str = ""


@dataclass
class FailureReflection:
    """LLM analysis of why an action failed.

    Attributes:
        summary: One-line summary of the failure.
        root_cause: Root cause analysis.
        missing_trigger: What trigger condition was missing.
        wrong_action: What action was wrong and why.
        correct_action: What action should be taken instead.
        new_trigger_events: Events that should trigger this skill.
        new_trigger_context: Context conditions for the trigger.
        applicable_conditions: When this correction should apply.
    """

    summary: str = ""
    root_cause: str = ""
    missing_trigger: str = ""
    wrong_action: str = ""
    correct_action: str = ""
    new_trigger_events: list[str] | None = None
    new_trigger_context: dict[str, Any] | None = None
    applicable_conditions: str = ""


# ── Skill Evolution Engine ──────────────────────────────────────────── #


class SkillEvolutionEngine:
    """Evolves procedural skills through the Read-Reflect-Write-Verify cycle.

    Uses frozen LLM weights to analyze failures and update skills
    stored in ProceduralMemory. No training or fine-tuning.

    Args:
        procedural_memory: Procedural memory store (Redis-backed).
        guardrail_pipeline: Guardrail pipeline for skill validation.
        llm_model: LLM model identifier for reflection calls.
    """

    def __init__(
        self,
        procedural_memory: Any,
        guardrails: Any,
        llm_model: str = "gpt-4o-mini",
        completion: Callable[[str], str] | None = None,
    ) -> None:
        self._memory = procedural_memory
        self._guardrails = guardrails
        self._llm_model = llm_model
        # completion: prompt -> model text (litellm retired; Dify
        # llm_generator adapter binds here)
        self._completion = completion
        self._evolution_log: list[dict[str, Any]] = []

    @property
    def evolution_count(self) -> int:
        """Total evolutions performed in this session."""
        return len(self._evolution_log)

    def get_evolution_count(self, days: int = 30) -> int:
        """Get the number of evolutions (session-scoped).

        Args:
            days: Lookback period (currently session-scoped).

        Returns:
            Number of skill evolutions.
        """
        return len(self._evolution_log)

    # ── Public: Evolve on Failure ───────────────────────────────────── #

    def evolve_on_failure(
        self,
        event_type: str,
        event_description: str,
        action_taken: str,
        failure_reason: str,
        context: dict[str, Any],
    ) -> SkillEvolutionResult:
        """Trigger skill evolution when an action fails.

        Full cycle: READ -> REFLECT -> WRITE -> VERIFY.

        Args:
            event_type: Type of the triggering event.
            event_description: Human-readable event description.
            action_taken: What action was taken.
            failure_reason: Why it failed.
            context: Full context of the interaction.

        Returns:
            SkillEvolutionResult with status and details.
        """
        # READ: find closest existing skill
        existing = self._memory.find_best_match(event_type, context)

        # REFLECT: LLM analyzes the failure
        reflection = self._reflect(
            event_description,
            action_taken,
            failure_reason,
            existing,
            context,
        )

        # WRITE: create or update the skill
        if existing:
            skill = self._update_skill(existing, reflection, event_type)
        else:
            skill = self._create_skill(event_type, reflection)

        # Blocked by manual/immutable conflict
        if skill is None:
            result = SkillEvolutionResult(
                status="blocked",
                change_description=("Blocked: contradicts manual/immutable rule"),
                requires_human_review=False,
            )
            self._log_evolution(event_type, result)
            return result

        # VERIFY: check for contradictions
        result = self._verify(skill, reflection)
        self._log_evolution(event_type, result)
        return result

    # ── Public: Reinforce on Success ────────────────────────────────── #

    def evolve_on_success(
        self,
        event_type: str,
        context: dict[str, Any],
    ) -> SkillEvolutionResult:
        """Reinforce a skill that led to a successful outcome.

        Increments success_count and boosts confidence.

        Args:
            event_type: Type of the triggering event.
            context: Interaction context.

        Returns:
            SkillEvolutionResult with reinforcement status.
        """
        existing = self._memory.find_best_match(event_type, context)
        if not existing:
            return SkillEvolutionResult(status="no_match")

        existing.success_count += 1
        old_conf = existing.confidence
        existing.confidence = min(1.0, existing.confidence + 0.05)
        existing.last_used = datetime.now(UTC).isoformat()

        self._memory._redis.set(
            self._memory._key(existing.procedure_id),
            json.dumps(existing.to_dict()),
        )

        result = SkillEvolutionResult(
            status="reinforced",
            skill_id=existing.procedure_id,
            skill_name=existing.name,
            change_description=(
                f"Success reinforced: confidence {old_conf:.2f} -> "
                f"{existing.confidence:.2f}, "
                f"successes={existing.success_count}"
            ),
            new_confidence=existing.confidence,
        )
        self._log_evolution(event_type, result)
        return result

    # ── READ step ───────────────────────────────────────────────────── #
    # (handled inline — just calls self._memory.find_best_match)

    # ── REFLECT step ────────────────────────────────────────────────── #

    def _reflect(
        self,
        event_description: str,
        action_taken: str,
        failure_reason: str,
        existing_skill: Any | None,
        context: dict[str, Any],
    ) -> FailureReflection:
        """Use LLM to deeply analyze why an action failed.

        Extracts: root cause, missing triggers, correct action,
        new trigger conditions.

        Args:
            event_description: What happened.
            action_taken: What the engine did.
            failure_reason: Why it was wrong.
            existing_skill: The skill that was applied (if any).
            context: Full context.

        Returns:
            FailureReflection with structured analysis.
        """
        skill_text = self._format_skill_for_prompt(existing_skill)
        context_text = json.dumps(context, default=str)[:800]

        prompt = _REFLECTION_PROMPT.format(
            event=event_description,
            action=action_taken,
            failure=failure_reason,
            skill=skill_text,
            context=context_text,
        )

        if self._completion is None:
            return FailureReflection(
                summary=f"Action '{action_taken}' failed: {failure_reason}",
                root_cause=failure_reason,
                correct_action="investigate_further",
            )
        try:
            text = self._completion(f"{_REFLECTION_SYSTEM}\n\n{prompt}") or ""
            return self._parse_reflection_json(text)
        except Exception:
            logger.error("Reflection LLM call failed", exc_info=True)
            return FailureReflection(
                summary=f"Action '{action_taken}' failed: {failure_reason}",
                root_cause=failure_reason,
                correct_action="investigate_further",
            )

    @staticmethod
    def _format_skill_for_prompt(skill: Any | None) -> str:
        """Format existing skill as text for the reflection prompt.

        Args:
            skill: The existing Procedure or None.

        Returns:
            Formatted skill text.
        """
        if not skill:
            return "None (no matching skill exists)"

        trigger = getattr(skill, "trigger_conditions", {})
        actions = getattr(skill, "actions", [])
        return (
            f"Name: {skill.name}\n"
            f"Description: {skill.description}\n"
            f"Triggers: {json.dumps(trigger, default=str)}\n"
            f"Actions: {json.dumps(actions)}\n"
            f"Confidence: {skill.confidence:.2f}\n"
            f"Success/Fail: {skill.success_count}/{skill.failure_count}"
        )

    @staticmethod
    def _parse_reflection_json(text: str) -> FailureReflection:
        """Parse structured JSON from LLM reflection.

        Args:
            text: Raw LLM response (JSON).

        Returns:
            Parsed FailureReflection.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    return FailureReflection(summary=text[:200])
            else:
                return FailureReflection(summary=text[:200])

        return FailureReflection(
            summary=data.get("summary", ""),
            root_cause=data.get("root_cause", ""),
            missing_trigger=data.get("missing_trigger", ""),
            wrong_action=data.get("wrong_action", ""),
            correct_action=data.get("correct_action", ""),
            new_trigger_events=data.get("new_trigger_events"),
            new_trigger_context=data.get("new_trigger_context"),
            applicable_conditions=data.get("applicable_conditions", ""),
        )

    # ── WRITE step ──────────────────────────────────────────────────── #

    def _update_skill(
        self,
        skill: Any,
        reflection: FailureReflection,
        event_type: str,
    ) -> Any:
        """Update an existing skill with failure learnings.

        Updates trigger conditions, actions, confidence, and metadata.

        Args:
            skill: The existing Procedure to update.
            reflection: Failure analysis.
            event_type: Event type for context.

        Returns:
            Updated Procedure (already saved to Redis).
        """
        # Update failure count and confidence
        skill.failure_count += 1
        skill.confidence = max(0.1, skill.confidence - 0.1)

        # Update trigger conditions if reflection provides new ones
        self._merge_trigger_conditions(skill, reflection, event_type)

        # Update actions
        self._merge_actions(skill, reflection)

        # Update description with evolution history
        evolution_note = f" [Evolved {datetime.now(UTC).strftime('%Y-%m-%d')}: {reflection.summary[:100]}]"
        if len(skill.description) + len(evolution_note) < 500:
            skill.description += evolution_note

        skill.source = "evolved"
        skill.last_used = datetime.now(UTC).isoformat()

        # Save back to Redis
        self._memory._redis.set(
            self._memory._key(skill.procedure_id),
            json.dumps(skill.to_dict()),
        )

        logger.info(
            "Updated skill: %s confidence=%.2f actions=%s",
            skill.name,
            skill.confidence,
            skill.actions,
        )
        return skill

    @staticmethod
    def _merge_trigger_conditions(
        skill: Any,
        reflection: FailureReflection,
        event_type: str,
    ) -> None:
        """Merge new trigger conditions from reflection into skill.

        Args:
            skill: The skill to update.
            reflection: The failure analysis.
            event_type: Current event type.
        """
        triggers = skill.trigger_conditions

        # Add new trigger events
        if reflection.new_trigger_events:
            existing_events = set(triggers.get("events", []))
            existing_events.update(reflection.new_trigger_events)
            triggers["events"] = sorted(existing_events)

        # Ensure current event_type is in triggers
        events = triggers.get("events", [])
        if event_type not in events:
            events.append(event_type)
            triggers["events"] = events

        # Merge new context requirements
        if reflection.new_trigger_context:
            existing_ctx = triggers.get("required_context", {})
            existing_ctx.update(reflection.new_trigger_context)
            triggers["required_context"] = existing_ctx

        # Add missing trigger note
        if reflection.missing_trigger:
            notes = triggers.get("evolution_notes", [])
            notes.append(reflection.missing_trigger)
            triggers["evolution_notes"] = notes[-5:]  # keep last 5

        skill.trigger_conditions = triggers

    @staticmethod
    def _merge_actions(
        skill: Any,
        reflection: FailureReflection,
    ) -> None:
        """Merge correct action from reflection into skill's actions.

        If the reflection identifies a wrong action, remove it.
        Add the correct action if not already present.

        Args:
            skill: The skill to update.
            reflection: The failure analysis.
        """
        actions = list(skill.actions)

        # Remove wrong action if identified
        if reflection.wrong_action and reflection.wrong_action in actions:
            actions.remove(reflection.wrong_action)

        # Add correct action
        if reflection.correct_action and reflection.correct_action not in actions:
            actions.insert(0, reflection.correct_action)

        skill.actions = actions

    def _check_manual_conflict(
        self,
        event_type: str,
        actions: list[str],
    ) -> str:
        """Check if a learned rule conflicts with manual/immutable rules.

        Args:
            event_type: Event type for trigger overlap check.
            actions: Proposed actions for the new learned rule.

        Returns:
            Conflict description or empty string if no conflict.
        """
        all_procs = self._memory.get_all_procedures()
        for proc in all_procs:
            if proc.source not in ("manual", "immutable"):
                continue
            if not getattr(proc, "active", True):
                continue

            proc_triggers = set(
                proc.trigger_conditions.get("events", []),
            )
            # Check category match (manual rules use category as trigger)
            proc_category = getattr(proc, "category", "")
            if proc_category:
                proc_triggers.add(proc_category)

            if event_type not in proc_triggers:
                continue

            # Overlapping trigger — check for opposing actions
            proc_actions = set(getattr(proc, "actions", []))
            for action in actions:
                opposites = _get_opposite_actions(action)
                conflicts = opposites.intersection(proc_actions)
                if conflicts:
                    return (
                        f"Contradicts {proc.source} rule "
                        f"'{proc.name}': opposing actions "
                        f"{conflicts} on trigger '{event_type}'"
                    )

            # Even without opposing actions, manual rule on same trigger
            # means learned rule should not override
            if proc.source in ("manual", "immutable") and proc_category == event_type:
                rule_text = getattr(proc, "rule", "")
                if rule_text:
                    return f"Manual/immutable rule exists for '{event_type}': '{rule_text[:80]}'"

        return ""

    def _create_skill(
        self,
        event_type: str,
        reflection: FailureReflection,
    ) -> Any:
        """Create a brand new skill from failure analysis.

        Checks for conflicts with manual/immutable rules before creation.

        Args:
            event_type: Event type for the trigger.
            reflection: Failure analysis.

        Returns:
            New Procedure (saved to Redis via add_procedure), or None
            if blocked by manual/immutable conflict.
        """
        actions = []
        if reflection.correct_action:
            actions.append(reflection.correct_action)

        # Check conflict with manual/immutable rules
        conflict = self._check_manual_conflict(event_type, actions)
        if conflict:
            logger.info(
                "Skipping learned rule creation — %s",
                conflict,
            )
            return None

        trigger_events = reflection.new_trigger_events or [event_type]
        trigger_conditions: dict[str, Any] = {"events": trigger_events}

        if reflection.new_trigger_context:
            trigger_conditions["required_context"] = reflection.new_trigger_context

        if reflection.applicable_conditions:
            trigger_conditions["notes"] = reflection.applicable_conditions

        name = f"evolved_{event_type}_{self.evolution_count}"
        description = reflection.summary or f"Learned from {event_type} failure"

        if reflection.root_cause:
            description += f" Root cause: {reflection.root_cause}"

        skill = self._memory.add_procedure(
            name=name,
            description=description[:300],
            trigger_conditions=trigger_conditions,
            actions=actions,
            source="evolved",
            tags=["evolved", event_type],
            confidence=0.6,
        )

        logger.info(
            "Created new skill: %s triggers=%s actions=%s",
            name,
            trigger_events,
            actions,
        )
        return skill

    # ── VERIFY step ─────────────────────────────────────────────────── #

    def _verify(
        self,
        skill: Any,
        reflection: FailureReflection,
    ) -> SkillEvolutionResult:
        """Verify the new/updated skill against all existing skills.

        Checks for:
        1. Direct action contradictions (approve vs reject same trigger)
        2. Overlapping triggers with opposing actions
        3. Guardrail violations

        Args:
            skill: The skill to verify.
            reflection: The failure reflection.

        Returns:
            SkillEvolutionResult.
        """
        existing_skills = self._memory.get_all_procedures()

        contradiction = self._find_contradiction(skill, existing_skills)
        if contradiction:
            logger.warning(
                "Skill evolution blocked — contradiction: %s",
                contradiction,
            )
            return SkillEvolutionResult(
                status="blocked",
                skill_id=getattr(skill, "procedure_id", ""),
                skill_name=getattr(skill, "name", ""),
                change_description=reflection.summary,
                requires_human_review=True,
                contradiction_details=contradiction,
            )

        # Guardrail check
        guardrail_ok = self._check_guardrails(skill)
        if not guardrail_ok:
            return SkillEvolutionResult(
                status="blocked",
                skill_id=getattr(skill, "procedure_id", ""),
                change_description="Guardrail validation failed",
                requires_human_review=True,
            )

        skill_id = getattr(skill, "procedure_id", "")
        skill_name = getattr(skill, "name", "")
        confidence = getattr(skill, "confidence", 0.5)

        return SkillEvolutionResult(
            status="evolved" if reflection.summary else "created",
            skill_id=skill_id,
            skill_name=skill_name,
            change_description=reflection.summary,
            new_confidence=confidence,
        )

    @staticmethod
    def _find_contradiction(
        new_skill: Any,
        existing: list[Any],
    ) -> str:
        """Find contradictions between new skill and existing skills.

        Checks for:
        - Same trigger events with directly opposing actions
        - approve_X vs reject_X patterns
        - send_message vs cancel_message patterns

        Args:
            new_skill: The skill being added/updated.
            existing: All existing skills.

        Returns:
            Contradiction description or empty string.
        """
        new_actions = set(getattr(new_skill, "actions", []))
        new_triggers = set(
            getattr(new_skill, "trigger_conditions", {}).get("events", []),
        )
        new_id = getattr(new_skill, "procedure_id", "")

        for skill in existing:
            if getattr(skill, "procedure_id", "") == new_id:
                continue
            if not getattr(skill, "active", True):
                continue

            skill_triggers = set(
                getattr(skill, "trigger_conditions", {}).get("events", []),
            )
            # Include category as trigger for manual/immutable rules
            skill_category = getattr(skill, "category", "")
            if skill_category:
                skill_triggers.add(skill_category)

            # Only check skills with overlapping triggers
            overlap = new_triggers.intersection(skill_triggers)
            if not overlap:
                continue

            skill_actions = set(getattr(skill, "actions", []))

            # Check for opposing action patterns
            for action in new_actions:
                opposites = _get_opposite_actions(action)
                conflicts = opposites.intersection(skill_actions)
                if conflicts:
                    source = getattr(skill, "source", "unknown")
                    return (
                        f"Skill '{getattr(skill, 'name', '?')}' "
                        f"(source={source}) has opposing action(s) "
                        f"{conflicts} for same trigger(s) {overlap}"
                    )

        return ""

    def _check_guardrails(self, skill: Any) -> bool:
        """Run guardrail validation on a skill's actions.

        Args:
            skill: The skill to validate.

        Returns:
            True if all actions pass guardrails.
        """
        if not self._guardrails:
            return True

        for action in getattr(skill, "actions", []):
            try:
                result = self._guardrails.validate_action(
                    action,
                    getattr(skill, "trigger_conditions", {}),
                )
                if not result.passed:
                    logger.warning(
                        "Guardrail blocked skill action: %s reason=%s",
                        action,
                        result.failures,
                    )
                    return False
            except Exception:  # noqa: S110 - guardrail check is best-effort by contract
                pass

        return True

    # ── Logging ─────────────────────────────────────────────────────── #

    def _log_evolution(
        self,
        event_type: str,
        result: SkillEvolutionResult,
    ) -> None:
        """Log an evolution event for tracking.

        Args:
            event_type: The triggering event type.
            result: The evolution result.
        """
        self._evolution_log.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event_type": event_type,
                "status": result.status,
                "skill_id": result.skill_id,
                "skill_name": result.skill_name,
                "change": result.change_description,
                "confidence": result.new_confidence,
            }
        )


# ── Opposite action detection ───────────────────────────────────────── #


def _get_opposite_actions(action: str) -> set[str]:
    """Generate set of actions that would contradict the given action.

    Args:
        action: An action string.

    Returns:
        Set of opposing action strings.
    """
    opposites: set[str] = set()

    # cancel_ / un_ prefix patterns
    if action.startswith("cancel_"):
        opposites.add(action[7:])  # cancel_X -> X
    else:
        opposites.add(f"cancel_{action}")

    if action.startswith("un"):
        opposites.add(action[2:])
    else:
        opposites.add(f"un{action}")

    # Specific known pairs
    _PAIRS = {
        "approve": "reject",
        "accept": "decline",
        "send_message": "block_message",
        "escalate_to_human": "auto_resolve",
        "contact_cleaner": "skip_cleaner",
        "notify_guest": "suppress_notification",
    }
    if action in _PAIRS:
        opposites.add(_PAIRS[action])
    # Reverse lookup
    for k, v in _PAIRS.items():
        if action == v:
            opposites.add(k)

    return opposites


# ── Prompt templates ────────────────────────────────────────────────── #

_REFLECTION_SYSTEM = (
    "You are a skill analysis engine for an autonomous property manager. "
    "Analyze failures and propose concrete skill improvements. "
    "Return valid JSON with these exact fields: "
    "summary, root_cause, missing_trigger, wrong_action, correct_action, "
    "new_trigger_events (array of event strings), "
    "new_trigger_context (object with required context fields), "
    "applicable_conditions (string describing when this applies)."
)

_REFLECTION_PROMPT = """Analyze why this action failed and propose a specific skill improvement.

EVENT: {event}
ACTION TAKEN: {action}
FAILURE REASON: {failure}
EXISTING SKILL: {skill}
CONTEXT (truncated): {context}

Analyze carefully:
1. What was the root cause of the failure?
2. What trigger condition was missing that would have prevented this?
3. What action should have been taken instead?
4. Under what specific conditions should this correction apply?

Return JSON:
{{
    "summary": "one-line summary",
    "root_cause": "why it failed",
    "missing_trigger": "what trigger was missing",
    "wrong_action": "what action was wrong (or empty if N/A)",
    "correct_action": "what to do instead",
    "new_trigger_events": ["event_type_1"],
    "new_trigger_context": {{"key": "value"}},
    "applicable_conditions": "when this should apply"
}}"""
