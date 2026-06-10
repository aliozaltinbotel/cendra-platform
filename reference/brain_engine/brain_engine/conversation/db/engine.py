"""Async database engine factory for conversation history.

Provides a singleton async engine and session factory.
Supports MySQL (asyncmy), PostgreSQL (asyncpg), and SQLite (aiosqlite).
Connection URL resolved from env or Azure Key Vault.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from brain_engine.conversation.db.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _resolve_database_url() -> str:
    """Resolve database URL from environment.

    Priority:
    1. DATABASE_URL env var
    2. Fallback to SQLite for development

    Returns:
        Async database URL string.
    """
    url = os.getenv("DATABASE_URL", "")
    if url:
        if url.startswith("mysql://"):
            url = url.replace("mysql://", "mysql+asyncmy://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    # Dev fallback: SQLite
    return "sqlite+aiosqlite:///conversations.db"


def _build_engine_kwargs(url: str) -> dict[str, Any]:
    """Build engine creation kwargs based on driver.

    Args:
        url: Database URL.

    Returns:
        Dict of kwargs for create_async_engine.
    """
    kwargs: dict[str, Any] = {
        "echo": False,
        "pool_pre_ping": True,
    }

    if "mysql" in url:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
        kwargs["pool_recycle"] = 3600
        if "ssl=true" in url.lower() or "ssl=True" in url:
            clean_url = _strip_ssl_param(url)
            kwargs["connect_args"] = {"ssl": True}
            return {**kwargs, "_url_override": clean_url}

    if "sqlite" in url:
        kwargs.pop("pool_pre_ping", None)

    return kwargs


def _strip_ssl_param(url: str) -> str:
    """Remove ssl parameter from URL query string.

    Args:
        url: Database URL with possible ssl param.

    Returns:
        URL without ssl parameter.
    """
    import re
    url = re.sub(r"[?&]ssl=[^&]*", "", url)
    url = url.replace("?&", "?").rstrip("?")
    return url


async def get_engine() -> AsyncEngine:
    """Get or create the async engine singleton.

    Creates tables on first call.

    Returns:
        AsyncEngine instance.
    """
    global _engine
    if _engine is not None:
        return _engine

    url = _resolve_database_url()
    kwargs = _build_engine_kwargs(url)

    actual_url = kwargs.pop("_url_override", url)
    _engine = create_async_engine(actual_url, **kwargs)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database engine created: %s", _mask_url(actual_url))
    return _engine


async def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the async session factory.

    Returns:
        Session factory bound to the engine.
    """
    global _session_factory
    if _session_factory is not None:
        return _session_factory

    engine = await get_engine()
    _session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    return _session_factory


async def get_session() -> AsyncSession:
    """Create a new async session.

    Returns:
        AsyncSession ready for queries.
    """
    factory = await get_session_factory()
    return factory()


def _mask_url(url: str) -> str:
    """Mask password in URL for logging.

    Args:
        url: Database URL.

    Returns:
        URL with password replaced by ***.
    """
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
