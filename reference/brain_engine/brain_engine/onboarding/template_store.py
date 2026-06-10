"""Template Store — Redis-backed persistence for rule templates.

Templates are collections of rule definitions that can be applied
to one or many properties at once for rapid customer onboarding.

Key structure:
    brain:template:{template_id}    → Template JSON
    brain:template:all              → Set of all template_ids
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class TemplateStore:
    """Redis-backed store for rule templates.

    Args:
        redis_url: Redis connection URL.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        workspace_id: str = "",
    ) -> None:
        import redis.asyncio as aioredis
        from brain_engine.memory.tenant import build_prefix
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = build_prefix("brain:template:", workspace_id)

    def _key(self, *parts: str) -> str:
        """Build a Redis key from parts.

        Args:
            parts: Key segments.

        Returns:
            Full Redis key string.
        """
        return self._prefix + ":".join(parts)

    async def create(
        self,
        template_name: str,
        description: str = "",
        rules: list[dict[str, Any]] | None = None,
        version: str = "1.0",
    ) -> dict[str, Any]:
        """Create a new template.

        Args:
            template_name: Human-readable name.
            description: Template description.
            rules: List of rule definition dicts.
            version: Version string.

        Returns:
            Created template dict.
        """
        now = datetime.now(timezone.utc).isoformat()
        template_id = f"tpl_{uuid.uuid4().hex[:12]}"

        template: dict[str, Any] = {
            "template_id": template_id,
            "template_name": template_name,
            "description": description,
            "rules": rules or [],
            "version": version,
            "created_at": now,
            "updated_at": now,
        }

        pipe = self._redis.pipeline()
        pipe.set(self._key(template_id), json.dumps(template))
        pipe.sadd(self._key("all"), template_id)
        await pipe.execute()

        logger.info("Created template: %s (%s)", template_name, template_id)
        return template

    async def get(self, template_id: str) -> dict[str, Any] | None:
        """Get a template by ID.

        Args:
            template_id: Template identifier.

        Returns:
            Template dict or None.
        """
        raw = await self._redis.get(self._key(template_id))
        if raw:
            return json.loads(raw)
        return None

    async def list_all(self) -> list[dict[str, Any]]:
        """List all templates.

        Returns:
            List of template dicts, sorted by name.
        """
        template_ids = await self._redis.smembers(self._key("all"))
        templates: list[dict[str, Any]] = []

        for tid in template_ids:
            template = await self.get(tid)
            if template:
                templates.append(template)

        return sorted(templates, key=lambda t: t.get("template_name", ""))

    async def update(
        self,
        template_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update a template.

        Args:
            template_id: Template to update.
            updates: Fields to update.

        Returns:
            Updated template dict or None if not found.
        """
        template = await self.get(template_id)
        if not template:
            return None

        for key, value in updates.items():
            if key != "template_id":
                template[key] = value

        template["updated_at"] = datetime.now(timezone.utc).isoformat()

        await self._redis.set(
            self._key(template_id), json.dumps(template),
        )
        logger.info("Updated template: %s", template_id)
        return template

    async def delete(self, template_id: str) -> bool:
        """Delete a template.

        Args:
            template_id: Template to delete.

        Returns:
            True if deleted, False if not found.
        """
        template = await self.get(template_id)
        if not template:
            return False

        pipe = self._redis.pipeline()
        pipe.delete(self._key(template_id))
        pipe.srem(self._key("all"), template_id)
        await pipe.execute()

        logger.info("Deleted template: %s", template_id)
        return True

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.close()
