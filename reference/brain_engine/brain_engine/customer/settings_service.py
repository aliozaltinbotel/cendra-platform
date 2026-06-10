"""Customer settings service — Redis-cached per-tenant configuration.

Loads customer AI settings from Redis with in-memory TTL cache.
Falls back to default settings when customer has no configuration.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from brain_engine.customer.models import (
    CustomerGuardrail,
    CustomerSettings,
    CustomerTag,
    GuardrailPriority,
    ToneType,
    ToolToggle,
)

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300  # 5 minutes
_REDIS_KEY_PREFIX = "CustomerAISettings"


class CustomerSettingsService:
    """Loads and caches customer AI settings from Redis.

    Uses a two-level cache: in-memory dict with TTL, backed by
    Redis. Falls back to default settings if not found.

    Args:
        redis_client: Async Redis client instance.
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._cache: dict[str, tuple[CustomerSettings, float]] = {}

    async def get_settings(self, customer_id: str) -> CustomerSettings:
        """Load settings for a customer, using cache when fresh.

        Args:
            customer_id: Customer identifier.

        Returns:
            CustomerSettings (from cache, Redis, or defaults).
        """
        cached = self._get_from_cache(customer_id)
        if cached:
            return cached

        settings = await self._load_from_redis(customer_id)
        if not settings:
            settings = _default_settings(customer_id)

        self._put_in_cache(customer_id, settings)
        return settings

    async def save_settings(
        self,
        settings: CustomerSettings,
    ) -> None:
        """Save customer settings to Redis.

        Args:
            settings: Settings to persist.
        """
        if not self._redis:
            logger.warning("No Redis client — settings not persisted")
            return

        key = f"{_REDIS_KEY_PREFIX}:{settings.customer_id}"
        data = settings.model_dump_json()
        await self._redis.set(key, data, ex=86400)  # 24h TTL
        self._put_in_cache(settings.customer_id, settings)

    async def invalidate(self, customer_id: str) -> None:
        """Remove cached settings for a customer.

        Args:
            customer_id: Customer to invalidate.
        """
        self._cache.pop(customer_id, None)
        if self._redis:
            key = f"{_REDIS_KEY_PREFIX}:{customer_id}"
            await self._redis.delete(key)

    def _get_from_cache(self, customer_id: str) -> CustomerSettings | None:
        """Check in-memory cache for fresh settings.

        Args:
            customer_id: Customer identifier.

        Returns:
            Settings if cached and not expired, else None.
        """
        entry = self._cache.get(customer_id)
        if not entry:
            return None

        settings, cached_at = entry
        if time.monotonic() - cached_at > _CACHE_TTL_SECONDS:
            del self._cache[customer_id]
            return None

        return settings

    def _put_in_cache(
        self,
        customer_id: str,
        settings: CustomerSettings,
    ) -> None:
        """Store settings in memory cache.

        Args:
            customer_id: Customer identifier.
            settings: Settings to cache.
        """
        self._cache[customer_id] = (settings, time.monotonic())

    async def _load_from_redis(
        self,
        customer_id: str,
    ) -> CustomerSettings | None:
        """Load settings from Redis.

        Args:
            customer_id: Customer identifier.

        Returns:
            Parsed CustomerSettings or None.
        """
        if not self._redis:
            return None

        key = f"{_REDIS_KEY_PREFIX}:{customer_id}"
        try:
            raw = await self._redis.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            return CustomerSettings(**data)
        except Exception:
            logger.warning(
                "Failed to load settings from Redis for %s",
                customer_id, exc_info=True,
            )
            return None


def _default_settings(customer_id: str) -> CustomerSettings:
    """Create default settings when none are configured.

    Args:
        customer_id: Customer identifier.

    Returns:
        CustomerSettings with sensible defaults.
    """
    return CustomerSettings(
        customer_id=customer_id,
        tone_type=ToneType.DEFAULT,
        tools=ToolToggle(),
        guardrails=[],
        tags=[],
    )
