"""Shared Redis-backed conversation history helpers.

Used by the AG-UI streaming endpoint (api_server/server.py),
the Cendra direct handler (cendra_adapter.py), and the durable
guest pipeline (durable_guest.py).

History is keyed by `conv:{property_id}:{guest_id}` with a
30-day TTL and a soft cap of 50 turns (100 list entries —
each turn = user + assistant). All errors are swallowed and
logged; callers never see Redis exceptions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

CONV_TTL_SECONDS = 30 * 86400
CONV_MAX_TURNS = 50


def _key(property_id: str, guest_id: str) -> str:
    """Compose the Redis list key for a conversation."""
    return f"conv:{property_id}:{guest_id}"


async def load_conversation_history(
    *, redis: Any | None, guest_id: str, property_id: str,
) -> list[dict[str, str]]:
    """Load all stored turns for a guest's conversation.

    Args:
        redis: Async Redis client (or ``None`` when not configured).
        guest_id: Guest identifier.
        property_id: Property identifier.

    Returns:
        Ordered list of ``{role, content}`` dicts. Empty when
        Redis is unavailable, ``guest_id`` is missing, or the
        list does not exist yet.
    """
    if not redis or not guest_id:
        return []
    try:
        raw = await redis.lrange(_key(property_id, guest_id), 0, -1)
        return [json.loads(r) for r in raw]
    except Exception:
        logger.warning("Failed to load conversation for %s", guest_id)
        return []


async def save_conversation_turn(
    *,
    redis: Any | None,
    guest_id: str,
    property_id: str,
    user_message: str,
    assistant_reply: str,
) -> None:
    """Persist a (user, assistant) turn pair to the conversation list.

    Args:
        redis: Async Redis client (or ``None`` when not configured).
        guest_id: Guest identifier.
        property_id: Property identifier.
        user_message: Latest guest message text.
        assistant_reply: Brain's reply text.
    """
    if not redis or not guest_id:
        return
    key = _key(property_id, guest_id)
    try:
        user_turn = json.dumps({"role": "user", "content": user_message})
        asst_turn = json.dumps({"role": "assistant", "content": assistant_reply})
        await redis.rpush(key, user_turn, asst_turn)
        await redis.ltrim(key, -(CONV_MAX_TURNS * 2), -1)
        await redis.expire(key, CONV_TTL_SECONDS)
    except Exception:
        logger.warning("Failed to save conversation for %s", guest_id)
