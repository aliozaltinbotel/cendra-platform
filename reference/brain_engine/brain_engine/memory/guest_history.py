"""Guest History — Persistent storage for guest profiles, bookings, and incidents.

Stores complete guest lifecycle data in Redis:
- Guest profiles (name, phone, email, language, notes)
- Booking history (all reservations per guest)
- Incident records (damage, complaints, late checkouts)
- Damage claims (submitted, approved, denied)
- Property history (all incidents per property)

Data survives across sessions. Brain Engine can recall:
"This guest damaged the TV last time"
"This property had 3 damage claims in the last 6 months"
"Guest John always requests late checkout"
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from brain_engine.streaming.emit_helpers import emit_memory_retrieved

logger = logging.getLogger(__name__)


# ─── Data Models ──────────────────────────────────────────────────────────── #

@dataclass
class GuestProfile:
    """Persistent guest profile across all bookings."""
    guest_id: str
    name: str
    phone: str | None = None
    email: str | None = None
    language: str | None = None
    airbnb_rating: float | None = None
    identity_verified: bool = False
    total_bookings: int = 0
    total_incidents: int = 0
    total_damage_claims: int = 0
    notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # e.g., ["late_checkout_requester", "damage_prone"]
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuestProfile:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class BookingRecord:
    """A single booking/reservation record.

    Extended with richer guest/booking features for DecisionCase pattern
    learning (adults/children/infants breakdown, ADR, fees, lead time,
    booking source, payment status).
    """
    booking_id: str
    guest_id: str
    guest_name: str
    property_id: str
    property_name: str = ""
    check_in: str = ""
    check_out: str = ""
    num_guests: int = 1
    adults: int = 0
    children: int = 0
    infants: int = 0
    pets: int = 0
    total_price: float = 0.0
    currency: str = "USD"
    adr: float = 0.0
    fees_total: float = 0.0
    booking_source: str = ""
    payment_status: str = ""
    lead_time_hours: float = 0.0
    status: str = "confirmed"  # confirmed, completed, cancelled
    late_checkout_requested: bool = False
    late_checkout_granted: bool = False
    incidents: list[str] = field(default_factory=list)  # incident IDs
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BookingRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class IncidentRecord:
    """A complete incident record with full context."""
    incident_id: str
    booking_id: str
    guest_id: str
    guest_name: str
    property_id: str
    property_name: str = ""
    incident_type: str = ""  # late_checkout, damage, complaint, cleaning_issue
    status: str = "open"  # open, in_progress, resolved, escalated
    severity: int = 1  # 1-5

    # Timeline
    created_at: str = ""
    resolved_at: str | None = None

    # Late checkout details
    late_checkout_time: str | None = None
    late_checkout_fee: float | None = None
    late_checkout_approved: bool | None = None

    # Cleaner details
    cleaner_name: str | None = None
    cleaner_phone: str | None = None
    cleaner_arrival_time: str | None = None
    cleaning_completed: bool = False
    cleaning_notes: str | None = None

    # Photo inspection
    photos_before: list[str] = field(default_factory=list)
    photos_after: list[str] = field(default_factory=list)
    damage_detected: bool = False
    damage_description: str | None = None
    damage_items: list[str] = field(default_factory=list)
    damage_severity: int | None = None
    analysis_confidence: float | None = None

    # Damage claim
    claim_id: str | None = None
    claim_amount: float | None = None
    claim_status: str | None = None  # draft, submitted, under_review, approved, denied, paid
    claim_deadline: str | None = None

    # Resolution
    resolution_summary: str | None = None
    escalation_reason: str | None = None

    # Full event log
    events: list[dict[str, Any]] = field(default_factory=list)

    def add_event(self, event: str, details: str = "", metadata: dict | None = None) -> None:
        self.events.append({
            "event": event,
            "details": details,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IncidentRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PropertyHistory:
    """Summary of all incidents for a property."""
    property_id: str
    property_name: str = ""
    total_bookings: int = 0
    total_incidents: int = 0
    total_damage_claims: int = 0
    total_damage_amount: float = 0.0
    incident_ids: list[str] = field(default_factory=list)
    common_damage_locations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropertyHistory:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ─── Storage Backend ──────────────────────────────────────────────────────── #

class GuestHistoryStore:
    """Redis-backed persistent store for guest history.

    Key structure:
        brain:guest:{guest_id}              → GuestProfile JSON
        brain:guest:{guest_id}:bookings     → Sorted set of BookingRecord JSONs
        brain:guest:{guest_id}:incidents    → Sorted set of IncidentRecord JSONs
        brain:booking:{booking_id}          → BookingRecord JSON
        brain:incident:{incident_id}        → IncidentRecord JSON
        brain:property:{property_id}        → PropertyHistory JSON
        brain:property:{property_id}:incidents → Sorted set of incident IDs
        brain:guest_lookup:{phone}          → guest_id (phone → guest mapping)
        brain:guest_lookup:{name_lower}     → guest_id (name → guest mapping)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        workspace_id: str = "",
    ) -> None:
        import redis.asyncio as aioredis
        from brain_engine.memory.tenant import build_prefix
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = build_prefix("brain:", workspace_id)

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    # ── Guest Profile ─────────────────────────────────────────────────── #

    async def save_guest(self, guest: GuestProfile) -> None:
        """Save or update a guest profile."""
        key = self._key("guest", guest.guest_id)
        await self._redis.set(key, json.dumps(guest.to_dict()))

        # Index by phone and name for lookup
        if guest.phone:
            await self._redis.set(
                self._key("guest_lookup", guest.phone.replace("+", "")),
                guest.guest_id,
            )
        if guest.name:
            await self._redis.set(
                self._key("guest_lookup", guest.name.lower().replace(" ", "_")),
                guest.guest_id,
            )
        logger.info("Saved guest profile: %s (%s)", guest.name, guest.guest_id)

    async def get_guest(self, guest_id: str) -> GuestProfile | None:
        """Get a guest profile by ID."""
        raw = await self._redis.get(self._key("guest", guest_id))
        if raw:
            return GuestProfile.from_dict(json.loads(raw))
        return None

    async def find_guest_by_phone(self, phone: str) -> GuestProfile | None:
        """Find a guest by phone number."""
        guest_id = await self._redis.get(
            self._key("guest_lookup", phone.replace("+", ""))
        )
        if guest_id:
            return await self.get_guest(guest_id)
        return None

    async def find_guest_by_name(self, name: str) -> GuestProfile | None:
        """Find a guest by name (case-insensitive)."""
        guest_id = await self._redis.get(
            self._key("guest_lookup", name.lower().replace(" ", "_"))
        )
        if guest_id:
            return await self.get_guest(guest_id)
        return None

    async def get_or_create_guest(
        self, name: str, phone: str | None = None, **kwargs: Any
    ) -> GuestProfile:
        """Get existing guest or create new one."""
        # Try phone first, then name
        guest = None
        if phone:
            guest = await self.find_guest_by_phone(phone)
        if not guest:
            guest = await self.find_guest_by_name(name)

        if guest:
            # Update fields if new info provided
            if phone and not guest.phone:
                guest.phone = phone
            guest.last_seen = datetime.now(timezone.utc).isoformat()
            for k, v in kwargs.items():
                if hasattr(guest, k) and v is not None:
                    setattr(guest, k, v)
            await self.save_guest(guest)
            return guest

        # Create new guest
        now = datetime.now(timezone.utc).isoformat()
        guest = GuestProfile(
            guest_id=str(uuid.uuid4())[:8],
            name=name,
            phone=phone,
            first_seen=now,
            last_seen=now,
            **{k: v for k, v in kwargs.items() if k in GuestProfile.__dataclass_fields__},
        )
        await self.save_guest(guest)
        return guest

    # ── Booking Records ───────────────────────────────────────────────── #

    async def save_booking(self, booking: BookingRecord) -> None:
        """Save a booking record and link to guest."""
        # Save booking
        await self._redis.set(
            self._key("booking", booking.booking_id),
            json.dumps(booking.to_dict()),
        )
        # Add to guest's booking list (sorted by check-in date)
        score = datetime.fromisoformat(booking.check_in).timestamp() if booking.check_in else 0
        await self._redis.zadd(
            self._key("guest", booking.guest_id, "bookings"),
            {json.dumps(booking.to_dict()): score},
        )
        # Update guest stats
        guest = await self.get_guest(booking.guest_id)
        if guest:
            guest.total_bookings += 1
            guest.last_seen = datetime.now(timezone.utc).isoformat()
            await self.save_guest(guest)
        logger.info("Saved booking: %s for guest %s", booking.booking_id, booking.guest_name)

    async def get_booking(self, booking_id: str) -> BookingRecord | None:
        raw = await self._redis.get(self._key("booking", booking_id))
        if raw:
            return BookingRecord.from_dict(json.loads(raw))
        return None

    async def get_guest_bookings(self, guest_id: str, limit: int = 50) -> list[BookingRecord]:
        """Get all bookings for a guest, most recent first."""
        raw_entries = await self._redis.zrevrange(
            self._key("guest", guest_id, "bookings"), 0, limit - 1
        )
        return [BookingRecord.from_dict(json.loads(r)) for r in raw_entries]

    # ── Incident Records ──────────────────────────────────────────────── #

    async def save_incident(self, incident: IncidentRecord) -> None:
        """Save an incident and link to guest + property."""
        # Save incident
        await self._redis.set(
            self._key("incident", incident.incident_id),
            json.dumps(incident.to_dict()),
        )
        # Add to guest's incident list
        score = datetime.fromisoformat(incident.created_at).timestamp() if incident.created_at else 0
        await self._redis.zadd(
            self._key("guest", incident.guest_id, "incidents"),
            {incident.incident_id: score},
        )
        # Add to property's incident list
        await self._redis.zadd(
            self._key("property", incident.property_id, "incidents"),
            {incident.incident_id: score},
        )
        # Update guest stats
        guest = await self.get_guest(incident.guest_id)
        if guest:
            guest.total_incidents = await self._redis.zcard(
                self._key("guest", incident.guest_id, "incidents")
            )
            if incident.claim_id:
                guest.total_damage_claims += 1
            await self.save_guest(guest)

        # Update property stats
        await self._update_property_stats(incident.property_id, incident.property_name)
        logger.info(
            "Saved incident: %s type=%s guest=%s property=%s",
            incident.incident_id, incident.incident_type,
            incident.guest_name, incident.property_id,
        )

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        raw = await self._redis.get(self._key("incident", incident_id))
        if raw:
            return IncidentRecord.from_dict(json.loads(raw))
        return None

    async def get_guest_incidents(self, guest_id: str, limit: int = 50) -> list[IncidentRecord]:
        """Get all incidents for a guest, most recent first."""
        incident_ids = await self._redis.zrevrange(
            self._key("guest", guest_id, "incidents"), 0, limit - 1
        )
        incidents = []
        for iid in incident_ids:
            inc = await self.get_incident(iid)
            if inc:
                incidents.append(inc)
        return incidents

    async def get_property_incidents(self, property_id: str, limit: int = 50) -> list[IncidentRecord]:
        """Get all incidents for a property, most recent first."""
        incident_ids = await self._redis.zrevrange(
            self._key("property", property_id, "incidents"), 0, limit - 1
        )
        incidents = []
        for iid in incident_ids:
            inc = await self.get_incident(iid)
            if inc:
                incidents.append(inc)
        return incidents

    # ── Property History ──────────────────────────────────────────────── #

    async def _update_property_stats(self, property_id: str, property_name: str = "") -> None:
        """Recalculate property stats from incidents."""
        raw = await self._redis.get(self._key("property", property_id))
        if raw:
            prop = PropertyHistory.from_dict(json.loads(raw))
        else:
            prop = PropertyHistory(property_id=property_id, property_name=property_name)

        if property_name:
            prop.property_name = property_name

        prop.total_incidents = await self._redis.zcard(
            self._key("property", property_id, "incidents")
        )
        await self._redis.set(
            self._key("property", property_id),
            json.dumps(prop.to_dict()),
        )

    async def get_property_history(self, property_id: str) -> PropertyHistory | None:
        raw = await self._redis.get(self._key("property", property_id))
        if raw:
            return PropertyHistory.from_dict(json.loads(raw))
        return None

    # ── Context Builder (for LLM prompts) ─────────────────────────────── #

    async def build_guest_context(self, guest_id: str) -> str:
        """Build a text summary of guest history for LLM context injection.

        Returns a human-readable summary like:
        "Guest: John Smith | Phone: +1234567890 | 5 bookings | 2 incidents
         Last booking: Oceana 3BR, 5-12 Apr 2026
         Past incident: TV screen cracked (Dec 2025, claim £240 approved)
         Note: Always requests late checkout"
        """
        t0 = time.perf_counter()
        guest = await self.get_guest(guest_id)
        if not guest:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            emit_memory_retrieved(
                tier="guest_history",
                query=f"guest:{guest_id}",
                hits=[],
                latency_ms=latency_ms,
            )
            return ""

        lines = [
            f"Guest: {guest.name}",
        ]
        if guest.phone:
            lines[0] += f" | Phone: {guest.phone}"
        lines[0] += f" | Bookings: {guest.total_bookings} | Incidents: {guest.total_incidents}"

        if guest.tags:
            lines.append(f"Tags: {', '.join(guest.tags)}")

        # Recent bookings
        bookings = await self.get_guest_bookings(guest_id, limit=3)
        for b in bookings:
            line = f"Booking: {b.property_name}, {b.check_in} - {b.check_out}"
            if b.late_checkout_requested:
                line += " (late checkout requested)"
            lines.append(line)

        # Past incidents
        incidents = await self.get_guest_incidents(guest_id, limit=5)
        for inc in incidents:
            line = f"Incident [{inc.incident_type}]: {inc.status}"
            if inc.damage_description:
                line += f" — {inc.damage_description}"
            if inc.claim_amount:
                line += f" (claim: {inc.claim_amount})"
            if inc.claim_status:
                line += f" [{inc.claim_status}]"
            lines.append(line)

        # Notes
        for note in guest.notes[-3:]:
            lines.append(f"Note: {note}")

        context = "\n".join(lines)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        # score=1.0 is a sentinel: guest_history is a deterministic
        # lookup by id, not a ranked retrieval, so there is no similarity
        # to report. Observers render this as "exact match".
        emit_memory_retrieved(
            tier="guest_history",
            query=f"guest:{guest_id}",
            hits=[
                {
                    "id": guest_id,
                    "score": 1.0,
                    "excerpt": context,
                }
            ],
            latency_ms=latency_ms,
        )
        return context

    async def build_property_context(self, property_id: str) -> str:
        """Build text summary of property incident history for LLM context."""
        prop = await self.get_property_history(property_id)
        if not prop:
            return ""

        lines = [
            f"Property: {prop.property_name} ({prop.property_id})",
            f"Total incidents: {prop.total_incidents} | Damage claims: {prop.total_damage_claims}",
        ]

        if prop.total_damage_amount > 0:
            lines.append(f"Total damage cost: ${prop.total_damage_amount:.2f}")

        if prop.common_damage_locations:
            lines.append(f"Common damage areas: {', '.join(prop.common_damage_locations)}")

        # Recent incidents
        incidents = await self.get_property_incidents(property_id, limit=5)
        for inc in incidents:
            line = f"  {inc.created_at[:10]} — {inc.guest_name}: {inc.incident_type}"
            if inc.damage_description:
                line += f" ({inc.damage_description})"
            lines.append(line)

        return "\n".join(lines)

    async def close(self) -> None:
        await self._redis.close()
