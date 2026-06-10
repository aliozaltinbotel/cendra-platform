"""Skill Registry — central store and lookup for all registered skills.

Provides fast name-based lookup, tag-based search, and priority-ordered
retrieval. Handles deduplication by name with priority resolution
(higher priority wins).
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.skills.models import SkillDefinition, SkillPriority

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Thread-safe registry of skill definitions.

    Skills are indexed by name. When a duplicate name is registered,
    the higher-priority skill wins (lower numeric value).

    Example::

        registry = SkillRegistry()
        registry.register(my_skill)
        found = registry.find_by_tag("damage")
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._skills: dict[str, SkillDefinition] = {}

    @property
    def count(self) -> int:
        """Number of registered skills."""
        return len(self._skills)

    @property
    def names(self) -> list[str]:
        """Sorted list of all registered skill names."""
        return sorted(self._skills.keys())

    def register(self, skill: SkillDefinition) -> bool:
        """Register a skill definition.

        If a skill with the same name exists, the higher-priority
        (lower numeric value) version wins.

        Args:
            skill: Skill to register.

        Returns:
            True if the skill was added or replaced.
        """
        existing = self._skills.get(skill.name)

        if existing and existing.priority <= skill.priority:
            logger.debug(
                "Skipped skill '%s': existing priority %s <= %s",
                skill.name, existing.priority.name, skill.priority.name,
            )
            return False

        self._skills[skill.name] = skill
        logger.debug("Registered skill: %s (%s)", skill.name, skill.priority.name)
        return True

    def register_many(self, skills: list[SkillDefinition]) -> int:
        """Register multiple skills at once.

        Args:
            skills: List of skills to register.

        Returns:
            Number of skills actually added or replaced.
        """
        return sum(1 for s in skills if self.register(s))

    def get(self, name: str) -> SkillDefinition | None:
        """Look up a skill by exact name.

        Args:
            name: Skill name.

        Returns:
            SkillDefinition or None if not found.
        """
        return self._skills.get(name)

    def find_by_tag(self, tag: str) -> list[SkillDefinition]:
        """Find all skills matching a tag.

        Args:
            tag: Tag to search for (case-insensitive).

        Returns:
            Matching skills sorted by priority.
        """
        matches = [s for s in self._skills.values() if s.matches_tag(tag)]
        return sorted(matches, key=lambda s: s.priority)

    def find_by_priority(
        self, priority: SkillPriority,
    ) -> list[SkillDefinition]:
        """Get all skills of a specific priority tier.

        Args:
            priority: Priority level to filter by.

        Returns:
            Matching skills sorted by name.
        """
        matches = [
            s for s in self._skills.values()
            if s.priority == priority
        ]
        return sorted(matches, key=lambda s: s.name)

    def search(self, query: str) -> list[SkillDefinition]:
        """Full-text search across name and description.

        Args:
            query: Search query (case-insensitive substring match).

        Returns:
            Matching skills sorted by priority.
        """
        q = query.lower()
        matches = [
            s for s in self._skills.values()
            if q in s.name.lower() or q in s.description.lower()
        ]
        return sorted(matches, key=lambda s: s.priority)

    def unregister(self, name: str) -> bool:
        """Remove a skill by name.

        Args:
            name: Skill name to remove.

        Returns:
            True if a skill was removed.
        """
        if name in self._skills:
            del self._skills[name]
            return True
        return False

    def get_all(self) -> list[SkillDefinition]:
        """Get all skills sorted by priority then name.

        Returns:
            All registered skills.
        """
        return sorted(
            self._skills.values(),
            key=lambda s: (s.priority, s.name),
        )

    def build_prompt_section(
        self,
        max_skills: int = 20,
        tags: list[str] | None = None,
    ) -> str:
        """Build a combined prompt section from active skills.

        Applies progressive disclosure: includes up to ``max_skills``
        most relevant skills, filtered by tags if provided.

        Args:
            max_skills: Maximum number of skills to include.
            tags: Optional tag filter (include skills matching any tag).

        Returns:
            Formatted prompt text with all skill blocks.
        """
        skills = self._select_skills(tags, max_skills)

        if not skills:
            return ""

        blocks = [s.to_prompt_block() for s in skills]
        header = f"## Active Skills ({len(skills)})\n\n"
        return header + "\n\n".join(blocks)

    def _select_skills(
        self,
        tags: list[str] | None,
        max_count: int,
    ) -> list[SkillDefinition]:
        """Select skills for prompt inclusion.

        Args:
            tags: Optional tag filter.
            max_count: Maximum skills to return.

        Returns:
            Filtered and truncated skill list.
        """
        if tags:
            candidates = _filter_by_tags(self._skills.values(), tags)
        else:
            candidates = list(self._skills.values())

        candidates.sort(key=lambda s: (s.priority, s.name))
        return candidates[:max_count]

    def clear(self) -> None:
        """Remove all registered skills."""
        self._skills.clear()


def _filter_by_tags(
    skills: Any,
    tags: list[str],
) -> list[SkillDefinition]:
    """Filter skills that match any of the given tags.

    Args:
        skills: Iterable of SkillDefinitions.
        tags: Tags to match (OR logic).

    Returns:
        Skills matching at least one tag.
    """
    return [
        s for s in skills
        if any(s.matches_tag(t) for t in tags)
    ]
