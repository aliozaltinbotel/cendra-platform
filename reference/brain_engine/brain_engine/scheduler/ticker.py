"""Ticker — Proactive follow-up scheduler service.

Runs on a configurable interval (1-5 minutes). Each tick:
1. Queries FollowUpStore for due follow-ups
2. For each due follow-up, builds an event payload
3. Triggers Brain Engine via internal callback or HTTP

The Ticker is intentionally simple — it does NOT make decisions.
It only says "X minutes have passed, condition Y is still unmet"
and lets Brain Engine decide what to do.

Can run as:
- Background asyncio task within the FastAPI server
- Separate Docker service (via docker-compose)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from brain_engine.scheduler.follow_up_store import FollowUpStore

logger = logging.getLogger(__name__)

# Type alias for the callback that processes triggered follow-ups
TriggerCallback = Callable[[dict[str, Any]], Awaitable[None]]


class Ticker:
    """Proactive follow-up scheduler.

    Polls FollowUpStore at a fixed interval and triggers
    Brain Engine when follow-ups are due.

    Args:
        follow_up_store: FollowUpStore instance.
        on_trigger: Async callback when a follow-up fires.
        interval_seconds: Polling interval (60-300 seconds).
    """

    def __init__(
        self,
        follow_up_store: FollowUpStore,
        on_trigger: TriggerCallback | None = None,
        interval_seconds: int = 60,
    ) -> None:
        self._store = follow_up_store
        self._on_trigger = on_trigger or self._default_trigger
        self._interval = max(10, min(300, interval_seconds))
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._tick_count = 0
        self._triggered_count = 0

    @property
    def stats(self) -> dict[str, Any]:
        """Current ticker statistics.

        Returns:
            Stats dict with tick count, triggered count, running status.
        """
        return {
            "running": self._running,
            "interval_seconds": self._interval,
            "tick_count": self._tick_count,
            "triggered_count": self._triggered_count,
        }

    async def start(self) -> None:
        """Start the ticker loop as a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Ticker started: interval=%ds", self._interval,
        )

    async def stop(self) -> None:
        """Stop the ticker loop gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Ticker stopped after %d ticks", self._tick_count)

    async def tick(self) -> list[dict[str, Any]]:
        """Run a single tick: check and trigger due follow-ups.

        Returns:
            List of triggered follow-up dicts.
        """
        self._tick_count += 1
        due = await self._store.get_due()

        if not due:
            return []

        triggered: list[dict[str, Any]] = []
        for follow_up in due:
            event = self._build_trigger_event(follow_up)
            await self._store.mark_triggered(follow_up["id"])
            await self._on_trigger(event)
            triggered.append(follow_up)
            self._triggered_count += 1

        logger.info(
            "Tick #%d: %d follow-ups triggered",
            self._tick_count, len(triggered),
        )
        return triggered

    async def _loop(self) -> None:
        """Internal polling loop.

        Runs until stop() is called.
        """
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.error("Ticker error", exc_info=True)
            await asyncio.sleep(self._interval)

    @staticmethod
    def _build_trigger_event(follow_up: dict[str, Any]) -> dict[str, Any]:
        """Build an event payload from a due follow-up.

        Args:
            follow_up: The follow-up that triggered.

        Returns:
            Event dict suitable for POST /api/v1/ops/event.
        """
        return {
            "event_type": "follow_up_triggered",
            "property_id": follow_up.get("property_id", ""),
            "description": follow_up.get("description", ""),
            "event_data": {
                "follow_up_id": follow_up["id"],
                "process_id": follow_up.get("process_id", ""),
                "condition": follow_up.get("condition", ""),
                "condition_params": follow_up.get("condition_params", {}),
                "trigger_reason": (
                    f"{follow_up.get('condition', 'unknown')}_after_"
                    f"{follow_up.get('check_after_minutes', 0)}m"
                ),
                "original_created_at": follow_up.get("created_at", ""),
            },
            "priority": "high",
        }

    @staticmethod
    async def _default_trigger(event: dict[str, Any]) -> None:
        """Default trigger handler — logs the event.

        Args:
            event: Trigger event payload.
        """
        logger.info(
            "Follow-up triggered (no callback): %s — %s",
            event.get("event_data", {}).get("follow_up_id", "?"),
            event.get("description", ""),
        )
