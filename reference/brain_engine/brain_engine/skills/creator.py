"""Skill Creator — generates new skills from LLM reflection or templates.

Integrates with SkillEvolutionEngine outputs and ProceduralMemory
to convert learned Procedures into formal SkillDefinitions. Also
supports manual skill creation with validation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from brain_engine.skills.models import SkillDefinition, SkillPriority

logger = logging.getLogger(__name__)


class SkillCreator:
    """Creates new SkillDefinitions from various sources.

    Converts ProceduralMemory entries, raw dicts, and LLM evolution
    results into validated SkillDefinitions ready for registration.
    """

    def from_procedure(self, procedure: Any) -> SkillDefinition:
        """Convert a ProceduralMemory Procedure to a SkillDefinition.

        Maps Procedure fields to the SKILL.md-compatible format.

        Args:
            procedure: A Procedure dataclass from ProceduralMemory.

        Returns:
            SkillDefinition with EVOLVED priority.
        """
        content = _build_procedure_content(procedure)
        tags = list(getattr(procedure, "tags", []))
        tags.append("evolved")

        return SkillDefinition(
            name=getattr(procedure, "name", "unnamed"),
            description=getattr(procedure, "description", ""),
            content=content,
            allowed_tools=[],
            tags=tags,
            priority=SkillPriority.EVOLVED,
            version=1,
            metadata=_extract_procedure_metadata(procedure),
        )

    def from_template(
        self,
        name: str,
        description: str,
        content: str,
        allowed_tools: list[str] | None = None,
        tags: list[str] | None = None,
        priority: SkillPriority = SkillPriority.PROJECT,
    ) -> SkillDefinition:
        """Create a skill from explicit parameters.

        Args:
            name: Unique skill name.
            description: One-line description.
            content: Full skill body.
            allowed_tools: Tool restrictions (empty = all).
            tags: Categorization tags.
            priority: Priority tier.

        Returns:
            Validated SkillDefinition.

        Raises:
            ValueError: If name or content is empty.
        """
        _validate_required(name, content)

        return SkillDefinition(
            name=name,
            description=description,
            content=content,
            allowed_tools=allowed_tools or [],
            tags=tags or [],
            priority=priority,
        )

    def from_evolution_result(
        self,
        event_type: str,
        reflection_summary: str,
        correct_action: str,
        trigger_events: list[str] | None = None,
    ) -> SkillDefinition:
        """Create a skill from a SkillEvolutionEngine output.

        Args:
            event_type: Event that triggered evolution.
            reflection_summary: LLM's failure analysis summary.
            correct_action: Corrected action to take.
            trigger_events: Events that should trigger this skill.

        Returns:
            SkillDefinition with EVOLVED priority.
        """
        events = trigger_events or [event_type]
        content = _build_evolution_content(
            event_type, reflection_summary, correct_action, events,
        )

        return SkillDefinition(
            name=f"evolved_{event_type}",
            description=reflection_summary[:200],
            content=content,
            tags=["evolved", event_type],
            priority=SkillPriority.EVOLVED,
            metadata={
                "evolved_from": event_type,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def batch_from_procedures(
        self, procedures: list[Any],
    ) -> list[SkillDefinition]:
        """Convert multiple procedures to skills.

        Args:
            procedures: List of Procedure objects.

        Returns:
            List of SkillDefinitions (skips failures).
        """
        results: list[SkillDefinition] = []

        for proc in procedures:
            try:
                skill = self.from_procedure(proc)
                results.append(skill)
            except Exception:
                logger.warning("Failed to convert procedure to skill")

        return results


# ── Helpers ───────────────────────────────────────────────────────── #


def _build_procedure_content(procedure: Any) -> str:
    """Build skill content from a Procedure's fields.

    Args:
        procedure: Procedure dataclass.

    Returns:
        Formatted markdown content.
    """
    triggers = getattr(procedure, "trigger_conditions", {})
    actions = getattr(procedure, "actions", [])
    events = triggers.get("events", [])

    lines = [
        f"**Trigger events:** {', '.join(events) if events else 'any'}",
        "",
        "**Actions:**",
    ]
    for action in actions:
        lines.append(f"- {action}")

    conditions = triggers.get("required_context", {})
    if conditions:
        lines.append("")
        lines.append("**Required context:**")
        for key, val in conditions.items():
            lines.append(f"- {key}: {val}")

    confidence = getattr(procedure, "confidence", 0.5)
    lines.append(f"\n**Confidence:** {confidence:.0%}")
    return "\n".join(lines)


def _extract_procedure_metadata(procedure: Any) -> dict[str, Any]:
    """Extract tracking metadata from a Procedure.

    Args:
        procedure: Procedure dataclass.

    Returns:
        Metadata dict.
    """
    return {
        "procedure_id": getattr(procedure, "procedure_id", ""),
        "source": getattr(procedure, "source", "unknown"),
        "success_count": getattr(procedure, "success_count", 0),
        "failure_count": getattr(procedure, "failure_count", 0),
        "created_at": getattr(procedure, "created_at", ""),
    }


def _build_evolution_content(
    event_type: str,
    summary: str,
    correct_action: str,
    trigger_events: list[str],
) -> str:
    """Build skill content from evolution analysis.

    Args:
        event_type: Origin event type.
        summary: Failure analysis summary.
        correct_action: Action that should be taken.
        trigger_events: Triggering events list.

    Returns:
        Formatted markdown content.
    """
    return (
        f"**Learned from:** {event_type} failure\n"
        f"**Analysis:** {summary}\n\n"
        f"**Trigger events:** {', '.join(trigger_events)}\n\n"
        f"**Correct action:** {correct_action}"
    )


def _validate_required(name: str, content: str) -> None:
    """Validate required fields for skill creation.

    Args:
        name: Skill name.
        content: Skill content.

    Raises:
        ValueError: If name or content is empty.
    """
    if not name.strip():
        raise ValueError("Skill name cannot be empty")
    if not content.strip():
        raise ValueError("Skill content cannot be empty")
