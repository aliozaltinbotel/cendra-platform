"""Skill Loader — loads SkillDefinitions from SKILL.md files on disk.

Supports the DeepAgents SKILL.md format: YAML frontmatter delimited
by ``---``, followed by a markdown body containing the skill instructions.

Directory layout::

    skills/
      builtin/
        damage_inspection.md
      project/
        custom_checkin.md
      user/
        my_personal.md

Priority is inferred from the subdirectory name.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from brain_engine.skills.models import SkillDefinition, SkillPriority

logger = logging.getLogger(__name__)

_DIR_PRIORITY_MAP: dict[str, SkillPriority] = {
    "builtin": SkillPriority.BUILTIN,
    "project": SkillPriority.PROJECT,
    "user": SkillPriority.USER,
    "evolved": SkillPriority.EVOLVED,
}


class SkillLoader:
    """Loads skill definitions from the filesystem.

    Scans a root directory for ``*.md`` files with YAML frontmatter
    and converts each into a ``SkillDefinition``.

    Args:
        root_dir: Root directory containing skill subdirectories.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)

    def load_all(self) -> list[SkillDefinition]:
        """Load all skills from all priority subdirectories.

        Returns:
            List of SkillDefinitions sorted by priority (ascending).
        """
        skills: list[SkillDefinition] = []

        for subdir in self._iter_priority_dirs():
            loaded = self._load_directory(subdir)
            skills.extend(loaded)

        skills.sort(key=lambda s: s.priority)
        logger.info("Loaded %d skills from %s", len(skills), self._root)
        return skills

    def load_file(self, path: Path) -> SkillDefinition | None:
        """Load a single skill from a markdown file.

        Args:
            path: Path to the SKILL.md file.

        Returns:
            SkillDefinition or None if parsing fails.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Cannot read skill file: %s", path)
            return None

        frontmatter, body = _split_frontmatter(raw)
        if frontmatter is None:
            logger.warning("No YAML frontmatter in %s", path)
            return None

        priority = _infer_priority(path, self._root)
        return _build_definition(frontmatter, body, priority, str(path))

    # ── Internal ──────────────────────────────────────────────────────

    def _iter_priority_dirs(self) -> list[Path]:
        """List existing priority subdirectories.

        Returns:
            Sorted list of existing subdirectory paths.
        """
        dirs: list[Path] = []

        if not self._root.is_dir():
            return dirs

        for name in _DIR_PRIORITY_MAP:
            candidate = self._root / name
            if candidate.is_dir():
                dirs.append(candidate)

        # Also load from root (flat layout)
        if any(self._root.glob("*.md")):
            dirs.append(self._root)

        return dirs

    def _load_directory(self, directory: Path) -> list[SkillDefinition]:
        """Load all .md skills from one directory.

        Args:
            directory: Directory to scan.

        Returns:
            List of successfully parsed SkillDefinitions.
        """
        skills: list[SkillDefinition] = []

        for md_file in sorted(directory.glob("*.md")):
            skill = self.load_file(md_file)
            if skill:
                skills.append(skill)

        return skills


# ── Parsing helpers ───────────────────────────────────────────────── #


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split YAML frontmatter from markdown body.

    Args:
        text: Raw file content.

    Returns:
        Tuple of (parsed YAML dict or None, body text).
    """
    stripped = text.strip()

    if not stripped.startswith("---"):
        return None, stripped

    end_idx = stripped.find("---", 3)
    if end_idx == -1:
        return None, stripped

    yaml_block = stripped[3:end_idx].strip()
    body = stripped[end_idx + 3:].strip()

    try:
        frontmatter = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        logger.warning("Invalid YAML frontmatter")
        return None, stripped

    return frontmatter, body


def _infer_priority(path: Path, root: Path) -> SkillPriority:
    """Infer skill priority from its parent directory name.

    Args:
        path: Path to the skill file.
        root: Root skills directory.

    Returns:
        Inferred SkillPriority.
    """
    parent_name = path.parent.name.lower()
    return _DIR_PRIORITY_MAP.get(parent_name, SkillPriority.USER)


def _build_definition(
    frontmatter: dict[str, Any],
    body: str,
    priority: SkillPriority,
    source_path: str,
) -> SkillDefinition:
    """Build a SkillDefinition from parsed frontmatter and body.

    Args:
        frontmatter: Parsed YAML dict.
        body: Markdown body text.
        priority: Inferred priority level.
        source_path: Original file path.

    Returns:
        SkillDefinition instance.
    """
    return SkillDefinition(
        name=frontmatter.get("name", Path(source_path).stem),
        description=frontmatter.get("description", ""),
        content=body,
        allowed_tools=frontmatter.get("allowed_tools", []),
        tags=frontmatter.get("tags", []),
        priority=priority,
        version=frontmatter.get("version", 1),
        source_path=source_path,
        metadata=frontmatter.get("metadata", {}),
    )
