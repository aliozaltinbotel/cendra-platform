"""Skills system for Brain Engine — SKILL.md-based skill management.

Provides skill loading from files, registration with priority resolution,
prompt injection with progressive disclosure, and integration with
ProceduralMemory for evolved skills.

Example::

    from brain_engine.skills import SkillLoader, SkillRegistry, SkillInjector

    loader = SkillLoader("./skills")
    registry = SkillRegistry()
    registry.register_many(loader.load_all())

    injector = SkillInjector(registry)
    prompt_section = injector.build_skill_prompt(context_tags=["damage"])
"""

from brain_engine.skills.creator import SkillCreator
from brain_engine.skills.injector import SkillInjector
from brain_engine.skills.loader import SkillLoader
from brain_engine.skills.models import SkillDefinition, SkillPriority
from brain_engine.skills.registry import SkillRegistry

__all__ = [
    "SkillCreator",
    "SkillDefinition",
    "SkillInjector",
    "SkillLoader",
    "SkillPriority",
    "SkillRegistry",
]
