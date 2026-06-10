"""StateBackend — in-memory ephemeral storage backend.

Stores files in a dict, checkpointed with graph state.
Suitable for temporary data that lives within a single
agent execution. Lost when process restarts.

Based on: Deep Agents StateBackend.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class StateBackend:
    """In-memory backend backed by a plain dict.

    All data is stored in ``_files: dict[str, str]`` where
    keys are paths and values are file contents.
    """

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    @property
    def file_count(self) -> int:
        """Number of stored files."""
        return len(self._files)

    async def ls(self, path: str) -> list[str]:
        """List entries at a virtual directory path.

        Args:
            path: Directory path (e.g. ``"docs"``).

        Returns:
            Sorted unique names at this level.
        """
        prefix = _normalize_path(path)
        entries: set[str] = set()
        for key in self._files:
            if not key.startswith(prefix):
                continue
            remainder = key[len(prefix):]
            first_part = remainder.split("/")[0]
            if first_part:
                entries.add(first_part)
        return sorted(entries)

    async def read(self, path: str) -> str:
        """Read file contents.

        Args:
            path: File path.

        Returns:
            File content.

        Raises:
            FileNotFoundError: If not stored.
        """
        key = _normalize_path(path).rstrip("/")
        if key not in self._files:
            raise FileNotFoundError(f"Not in state: {path}")
        return self._files[key]

    async def write(self, path: str, content: str) -> None:
        """Store file content.

        Args:
            path: File path.
            content: Text content.
        """
        key = _normalize_path(path).rstrip("/")
        self._files[key] = content

    async def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> bool:
        """Replace text in a stored file.

        Args:
            path: File path.
            old_text: Text to replace.
            new_text: Replacement.

        Returns:
            True if replacement made.
        """
        key = _normalize_path(path).rstrip("/")
        content = self._files.get(key)
        if content is None or old_text not in content:
            return False
        self._files[key] = content.replace(old_text, new_text, 1)
        return True

    async def glob(self, pattern: str) -> list[str]:
        """Find files matching glob pattern.

        Args:
            pattern: Glob pattern.

        Returns:
            Matching paths.
        """
        return sorted(
            key for key in self._files
            if fnmatch.fnmatch(key, pattern)
        )

    async def grep(
        self,
        pattern: str,
        path: str = ".",
    ) -> list[dict[str, Any]]:
        """Search stored files for regex.

        Args:
            pattern: Regex pattern.
            path: Path prefix filter.

        Returns:
            Match results.
        """
        compiled = re.compile(pattern, re.IGNORECASE)
        prefix = _normalize_path(path)
        results: list[dict[str, Any]] = []

        for key, content in self._files.items():
            if not key.startswith(prefix):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    results.append({
                        "file": key,
                        "line": i,
                        "content": line.strip(),
                    })

        return results

    async def exists(self, path: str) -> bool:
        """Check if a path exists in state.

        Args:
            path: File path.

        Returns:
            True if stored.
        """
        key = _normalize_path(path).rstrip("/")
        if key in self._files:
            return True
        prefix = key + "/"
        return any(k.startswith(prefix) for k in self._files)

    def checkpoint(self) -> dict[str, str]:
        """Export state for checkpointing.

        Returns:
            Copy of all stored files.
        """
        return dict(self._files)

    def from_checkpoint(self, data: dict[str, str]) -> None:
        """Restore state from checkpoint.

        Args:
            data: Previously checkpointed files dict.
        """
        self._files = dict(data)

    def clear(self) -> None:
        """Remove all stored files."""
        self._files.clear()


def _normalize_path(path: str) -> str:
    """Normalize a path string.

    Removes leading ``./``, ensures directory paths end with ``/``.

    Args:
        path: Raw path string.

    Returns:
        Normalized path.
    """
    path = path.strip()
    if path.startswith("./"):
        path = path[2:]
    if path == ".":
        return ""
    if path and not path.endswith("/") and "." not in path.split("/")[-1]:
        path += "/"
    return path
