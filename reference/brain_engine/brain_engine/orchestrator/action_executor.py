"""
Action Executor - executes discrete actions by calling the appropriate integration.

Each action type maps to an integration method. The executor handles errors
gracefully and returns structured results.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """A discrete action to be executed by an integration."""

    action_type: str
    params: dict[str, Any] = field(default_factory=dict)
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    requires_confirmation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    """Result of executing an action."""

    action_id: str
    action_type: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    summary: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for AG-UI event payloads."""
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "summary": self.summary,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class ActionExecutor:
    """
    Executes actions by dispatching to the correct integration client.

    Each action type is mapped to a handler method. Unknown action types
    return a graceful error result instead of raising.

    Usage::

        executor = ActionExecutor(
            voice=elevenlabs_client,
            messaging=whatsapp_client,
            lock=nuki_client,
            ...
        )
        result = await executor.execute(
            Action(action_type="make_call", params={...})
        )
    """

    def __init__(
        self,
        *,
        voice: Any | None = None,
        messaging: Any | None = None,
        lock: Any | None = None,
        cleaning: Any | None = None,
        calendar: Any | None = None,
        airbnb: Any | None = None,
        comparator: Any | None = None,
    ) -> None:
        self._voice = voice
        self._messaging = messaging
        self._lock = lock
        self._cleaning = cleaning
        self._calendar = calendar
        self._airbnb = airbnb
        self._comparator = comparator

        self._handlers: dict[str, Any] = {
            "make_call": self._handle_make_call,
            "get_call_status": self._handle_get_call_status,
            "send_message": self._handle_send_message,
            "generate_access_code": self._handle_generate_access_code,
            "unlock_property": self._handle_unlock,
            "lock_property": self._handle_lock,
            "find_cleaners": self._handle_find_cleaners,
            "schedule_cleaning": self._handle_schedule_cleaning,
            "get_cleaning_status": self._handle_get_cleaning_status,
            "create_calendar_event": self._handle_create_calendar_event,
            "reservation_lookup": self._handle_reservation_lookup,
            "submit_claim": self._handle_submit_claim,
            "get_claim_status": self._handle_get_claim_status,
            "compare_photos": self._handle_compare_photos,
        }

    async def execute(self, action: Action) -> ActionResult:
        """Execute a single action and return the result.

        Args:
            action: The :class:`Action` to execute.

        Returns:
            :class:`ActionResult` with success/failure and data.
        """
        start = time.monotonic()
        handler = self._handlers.get(action.action_type)

        if handler is None:
            logger.warning("Unknown action type: %s", action.action_type)
            return ActionResult(
                action_id=action.action_id,
                action_type=action.action_type,
                success=False,
                error=f"Unknown action type: {action.action_type}",
                summary=f"Action '{action.action_type}' is not recognized.",
            )

        try:
            result = await handler(action.params)
            duration = (time.monotonic() - start) * 1000
            logger.info(
                "Action %s (%s) completed in %.1fms",
                action.action_id,
                action.action_type,
                duration,
            )
            return ActionResult(
                action_id=action.action_id,
                action_type=action.action_type,
                success=True,
                data=result.get("data", {}),
                summary=result.get("summary", "Action completed successfully."),
                duration_ms=duration,
            )
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            logger.exception(
                "Action %s (%s) failed after %.1fms",
                action.action_id,
                action.action_type,
                duration,
            )
            return ActionResult(
                action_id=action.action_id,
                action_type=action.action_type,
                success=False,
                error=str(exc),
                summary=f"Action '{action.action_type}' failed: {exc}",
                duration_ms=duration,
            )

    # ------------------------------------------------------------------
    # Handler implementations
    # ------------------------------------------------------------------

    async def _handle_make_call(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._voice:
            raise RuntimeError("No voice provider configured")
        result = await self._voice.make_call(
            phone=params["phone"],
            script=params.get("script", ""),
        )
        return {
            "data": {"call_id": result.call_id, "status": result.status},
            "summary": f"Phone call initiated to {params['phone']}.",
        }

    async def _handle_get_call_status(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._voice:
            raise RuntimeError("No voice provider configured")
        status = await self._voice.get_call_status(params["call_id"])
        return {
            "data": {"call_id": status.call_id, "status": status.status},
            "summary": f"Call {status.call_id} status: {status.status}.",
        }

    async def _handle_send_message(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._messaging:
            raise RuntimeError("No messaging provider configured")
        result = await self._messaging.send_message(
            params["recipient"], params["text"]
        )
        return {
            "data": {"message_id": getattr(result, "message_id", "")},
            "summary": f"Message sent to {params['recipient']}.",
        }

    async def _handle_generate_access_code(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._lock:
            raise RuntimeError("No smart lock provider configured")
        lock_id = params.get("lock_id", "")
        if not lock_id:
            return {
                "data": {},
                "summary": "Lock ID is required to generate an access code.",
            }
        now = datetime.now()
        code = await self._lock.create_access_code(
            lock_id=lock_id,
            name=params.get("guest_name", "Guest"),
            start=params.get("start", now),
            end=params.get("end", now.replace(hour=23, minute=59)),
        )
        return {
            "data": {"code": getattr(code, "code", ""), "code_id": getattr(code, "code_id", "")},
            "summary": f"Access code generated for lock {lock_id}.",
        }

    async def _handle_unlock(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._lock:
            raise RuntimeError("No smart lock provider configured")
        status = await self._lock.unlock(params["lock_id"])
        return {
            "data": {"state": getattr(status, "state", "unlocked")},
            "summary": f"Lock {params['lock_id']} unlocked.",
        }

    async def _handle_lock(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._lock:
            raise RuntimeError("No smart lock provider configured")
        status = await self._lock.lock(params.get("lock_id", ""))
        return {
            "data": {"state": getattr(status, "state", "locked")},
            "summary": "Property locked.",
        }

    async def _handle_find_cleaners(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._cleaning:
            raise RuntimeError("No cleaning provider configured")
        date_str = params.get("date", date.today().isoformat())
        cleaning_date = (
            date.fromisoformat(date_str) if isinstance(date_str, str) else date_str
        )
        cleaners = await self._cleaning.get_available_cleaners(
            cleaning_date, params.get("property_id", "")
        )
        cleaner_list = [
            {"id": getattr(c, "cleaner_id", ""), "name": getattr(c, "name", "")}
            for c in cleaners
        ]
        return {
            "data": {"cleaners": cleaner_list},
            "summary": f"Found {len(cleaner_list)} available cleaner(s).",
        }

    async def _handle_schedule_cleaning(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._cleaning:
            raise RuntimeError("No cleaning provider configured")
        date_str = params.get("date", date.today().isoformat())
        cleaning_date = (
            date.fromisoformat(date_str) if isinstance(date_str, str) else date_str
        )
        job = await self._cleaning.assign_cleaner(
            cleaner_id=params.get("cleaner_id", ""),
            property_id=params.get("property_id", ""),
            cleaning_date=cleaning_date,
        )
        return {
            "data": {"job_id": getattr(job, "job_id", ""), "status": getattr(job, "status", "")},
            "summary": f"Cleaning scheduled (job {getattr(job, 'job_id', 'N/A')}).",
        }

    async def _handle_get_cleaning_status(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._cleaning:
            raise RuntimeError("No cleaning provider configured")
        job = await self._cleaning.get_cleaning_status(params["job_id"])
        return {
            "data": {"job_id": job.job_id, "status": job.status},
            "summary": f"Cleaning job {job.job_id}: {job.status}.",
        }

    async def _handle_create_calendar_event(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._calendar:
            raise RuntimeError("No calendar provider configured")
        event = await self._calendar.create_event(
            title=params["title"],
            start=datetime.fromisoformat(params["start"]),
            end=datetime.fromisoformat(params["end"]),
            description=params.get("description"),
        )
        return {
            "data": {"event_id": event.event_id},
            "summary": f"Calendar event '{params['title']}' created.",
        }

    async def _handle_reservation_lookup(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._airbnb:
            raise RuntimeError("No Airbnb API configured")
        reservation = await self._airbnb.get_reservation(
            params["reservation_id"]
        )
        return {
            "data": {
                "reservation_id": reservation.reservation_id,
                "guest_name": reservation.guest_name,
                "check_in": reservation.check_in,
                "check_out": reservation.check_out,
                "status": reservation.status,
            },
            "summary": (
                f"Reservation {reservation.reservation_id}: "
                f"{reservation.guest_name}, "
                f"{reservation.check_in} to {reservation.check_out}."
            ),
        }

    async def _handle_submit_claim(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._airbnb:
            raise RuntimeError("No Airbnb API configured")
        claim = await self._airbnb.submit_claim(
            reservation_id=params.get("reservation_id", ""),
            claim_data=params,
        )
        return {
            "data": {"claim_id": claim.claim_id, "status": claim.status},
            "summary": f"Damage claim {claim.claim_id} submitted ({claim.status}).",
        }

    async def _handle_get_claim_status(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._airbnb:
            raise RuntimeError("No Airbnb API configured")
        claim = await self._airbnb.get_claim_status(params["claim_id"])
        return {
            "data": {
                "claim_id": claim.claim_id,
                "status": claim.status,
                "amount_approved": claim.amount_approved,
            },
            "summary": f"Claim {claim.claim_id}: {claim.status}.",
        }

    async def _handle_compare_photos(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._comparator:
            raise RuntimeError("No photo comparator configured")
        result = await self._comparator.compare_photos(
            before_path=params.get("before_path", ""),
            after_path=params.get("after_path", ""),
            room_context=params.get("room_context"),
        )
        damages = [
            {
                "description": d.description,
                "severity": d.severity,
                "category": d.category,
                "estimated_cost": d.estimated_cost,
            }
            for d in result.damages
        ]
        return {
            "data": {
                "damages": damages,
                "overall_severity": result.overall_severity,
                "total_estimated_cost": result.total_estimated_cost,
                "no_damage": result.no_damage_detected,
            },
            "summary": result.summary or "Photo comparison completed.",
        }
