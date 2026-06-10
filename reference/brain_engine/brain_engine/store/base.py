"""BaseStore — abstract interface for cross-thread persistence.

Provides a namespace-scoped key-value store that persists data
across conversations and threads. Used by agents, middleware,
and graph nodes to share durable state.

Based on: LangGraph BaseStore (langgraph/store/base.py).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence


@dataclass
class Item:
    """A single stored item with metadata.

    Attributes:
        key: Item identifier within its namespace.
        value: Stored data (any JSON-serializable dict).
        namespace: Hierarchical namespace tuple (e.g. ("user", "123")).
        created_at: When the item was first stored.
        updated_at: When the item was last modified.
        item_id: Unique item identifier.
    """

    key: str
    value: dict[str, Any]
    namespace: tuple[str, ...] = ()
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    item_id: str = field(
        default_factory=lambda: str(uuid.uuid4()),
    )


class BaseStore(ABC):
    """Abstract base for cross-thread key-value stores.

    All operations are scoped by namespace tuples, allowing
    hierarchical organization (e.g. ``("user", user_id, "prefs")``).
    """

    @abstractmethod
    async def get(
        self,
        namespace: tuple[str, ...],
        key: str,
    ) -> Item | None:
        """Retrieve a single item by namespace and key.

        Args:
            namespace: Hierarchical namespace tuple.
            key: Item key within the namespace.

        Returns:
            Item if found, None otherwise.
        """
        ...

    @abstractmethod
    async def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
    ) -> Item:
        """Store or update an item.

        Args:
            namespace: Hierarchical namespace tuple.
            key: Item key within the namespace.
            value: Data to store.

        Returns:
            The stored Item.
        """
        ...

    @abstractmethod
    async def delete(
        self,
        namespace: tuple[str, ...],
        key: str,
    ) -> bool:
        """Delete an item by namespace and key.

        Args:
            namespace: Hierarchical namespace tuple.
            key: Item key to delete.

        Returns:
            True if the item existed and was deleted.
        """
        ...

    @abstractmethod
    async def list(
        self,
        namespace: tuple[str, ...],
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Item]:
        """List items in a namespace.

        Args:
            namespace: Hierarchical namespace tuple.
            limit: Maximum items to return.
            offset: Number of items to skip.

        Returns:
            List of Items in the namespace.
        """
        ...

    @abstractmethod
    async def search(
        self,
        namespace: tuple[str, ...],
        query: str,
        *,
        limit: int = 10,
    ) -> list[Item]:
        """Search items by content within a namespace.

        Args:
            namespace: Scope for the search.
            query: Text to search for in item values.
            limit: Maximum results.

        Returns:
            Matching Items ranked by relevance.
        """
        ...
