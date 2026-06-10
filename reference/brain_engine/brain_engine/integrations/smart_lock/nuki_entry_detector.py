"""Nuki Entry Detector — Detects when a guest enters the property.

Monitors the Nuki smart lock for door-open / unlock events using two strategies:
1. Webhook: Instant notification from Nuki when lock state changes.
2. Polling fallback: Periodically checks the activity log if webhook is unavailable.

Usage::

    detector = NukiEntryDetector(nuki_client, lock_id="12345")

    # Option A — wait (blocks until entry or timeout)
    entry = await detector.wait_for_entry(timeout_seconds=3600)

    # Option B — webhook push (called from the /api/nuki/webhook endpoint)
    entry = detector.handle_webhook(payload)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# Nuki lock action constants (from Nuki Web API docs)
ACTION_UNLOCK = 1
ACTION_LOCK = 2
ACTION_UNLATCH = 3
ACTION_LOCK_N_GO = 4
ACTION_LOCK_N_GO_UNLATCH = 5

# Trigger types
TRIGGER_SYSTEM = 0
TRIGGER_MANUAL = 1
TRIGGER_BUTTON = 2
TRIGGER_AUTO = 3
TRIGGER_KEYPAD = 6

ENTRY_ACTIONS = {ACTION_UNLOCK, ACTION_UNLATCH, ACTION_LOCK_N_GO_UNLATCH}


@dataclass
class EntryEvent:
    """Represents a detected guest entry."""

    lock_id: str
    timestamp: datetime
    trigger: str  # "keypad", "app", "manual", "auto", "button", "system"
    action: str  # "unlock", "unlatch", etc.
    user_name: str = ""
    access_code_used: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _trigger_label(trigger_type: int) -> str:
    """Convert Nuki trigger type int to human label."""
    return {
        TRIGGER_SYSTEM: "system",
        TRIGGER_MANUAL: "manual",
        TRIGGER_BUTTON: "button",
        TRIGGER_AUTO: "auto",
        TRIGGER_KEYPAD: "keypad",
    }.get(trigger_type, f"unknown({trigger_type})")


def _action_label(action: int) -> str:
    """Convert Nuki action int to human label."""
    return {
        ACTION_UNLOCK: "unlock",
        ACTION_LOCK: "lock",
        ACTION_UNLATCH: "unlatch",
        ACTION_LOCK_N_GO: "lock_n_go",
        ACTION_LOCK_N_GO_UNLATCH: "lock_n_go_unlatch",
    }.get(action, f"unknown({action})")


class NukiEntryDetector:
    """Detects guest entry via Nuki smart lock events.

    Supports both webhook-driven and polling-based detection.

    Args:
        nuki: NukiLock client instance.
        lock_id: The lock to monitor.
        polling_interval: Seconds between activity log polls (default 30).
        on_entry: Optional async callback fired when entry is detected.
    """

    def __init__(
        self,
        nuki: Any,  # NukiLock — using Any to avoid circular import
        lock_id: str,
        *,
        polling_interval: int = 30,
        on_entry: Callable[[EntryEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._nuki = nuki
        self._lock_id = lock_id
        self._polling_interval = polling_interval
        self._on_entry = on_entry
        self._detected_entry: EntryEvent | None = None
        self._entry_event = asyncio.Event()
        self._last_seen_log_id: str | None = None
        self._polling_task: asyncio.Task[None] | None = None

    @property
    def detected_entry(self) -> EntryEvent | None:
        """The most recently detected entry event, or None."""
        return self._detected_entry

    # ── Webhook-based detection ──────────────────────────────────────── #

    def handle_webhook(self, payload: dict[str, Any]) -> EntryEvent | None:
        """Process a Nuki webhook payload and detect entry events.

        Called from the webhook endpoint when Nuki sends a state change notification.

        Args:
            payload: Raw webhook JSON body from Nuki.

        Returns:
            EntryEvent if an entry was detected, None otherwise.
        """
        lock_id = str(payload.get("smartlockId", payload.get("nukiId", "")))
        if lock_id != self._lock_id:
            logger.debug("Webhook for different lock %s (monitoring %s)", lock_id, self._lock_id)
            return None

        action = payload.get("action", payload.get("state", {}).get("lastAction", 0))
        trigger = payload.get("trigger", payload.get("state", {}).get("trigger", 0))

        if action not in ENTRY_ACTIONS:
            logger.debug("Webhook action %s is not an entry action", action)
            return None

        entry = EntryEvent(
            lock_id=self._lock_id,
            timestamp=datetime.now(timezone.utc),
            trigger=_trigger_label(trigger),
            action=_action_label(action),
            user_name=payload.get("name", payload.get("authName", "")),
            access_code_used=str(payload.get("code", "")),
            raw=payload,
        )

        self._set_entry(entry)
        return entry

    # ── Polling-based detection ──────────────────────────────────────── #

    async def start_polling(self) -> None:
        """Start background polling of the Nuki activity log."""
        if self._polling_task and not self._polling_task.done():
            logger.warning("Polling already running for lock %s", self._lock_id)
            return

        # Seed the last-seen ID so we only detect NEW events
        await self._seed_last_seen()
        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info("Started polling for lock %s every %ds", self._lock_id, self._polling_interval)

    async def stop_polling(self) -> None:
        """Stop the background polling task."""
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped polling for lock %s", self._lock_id)

    async def _seed_last_seen(self) -> None:
        """Fetch current log to establish baseline (ignore existing entries)."""
        try:
            logs = await self._nuki.get_activity_log(self._lock_id, limit=1)
            if logs:
                self._last_seen_log_id = str(logs[0].get("id", ""))
        except Exception:
            logger.warning("Could not seed activity log for lock %s", self._lock_id)

    async def _poll_loop(self) -> None:
        """Continuously poll the activity log for new entry events."""
        while True:
            try:
                await asyncio.sleep(self._polling_interval)
                entry = await self.check_entry()
                if entry:
                    logger.info("Entry detected via polling: %s", entry)
                    break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error polling lock %s activity log", self._lock_id)

    async def check_entry(self) -> EntryEvent | None:
        """Check the activity log once for new entry events.

        Returns:
            EntryEvent if a new entry was found, None otherwise.
        """
        try:
            logs = await self._nuki.get_activity_log(self._lock_id, limit=10)
        except Exception:
            logger.exception("Failed to fetch activity log for lock %s", self._lock_id)
            return None

        for log_entry in logs:
            log_id = str(log_entry.get("id", ""))

            # Stop at already-seen entries
            if log_id == self._last_seen_log_id:
                break

            action = log_entry.get("action", 0)
            if action not in ENTRY_ACTIONS:
                continue

            trigger = log_entry.get("trigger", 0)
            timestamp_str = log_entry.get("date", "")
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                timestamp = datetime.now(timezone.utc)

            entry = EntryEvent(
                lock_id=self._lock_id,
                timestamp=timestamp,
                trigger=_trigger_label(trigger),
                action=_action_label(action),
                user_name=log_entry.get("name", log_entry.get("authName", "")),
                access_code_used=str(log_entry.get("code", "")),
                raw=log_entry,
            )

            # Update last-seen to newest
            if logs:
                self._last_seen_log_id = str(logs[0].get("id", ""))

            self._set_entry(entry)
            return entry

        return None

    # ── Wait for entry ───────────────────────────────────────────────── #

    async def wait_for_entry(self, *, timeout_seconds: int = 3600) -> EntryEvent | None:
        """Block until an entry is detected or timeout expires.

        Works with both webhook and polling — whichever fires first.

        Args:
            timeout_seconds: Maximum time to wait (default 1 hour).

        Returns:
            EntryEvent if detected, None on timeout.
        """
        self._entry_event.clear()
        try:
            await asyncio.wait_for(
                self._entry_event.wait(),
                timeout=timeout_seconds,
            )
            return self._detected_entry
        except asyncio.TimeoutError:
            logger.warning(
                "Entry detection timed out after %ds for lock %s",
                timeout_seconds,
                self._lock_id,
            )
            return None

    # ── Internal ─────────────────────────────────────────────────────── #

    def _set_entry(self, entry: EntryEvent) -> None:
        """Store entry and signal waiters."""
        self._detected_entry = entry
        self._entry_event.set()
        logger.info(
            "Entry detected on lock %s: %s via %s at %s",
            entry.lock_id,
            entry.action,
            entry.trigger,
            entry.timestamp.isoformat(),
        )
        if self._on_entry:
            asyncio.create_task(self._on_entry(entry))
