"""BackendProtocol — abstract storage interface for agents.

Defines the contract for all backend implementations: filesystem,
in-memory state, remote storage. Agents interact with files and
data through this protocol without caring about the storage layer.

Based on: Deep Agents BackendProtocol (deepagents/backends/protocol.py).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class BackendProtocol(Protocol):
    """Protocol for pluggable file/data storage backends.

    Every backend must implement these 7 operations. Agents use
    these to read SOPs, write reports, search knowledge, and
    execute commands.
    """

    async def ls(self, path: str) -> list[str]:
        """List files and directories at path.

        Args:
            path: Directory path to list.

        Returns:
            List of file/directory names.
        """
        ...

    async def read(self, path: str) -> str:
        """Read file contents as text.

        Args:
            path: File path to read.

        Returns:
            File content string.

        Raises:
            FileNotFoundError: If path does not exist.
        """
        ...

    async def write(self, path: str, content: str) -> None:
        """Write text content to a file.

        Creates parent directories if needed.

        Args:
            path: File path to write.
            content: Text content to write.
        """
        ...

    async def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> bool:
        """Replace text in a file.

        Args:
            path: File path to edit.
            old_text: Text to find and replace.
            new_text: Replacement text.

        Returns:
            True if replacement was made.
        """
        ...

    async def glob(self, pattern: str) -> list[str]:
        """Find files matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g. ``**/*.md``).

        Returns:
            List of matching file paths.
        """
        ...

    async def grep(
        self,
        pattern: str,
        path: str = ".",
    ) -> list[dict[str, Any]]:
        """Search file contents for a regex pattern.

        Args:
            pattern: Regex pattern to search.
            path: Directory to search in.

        Returns:
            List of dicts with ``file``, ``line``, ``content``.
        """
        ...

    async def exists(self, path: str) -> bool:
        """Check if a path exists.

        Args:
            path: Path to check.

        Returns:
            True if exists.
        """
        ...


@runtime_checkable
class SandboxProtocol(Protocol):
    """Extended protocol for backends that support command execution."""

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Execute a shell command.

        Args:
            command: Shell command string.
            timeout: Max execution time in seconds.

        Returns:
            Dict with ``stdout``, ``stderr``, ``exit_code``.
        """
        ...
