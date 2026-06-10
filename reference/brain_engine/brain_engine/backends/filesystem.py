"""FilesystemBackend — disk-based storage backend.

Implements BackendProtocol using the local filesystem.
Used for reading SOPs, property documents, knowledge base
files, and writing reports/logs.

Based on: Deep Agents FilesystemBackend.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FilesystemBackend:
    """Disk-based backend for file operations.

    All paths are resolved relative to a root directory for
    security. Prevents path traversal outside the root.

    Args:
        root: Root directory for all operations.
    """

    def __init__(self, root: str = ".") -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        """Return the root directory path."""
        return self._root

    async def ls(self, path: str) -> list[str]:
        """List files and directories.

        Args:
            path: Relative directory path.

        Returns:
            Sorted list of names.
        """
        target = self._resolve(path)
        if not target.is_dir():
            return []
        return sorted(entry.name for entry in target.iterdir())

    async def read(self, path: str) -> str:
        """Read file contents.

        Args:
            path: Relative file path.

        Returns:
            File text content.

        Raises:
            FileNotFoundError: If file does not exist.
        """
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"Not found: {path}")
        return target.read_text(encoding="utf-8")

    async def write(self, path: str, content: str) -> None:
        """Write content to file, creating parents.

        Args:
            path: Relative file path.
            content: Text to write.
        """
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> bool:
        """Replace text in a file.

        Args:
            path: Relative file path.
            old_text: Text to find.
            new_text: Replacement text.

        Returns:
            True if replacement was made.
        """
        target = self._resolve(path)
        if not target.is_file():
            return False
        content = target.read_text(encoding="utf-8")
        if old_text not in content:
            return False
        target.write_text(
            content.replace(old_text, new_text, 1),
            encoding="utf-8",
        )
        return True

    async def glob(self, pattern: str) -> list[str]:
        """Find files matching glob pattern.

        Args:
            pattern: Glob pattern relative to root.

        Returns:
            Sorted list of relative paths.
        """
        matches = sorted(self._root.glob(pattern))
        return [
            str(m.relative_to(self._root)) for m in matches
            if m.is_file()
        ]

    async def grep(
        self,
        pattern: str,
        path: str = ".",
    ) -> list[dict[str, Any]]:
        """Search file contents for regex pattern.

        Args:
            pattern: Regex pattern.
            path: Directory to search.

        Returns:
            List of match dicts.
        """
        target = self._resolve(path)
        compiled = re.compile(pattern, re.IGNORECASE)
        results: list[dict[str, Any]] = []

        for file_path in _walk_files(target):
            matches = _search_file(file_path, compiled)
            for line_no, line_text in matches:
                results.append({
                    "file": str(file_path.relative_to(self._root)),
                    "line": line_no,
                    "content": line_text.strip(),
                })

        return results

    async def exists(self, path: str) -> bool:
        """Check if path exists.

        Args:
            path: Relative path.

        Returns:
            True if exists.
        """
        return self._resolve(path).exists()

    def _resolve(self, path: str) -> Path:
        """Resolve a relative path safely within root.

        Prevents path traversal attacks.

        Args:
            path: Relative path string.

        Returns:
            Resolved absolute path.

        Raises:
            ValueError: If path escapes root directory.
        """
        resolved = (self._root / path).resolve()
        if not str(resolved).startswith(str(self._root)):
            raise ValueError(f"Path traversal blocked: {path}")
        return resolved


def _walk_files(directory: Path) -> list[Path]:
    """Walk directory tree and collect text files.

    Args:
        directory: Root directory to walk.

    Returns:
        List of file paths (skips binary files).
    """
    if not directory.is_dir():
        return [directory] if directory.is_file() else []
    files: list[Path] = []
    for root, _, filenames in os.walk(directory):
        for name in filenames:
            fp = Path(root) / name
            if _is_text_file(fp):
                files.append(fp)
    return files


def _is_text_file(path: Path) -> bool:
    """Check if a file is likely text (not binary).

    Args:
        path: File path to check.

    Returns:
        True if likely text file.
    """
    text_extensions = {
        ".txt", ".md", ".py", ".json", ".yaml", ".yml",
        ".toml", ".cfg", ".ini", ".csv", ".html", ".xml",
        ".rst", ".log", ".sh", ".env",
    }
    return path.suffix.lower() in text_extensions


def _search_file(
    path: Path,
    pattern: re.Pattern[str],
) -> list[tuple[int, str]]:
    """Search a single file for regex matches.

    Args:
        path: File to search.
        pattern: Compiled regex.

    Returns:
        List of (line_number, line_text) tuples.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, PermissionError):
        return []
    matches: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            matches.append((i, line))
    return matches
