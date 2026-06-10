"""CompositeBackend — route operations to backends by path prefix.

Enables mixing storage backends: property docs on filesystem,
temp data in state, SOP files from remote. Routes each operation
to the correct backend based on path prefix matching.

Example::

    composite = CompositeBackend({
        "sops/": FilesystemBackend("/data/sops"),
        "temp/": StateBackend(),
        "": FilesystemBackend("/data/default"),  # fallback
    })

Based on: Deep Agents CompositeBackend.
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.backends.protocol import BackendProtocol

logger = logging.getLogger(__name__)


class CompositeBackend:
    """Routes file operations to backends by path prefix.

    Checks prefixes in longest-first order. The empty string
    prefix acts as a fallback for unmatched paths.

    Args:
        backends: Dict of prefix -> backend instance.
    """

    def __init__(
        self,
        backends: dict[str, BackendProtocol],
    ) -> None:
        self._backends = backends
        self._sorted_prefixes = sorted(
            backends.keys(), key=len, reverse=True,
        )

    async def ls(self, path: str) -> list[str]:
        """List files via the matched backend.

        Args:
            path: Directory path.

        Returns:
            File/directory names.
        """
        backend, relative = self._route(path)
        return await backend.ls(relative)

    async def read(self, path: str) -> str:
        """Read file via the matched backend.

        Args:
            path: File path.

        Returns:
            File content.
        """
        backend, relative = self._route(path)
        return await backend.read(relative)

    async def write(self, path: str, content: str) -> None:
        """Write file via the matched backend.

        Args:
            path: File path.
            content: Text content.
        """
        backend, relative = self._route(path)
        await backend.write(relative, content)

    async def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> bool:
        """Edit file via the matched backend.

        Args:
            path: File path.
            old_text: Text to replace.
            new_text: Replacement.

        Returns:
            True if edit was made.
        """
        backend, relative = self._route(path)
        return await backend.edit(relative, old_text, new_text)

    async def glob(self, pattern: str) -> list[str]:
        """Glob across all backends, combining results.

        Args:
            pattern: Glob pattern.

        Returns:
            Combined sorted results with prefix restored.
        """
        results: list[str] = []
        for prefix, backend in self._backends.items():
            matches = await backend.glob(pattern)
            for match in matches:
                results.append(prefix + match)
        return sorted(set(results))

    async def grep(
        self,
        pattern: str,
        path: str = ".",
    ) -> list[dict[str, Any]]:
        """Search via the matched backend.

        Args:
            pattern: Regex pattern.
            path: Search scope.

        Returns:
            Match results.
        """
        backend, relative = self._route(path)
        return await backend.grep(pattern, relative)

    async def exists(self, path: str) -> bool:
        """Check existence via the matched backend.

        Args:
            path: Path to check.

        Returns:
            True if exists.
        """
        backend, relative = self._route(path)
        return await backend.exists(relative)

    def _route(self, path: str) -> tuple[BackendProtocol, str]:
        """Find the backend and relative path for a given path.

        Matches the longest prefix first. Falls back to the
        empty-prefix backend if configured.

        Args:
            path: Full path to route.

        Returns:
            Tuple of (backend, relative_path).

        Raises:
            KeyError: If no matching backend is found.
        """
        for prefix in self._sorted_prefixes:
            if path.startswith(prefix):
                relative = path[len(prefix):]
                return self._backends[prefix], relative

        raise KeyError(
            f"No backend matches path '{path}'. "
            f"Prefixes: {list(self._backends.keys())}"
        )

    @property
    def backend_count(self) -> int:
        """Number of registered backends."""
        return len(self._backends)

    @property
    def prefixes(self) -> list[str]:
        """All registered prefixes in match order."""
        return list(self._sorted_prefixes)
