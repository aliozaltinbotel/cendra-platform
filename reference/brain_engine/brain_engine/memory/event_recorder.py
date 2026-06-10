"""Event Recorder — Automatically records all flow events to persistent memory.

Hooks into the Brain Engine flows to capture every meaningful event:
- Guest check-in/check-out
- Late checkout requests and decisions
- Cleaner assignments and status updates
- Photo inspections and damage detection
- Damage claims submitted/approved/denied
- Phone calls made and their outcomes
- Escalations and resolutions

Usage in flows:
    recorder = EventRecorder(guest_history_store, episodic_memory)
    await recorder.record_guest_identified(guest_name, phone, booking_id, property_id)
    await recorder.record_late_checkout_requested(checkout_time, fee)
    await recorder.record_damage_detected(description, severity, items)
    await recorder.record_claim_submitted(claim_id, amount)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.guest_history import (
    BookingRecord,
    GuestHistoryStore,
    IncidentRecord,
)

logger = logging.getLogger(__name__)


class EventRecorder:
    """Records flow events to both episodic memory and guest history.

    Dual-write: every event goes to:
    1. EpisodicMemory (for session-level temporal queries)
    2. GuestHistoryStore (for cross-session guest/property history)
    """

    def __init__(
        self,
        history: GuestHistoryStore,
        episodic: EpisodicMemory,
    ) -> None:
        self._history = history
        self._episodic = episodic
        self._current_incident: IncidentRecord | None = None
        self._current_guest_id: str | None = None

    @property
    def current_incident(self) -> IncidentRecord | None:
        return self._current_incident

    # ── Guest & Booking ───────────────────────────────────────────────── #

    async def record_guest_identified(
        self,
        guest_name: str,
        phone: str | None = None,
        booking_id: str | None = None,
        property_id: str | None = None,
        property_name: str = "",
        check_in: str = "",
        check_out: str = "",
        **kwargs: Any,
    ) -> str:
        """Record that a guest has been identified. Returns guest_id."""
        guest = await self._history.get_or_create_guest(
            name=guest_name, phone=phone, **kwargs
        )
        self._current_guest_id = guest.guest_id

        # Save booking if provided
        if booking_id:
            booking = BookingRecord(
                booking_id=booking_id,
                guest_id=guest.guest_id,
                guest_name=guest_name,
                property_id=property_id or "",
                property_name=property_name,
                check_in=check_in,
                check_out=check_out,
            )
            await self._history.save_booking(booking)

        await self._episodic.add_episode(
            event="guest_identified",
            content=f"Guest identified: {guest_name} (ID: {guest.guest_id})",
            metadata={
                "guest_id": guest.guest_id,
                "guest_name": guest_name,
                "phone": phone,
                "booking_id": booking_id,
                "property_id": property_id,
            },
        )

        logger.info("Guest identified: %s (%s)", guest_name, guest.guest_id)
        return guest.guest_id

    # ── Incident Lifecycle ────────────────────────────────────────────── #

    async def record_incident_started(
        self,
        incident_type: str,
        guest_name: str,
        guest_id: str | None = None,
        booking_id: str = "",
        property_id: str = "",
        property_name: str = "",
        severity: int = 1,
    ) -> str:
        """Start tracking a new incident. Returns incident_id."""
        now = datetime.now(timezone.utc).isoformat()
        gid = guest_id or self._current_guest_id or ""

        incident = IncidentRecord(
            incident_id=f"INC-{now[:10].replace('-', '')}-{id(self) % 10000:04d}",
            booking_id=booking_id,
            guest_id=gid,
            guest_name=guest_name,
            property_id=property_id,
            property_name=property_name,
            incident_type=incident_type,
            status="open",
            severity=severity,
            created_at=now,
        )
        incident.add_event("incident_started", f"New {incident_type} incident for {guest_name}")

        self._current_incident = incident
        await self._history.save_incident(incident)

        await self._episodic.add_episode(
            event="incident_started",
            content=f"Incident started: {incident_type} for {guest_name}",
            metadata={"incident_id": incident.incident_id, "type": incident_type},
        )

        logger.info("Incident started: %s (%s)", incident.incident_id, incident_type)
        return incident.incident_id

    async def record_incident_update(self, event: str, details: str = "", **metadata: Any) -> None:
        """Record an event on the current incident."""
        if self._current_incident:
            self._current_incident.add_event(event, details, metadata)
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event=event,
            content=details,
            metadata=metadata,
        )

    async def record_incident_resolved(self, summary: str = "") -> None:
        """Mark current incident as resolved."""
        if self._current_incident:
            self._current_incident.status = "resolved"
            self._current_incident.resolved_at = datetime.now(timezone.utc).isoformat()
            self._current_incident.resolution_summary = summary
            self._current_incident.add_event("incident_resolved", summary)
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="incident_resolved",
            content=summary,
        )

    # ── Late Checkout ─────────────────────────────────────────────────── #

    async def record_late_checkout_requested(
        self, checkout_time: str, fee: float | None = None
    ) -> None:
        if self._current_incident:
            self._current_incident.late_checkout_time = checkout_time
            self._current_incident.late_checkout_fee = fee
            self._current_incident.add_event(
                "late_checkout_requested",
                f"Late checkout requested: {checkout_time}, fee: ${fee}",
            )
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="late_checkout_requested",
            content=f"Guest requested late checkout at {checkout_time}",
            metadata={"checkout_time": checkout_time, "fee": fee},
        )

    async def record_late_checkout_decision(self, approved: bool, reason: str = "") -> None:
        if self._current_incident:
            self._current_incident.late_checkout_approved = approved
            self._current_incident.add_event(
                "late_checkout_decision",
                f"Late checkout {'approved' if approved else 'denied'}: {reason}",
            )
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="late_checkout_decision",
            content=f"Late checkout {'approved' if approved else 'denied'}. {reason}",
            metadata={"approved": approved},
        )

    # ── Cleaner Coordination ──────────────────────────────────────────── #

    async def record_cleaner_assigned(
        self, cleaner_name: str, cleaner_phone: str, arrival_time: str
    ) -> None:
        if self._current_incident:
            self._current_incident.cleaner_name = cleaner_name
            self._current_incident.cleaner_phone = cleaner_phone
            self._current_incident.cleaner_arrival_time = arrival_time
            self._current_incident.add_event(
                "cleaner_assigned",
                f"Cleaner {cleaner_name} assigned, arriving at {arrival_time}",
            )
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="cleaner_assigned",
            content=f"Cleaner {cleaner_name} assigned for {arrival_time}",
            metadata={"cleaner_name": cleaner_name, "arrival_time": arrival_time},
        )

    async def record_cleaning_completed(self, notes: str = "") -> None:
        if self._current_incident:
            self._current_incident.cleaning_completed = True
            self._current_incident.cleaning_notes = notes
            self._current_incident.add_event("cleaning_completed", notes)
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="cleaning_completed", content=notes,
        )

    # ── Photo Inspection ──────────────────────────────────────────────── #

    async def record_photos_received(self, before_count: int, after_count: int) -> None:
        if self._current_incident:
            self._current_incident.add_event(
                "photos_received",
                f"Received {before_count} before + {after_count} after photos",
            )
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="photos_received",
            content=f"{before_count} before, {after_count} after photos",
        )

    async def record_damage_detected(
        self,
        description: str,
        severity: int,
        items: list[str] | None = None,
        confidence: float | None = None,
    ) -> None:
        if self._current_incident:
            self._current_incident.damage_detected = True
            self._current_incident.damage_description = description
            self._current_incident.damage_severity = severity
            self._current_incident.damage_items = items or []
            self._current_incident.analysis_confidence = confidence
            self._current_incident.add_event(
                "damage_detected",
                f"Damage found: {description} (severity: {severity}/5)",
                {"items": items, "confidence": confidence},
            )
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="damage_detected",
            content=f"Damage detected: {description}",
            metadata={"severity": severity, "items": items},
        )

    async def record_no_damage(self) -> None:
        if self._current_incident:
            self._current_incident.damage_detected = False
            self._current_incident.add_event("no_damage", "No damage detected")
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="no_damage", content="Photo inspection: no damage found",
        )

    # ── Damage Claims ─────────────────────────────────────────────────── #

    async def record_claim_submitted(
        self, claim_id: str, amount: float, deadline: str = ""
    ) -> None:
        if self._current_incident:
            self._current_incident.claim_id = claim_id
            self._current_incident.claim_amount = amount
            self._current_incident.claim_status = "submitted"
            self._current_incident.claim_deadline = deadline
            self._current_incident.add_event(
                "claim_submitted",
                f"Claim {claim_id} submitted for ${amount}",
                {"deadline": deadline},
            )
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="claim_submitted",
            content=f"Damage claim submitted: {claim_id} for ${amount}",
            metadata={"claim_id": claim_id, "amount": amount},
        )

    async def record_claim_status_changed(self, claim_id: str, new_status: str) -> None:
        if self._current_incident:
            self._current_incident.claim_status = new_status
            self._current_incident.add_event(
                "claim_status_changed",
                f"Claim {claim_id} status: {new_status}",
            )
            await self._history.save_incident(self._current_incident)

        await self._episodic.add_episode(
            event="claim_status_changed",
            content=f"Claim {claim_id} → {new_status}",
            metadata={"claim_id": claim_id, "status": new_status},
        )

    # ── Phone Calls ───────────────────────────────────────────────────── #

    async def record_call_made(
        self, to_name: str, to_phone: str, purpose: str, outcome: str = ""
    ) -> None:
        await self.record_incident_update(
            event="call_made",
            details=f"Called {to_name} ({to_phone}): {purpose}. Outcome: {outcome}",
            to_name=to_name,
            to_phone=to_phone,
            purpose=purpose,
            outcome=outcome,
        )

    # ── Context Retrieval ─────────────────────────────────────────────── #

    async def get_guest_context(self, guest_id: str | None = None) -> str:
        """Get full guest context for LLM prompt injection."""
        gid = guest_id or self._current_guest_id
        if not gid:
            return ""
        return await self._history.build_guest_context(gid)

    async def get_property_context(self, property_id: str) -> str:
        """Get property history context for LLM prompt injection."""
        return await self._history.build_property_context(property_id)
