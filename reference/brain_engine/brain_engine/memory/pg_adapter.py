"""PostgreSQL Adapter — asyncpg wrapper for GuestMemoryStore.

Provides the GuestDBLike interface using asyncpg connection pool.
Only loaded when DATABASE_URL environment variable is set.

Usage:
    adapter = AsyncPGAdapter("postgresql://user:pass@localhost/brainengine")
    await adapter.connect()
    store = GuestMemoryStore(adapter)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AsyncPGAdapter:
    """Asyncpg-based PostgreSQL adapter for GuestMemoryStore.

    Implements GuestDBLike protocol. Creates connection pool on first use.

    Args:
        database_url: PostgreSQL connection string.
        min_pool_size: Minimum connections in pool.
        max_pool_size: Maximum connections in pool.
    """

    def __init__(
        self,
        database_url: str,
        min_pool_size: int = 2,
        max_pool_size: int = 10,
    ) -> None:
        self._url = database_url
        self._min_size = min_pool_size
        self._max_size = max_pool_size
        self._pool: Any = None

    async def connect(self) -> None:
        """Create connection pool.

        Call this once at application startup.
        """
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._url,
                min_size=self._min_size,
                max_size=self._max_size,
            )
            logger.info("PostgreSQL pool created: %s", self._url[:30])
        except ImportError:
            logger.error("asyncpg not installed — pip install asyncpg")
            raise
        except Exception:
            logger.error("Failed to connect to PostgreSQL", exc_info=True)
            raise

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("PostgreSQL pool closed")

    async def execute(self, query: str, *args: Any) -> None:
        """Execute a query without returning results.

        Args:
            query: SQL query string.
            *args: Query parameters.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(query, *args)

    async def fetchone(
        self, query: str, *args: Any,
    ) -> dict[str, Any] | None:
        """Execute query and return single row as dict.

        Args:
            query: SQL query string.
            *args: Query parameters.

        Returns:
            Row as dict, or None.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            if row is None:
                return None
            return dict(row)

    async def fetchall(
        self, query: str, *args: Any,
    ) -> list[dict[str, Any]]:
        """Execute query and return all rows as dicts.

        Args:
            query: SQL query string.
            *args: Query parameters.

        Returns:
            List of row dicts.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(row) for row in rows]

    async def _ensure_pool(self) -> Any:
        """Ensure connection pool exists, create if needed.

        Returns:
            Active connection pool.

        Raises:
            RuntimeError: If pool cannot be created.
        """
        if self._pool is None:
            await self.connect()
        if self._pool is None:
            raise RuntimeError("PostgreSQL pool not available")
        return self._pool
