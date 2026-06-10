"""InMemoryStore — in-memory cross-thread key-value store.

Dict-backed implementation of BaseStore suitable for development,
testing, and single-process deployments. Thread-safe via asyncio.

Based on: LangGraph InMemoryStore (langgraph/store/memory.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from brain_engine.store.base import BaseStore, Item

logger = logging.getLogger(__name__)


class InMemoryStore(BaseStore):
    """In-memory store backed by nested dicts.

    Data is organized as::

        _data[namespace_key][item_key] = Item

    Where ``namespace_key`` is the joined namespace tuple.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Item]] = {}

    @property
    def size(self) -> int:
        """Total number of items across all namespaces."""
        return sum(len(ns) for ns in self._data.values())

    async def get(
        self,
        namespace: tuple[str, ...],
        key: str,
    ) -> Item | None:
        """Retrieve a single item.

        Args:
            namespace: Namespace tuple.
            key: Item key.

        Returns:
            Item if found, None otherwise.
        """
        ns_key = _namespace_key(namespace)
        ns_data = self._data.get(ns_key, {})
        return ns_data.get(key)

    async def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
    ) -> Item:
        """Store or update an item.

        Creates the namespace if it doesn't exist. Updates
        ``updated_at`` on existing items.

        Args:
            namespace: Namespace tuple.
            key: Item key.
            value: Data to store.

        Returns:
            The stored Item.
        """
        ns_key = _namespace_key(namespace)
        ns_data = self._data.setdefault(ns_key, {})

        existing = ns_data.get(key)
        if existing is not None:
            return _update_existing(existing, value)

        item = Item(key=key, value=value, namespace=namespace)
        ns_data[key] = item
        return item

    async def delete(
        self,
        namespace: tuple[str, ...],
        key: str,
    ) -> bool:
        """Delete an item.

        Args:
            namespace: Namespace tuple.
            key: Item key.

        Returns:
            True if deleted.
        """
        ns_key = _namespace_key(namespace)
        ns_data = self._data.get(ns_key, {})
        return ns_data.pop(key, None) is not None

    async def list(
        self,
        namespace: tuple[str, ...],
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Item]:
        """List items in a namespace with pagination.

        Args:
            namespace: Namespace tuple.
            limit: Max results.
            offset: Skip count.

        Returns:
            List of Items.
        """
        ns_key = _namespace_key(namespace)
        ns_data = self._data.get(ns_key, {})
        items = sorted(ns_data.values(), key=lambda i: i.created_at)
        return items[offset:offset + limit]

    async def search(
        self,
        namespace: tuple[str, ...],
        query: str,
        *,
        limit: int = 10,
    ) -> list[Item]:
        """Search items by matching query against string values.

        Performs case-insensitive substring matching against all
        string values in each item.

        Args:
            namespace: Search scope.
            query: Search text.
            limit: Max results.

        Returns:
            Matching Items.
        """
        ns_key = _namespace_key(namespace)
        ns_data = self._data.get(ns_key, {})
        query_lower = query.lower()

        matches = [
            item for item in ns_data.values()
            if _item_matches(item, query_lower)
        ]
        return matches[:limit]

    async def clear(self) -> None:
        """Remove all items from all namespaces."""
        self._data.clear()

    async def namespaces(self) -> list[tuple[str, ...]]:
        """List all namespaces that have items.

        Returns:
            List of namespace tuples.
        """
        return [
            tuple(k.split("|")) for k in self._data if self._data[k]
        ]


# ── Helpers ──────────────────────────────────────────────────────────── #


def _namespace_key(namespace: tuple[str, ...]) -> str:
    """Convert namespace tuple to a string key.

    Args:
        namespace: Tuple of namespace parts.

    Returns:
        Pipe-delimited string key.
    """
    return "|".join(namespace) if namespace else "__root__"


def _update_existing(item: Item, value: dict[str, Any]) -> Item:
    """Update an existing item's value and timestamp.

    Args:
        item: Existing item to update.
        value: New value data.

    Returns:
        The updated Item.
    """
    item.value = value
    item.updated_at = datetime.now(timezone.utc)
    return item


def _item_matches(item: Item, query_lower: str) -> bool:
    """Check if any string value in the item matches the query.

    Args:
        item: Item to check.
        query_lower: Lowercased search query.

    Returns:
        True if any value contains the query.
    """
    for val in item.value.values():
        if isinstance(val, str) and query_lower in val.lower():
            return True
    return False
