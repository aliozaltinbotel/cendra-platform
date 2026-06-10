"""Skill Injector — integrates skills into the prompt assembly pipeline.

Bridges the SkillRegistry with PromptAssembler by formatting active
skills as system prompt sections. Implements progressive disclosure:
only relevant skills are included based on context tags and limits.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.skills.models import SkillDefinition
from brain_engine.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SKILLS = 15
_DEFAULT_MAX_CONTENT_CHARS = 8000


class SkillInjector:
    """Injects skills from registry into LLM prompts.

    Applies progressive disclosure based on context relevance,
    token budget, and priority ordering.

    Args:
        registry: Skill registry to read from.
        max_skills: Maximum skills to inject per prompt.
        max_content_chars: Character budget for skill content.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        max_skills: int = _DEFAULT_MAX_SKILLS,
        max_content_chars: int = _DEFAULT_MAX_CONTENT_CHARS,
    ) -> None:
        self._registry = registry
        self._max_skills = max_skills
        self._max_chars = max_content_chars

    def build_skill_prompt(
        self,
        context_tags: list[str] | None = None,
        event_type: str | None = None,
    ) -> str:
        """Build the skill section for system prompt injection.

        Selects relevant skills based on tags and event context,
        respects token budget, and formats for LLM consumption.

        Args:
            context_tags: Tags from the current conversation context.
            event_type: Current event type for relevance filtering.

        Returns:
            Formatted skill prompt section (empty string if no skills).
        """
        candidates = self._gather_candidates(context_tags, event_type)

        if not candidates:
            return ""

        selected = self._apply_budget(candidates)
        return _format_skill_section(selected)

    def get_allowed_tools(
        self,
        active_skill_name: str | None = None,
    ) -> list[str] | None:
        """Get tool restrictions for the active skill.

        Args:
            active_skill_name: Name of the currently active skill.

        Returns:
            List of allowed tool names, or None for unrestricted.
        """
        if not active_skill_name:
            return None

        skill = self._registry.get(active_skill_name)
        if skill and skill.tool_restricted:
            return list(skill.allowed_tools)

        return None

    # ── Internal ──────────────────────────────────────────────────────

    def _gather_candidates(
        self,
        tags: list[str] | None,
        event_type: str | None,
    ) -> list[SkillDefinition]:
        """Gather candidate skills from registry.

        Args:
            tags: Context tags for filtering.
            event_type: Event type for filtering.

        Returns:
            Candidate skills sorted by priority.
        """
        if tags:
            candidates = self._registry.find_by_tag(tags[0])
            for tag in tags[1:]:
                candidates.extend(self._registry.find_by_tag(tag))
            candidates = _deduplicate(candidates)
        else:
            candidates = self._registry.get_all()

        if event_type:
            event_matches = self._registry.search(event_type)
            candidates = _merge_unique(candidates, event_matches)

        return candidates[:self._max_skills]

    def _apply_budget(
        self,
        candidates: list[SkillDefinition],
    ) -> list[SkillDefinition]:
        """Trim candidates to fit within character budget.

        Args:
            candidates: Pre-selected skills.

        Returns:
            Skills that fit within the budget.
        """
        selected: list[SkillDefinition] = []
        total_chars = 0

        for skill in candidates:
            block_len = len(skill.to_prompt_block())
            if total_chars + block_len > self._max_chars:
                break
            selected.append(skill)
            total_chars += block_len

        return selected


# ── Formatting helpers ────────────────────────────────────────────── #


def _format_skill_section(skills: list[SkillDefinition]) -> str:
    """Format selected skills into a prompt section.

    Args:
        skills: Skills to format.

    Returns:
        Complete skill section with header.
    """
    blocks = [s.to_prompt_block() for s in skills]
    header = f"## Active Skills ({len(skills)})\n\n"
    return header + "\n\n---\n\n".join(blocks)


def _deduplicate(
    skills: list[SkillDefinition],
) -> list[SkillDefinition]:
    """Remove duplicate skills by name, keeping first occurrence.

    Args:
        skills: List with potential duplicates.

    Returns:
        Deduplicated list preserving order.
    """
    seen: set[str] = set()
    result: list[SkillDefinition] = []

    for skill in skills:
        if skill.name not in seen:
            seen.add(skill.name)
            result.append(skill)

    return result


def _merge_unique(
    primary: list[SkillDefinition],
    secondary: list[SkillDefinition],
) -> list[SkillDefinition]:
    """Merge two skill lists without duplicates.

    Args:
        primary: Primary list (takes precedence).
        secondary: Additional skills to append.

    Returns:
        Merged list without duplicates.
    """
    names = {s.name for s in primary}
    merged = list(primary)

    for skill in secondary:
        if skill.name not in names:
            merged.append(skill)
            names.add(skill.name)

    return merged
