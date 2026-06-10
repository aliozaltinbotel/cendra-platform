"""DatasetManager — hierarchical namespace for brain:// paths.

Manages a tree of datasets with inherited properties. Each dataset
is a namespace that can contain data paths and child datasets.

Hierarchy example:
    brain://sessions/sess_abc/context
    brain://sessions/sess_abc/tools/search_results
    brain://memory/guests/guest_123
    brain://cache/property_descriptions
"""

from __future__ import annotations

import logging
from typing import Any

from brain_engine.zfs.models import Dataset

logger = logging.getLogger(__name__)

DEFAULT_PROPERTIES: dict[str, Any] = {
    "compression": "lz4",
    "dedup": True,
    "quota": 0,
    "readonly": False,
}


class DatasetManager:
    """Manages hierarchical dataset namespaces.

    Datasets form a tree where child datasets inherit parent
    properties unless explicitly overridden.

    Args:
        root_path: Root path prefix (default: "brain://").
    """

    def __init__(self, root_path: str = "brain://") -> None:
        self._root = root_path
        self._datasets: dict[str, Dataset] = {}
        root_ds = Dataset(
            path=root_path,
            properties=dict(DEFAULT_PROPERTIES),
        )
        self._datasets[root_path] = root_ds

    @property
    def count(self) -> int:
        """Return the number of datasets (including root)."""
        return len(self._datasets)

    # ── Create ───────────────────────────────────────────────────────

    async def create(
        self,
        path: str,
        **properties: Any,
    ) -> Dataset:
        """Create a new dataset at the given path.

        Parent datasets are created automatically if they don't exist.
        Properties are inherited from the parent and overridden by
        explicitly provided values.

        Args:
            path: Full brain:// path for the dataset.
            **properties: Override properties (compression, dedup, etc.).

        Returns:
            The created Dataset.

        Raises:
            ValueError: If the dataset already exists.
        """
        if path in self._datasets:
            msg = f"Dataset '{path}' already exists"
            raise ValueError(msg)

        parent_path = self._find_parent_path(path)
        self._ensure_parent_chain(parent_path)

        parent = self._datasets.get(parent_path)
        inherited = dict(parent.properties) if parent else dict(DEFAULT_PROPERTIES)
        inherited.update(properties)

        ds = Dataset(path=path, properties=inherited)
        self._datasets[path] = ds

        if parent and path not in parent.children:
            parent.children.append(path)

        logger.debug("Dataset created: %s", path)
        return ds

    # ── Get / List ───────────────────────────────────────────────────

    async def get(self, path: str) -> Dataset | None:
        """Get a dataset by path.

        Args:
            path: Dataset path.

        Returns:
            Dataset or None.
        """
        return self._datasets.get(path)

    async def exists(self, path: str) -> bool:
        """Check if a dataset exists."""
        return path in self._datasets

    async def list_children(self, path: str) -> list[Dataset]:
        """List direct child datasets.

        Args:
            path: Parent dataset path.

        Returns:
            List of child datasets.
        """
        parent = self._datasets.get(path)
        if parent is None:
            return []
        return [
            self._datasets[cp]
            for cp in parent.children
            if cp in self._datasets
        ]

    async def list_all(self) -> list[Dataset]:
        """List all datasets in creation order.

        Returns:
            List of all Dataset objects.
        """
        return sorted(self._datasets.values(), key=lambda d: d.path)

    # ── Properties ───────────────────────────────────────────────────

    async def set_property(
        self,
        path: str,
        key: str,
        value: Any,
    ) -> None:
        """Set a property on a dataset.

        Args:
            path: Dataset path.
            key: Property name.
            value: Property value.

        Raises:
            KeyError: If the dataset does not exist.
        """
        ds = self._datasets.get(path)
        if ds is None:
            msg = f"Dataset '{path}' not found"
            raise KeyError(msg)
        ds.properties[key] = value

    async def get_property(self, path: str, key: str) -> Any:
        """Get a property value, walking up the hierarchy if not set.

        Args:
            path: Dataset path.
            key: Property name.

        Returns:
            Property value, or None if not found anywhere.
        """
        current = path
        while current:
            ds = self._datasets.get(current)
            if ds and key in ds.properties:
                return ds.properties[key]
            current = self._find_parent_path(current)
            if current == path:
                break
        return None

    # ── Destroy ──────────────────────────────────────────────────────

    async def destroy(self, path: str, recursive: bool = False) -> bool:
        """Delete a dataset.

        Args:
            path: Dataset to delete.
            recursive: If True, also delete all children.

        Returns:
            True if deleted, False if not found.

        Raises:
            ValueError: If dataset has children and recursive is False.
        """
        ds = self._datasets.get(path)
        if ds is None:
            return False

        if ds.children and not recursive:
            msg = f"Dataset '{path}' has children. Use recursive=True."
            raise ValueError(msg)

        if recursive:
            for child_path in list(ds.children):
                await self.destroy(child_path, recursive=True)

        parent_path = self._find_parent_path(path)
        parent = self._datasets.get(parent_path)
        if parent and path in parent.children:
            parent.children.remove(path)

        del self._datasets[path]
        logger.debug("Dataset destroyed: %s", path)
        return True

    # ── Internal ─────────────────────────────────────────────────────

    def _find_parent_path(self, path: str) -> str:
        """Find the parent path by trimming the last segment.

        Handles brain:// URI scheme correctly, treating the double-slash
        as part of the root prefix.

        Args:
            path: Current path.

        Returns:
            Parent path, or root if at top level.
        """
        if path == self._root:
            return self._root

        stripped = path.rstrip("/")

        prefix = ""
        rest = stripped
        if "://" in stripped:
            idx = stripped.index("://") + 3
            prefix = stripped[:idx]
            rest = stripped[idx:]

        if "/" not in rest or not rest:
            return self._root

        parent_rest = rest.rsplit("/", 1)[0]
        if not parent_rest:
            return self._root

        return prefix + parent_rest + "/"

    def _ensure_parent_chain(self, parent_path: str) -> None:
        """Create parent datasets if they don't exist.

        Args:
            parent_path: Path of the required parent.
        """
        if parent_path in self._datasets:
            return
        if parent_path == self._root:
            return

        grandparent = self._find_parent_path(parent_path)
        self._ensure_parent_chain(grandparent)

        parent = Dataset(
            path=parent_path,
            properties=dict(DEFAULT_PROPERTIES),
        )
        self._datasets[parent_path] = parent

        gp = self._datasets.get(grandparent)
        if gp and parent_path not in gp.children:
            gp.children.append(parent_path)
