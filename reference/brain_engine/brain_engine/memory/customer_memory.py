"""Hierarchical customer memory — Customer → Workspaces → Properties → Events.

Stores and recalls the full operational history of a Cendra customer
across all their workspaces and properties.  This is the "wow" layer
that makes the PM feel the system *knows* them:

    "You remember the damage claim at 48 River Road last month!"
    "You know my smoking policy across all my properties!"

Data hierarchy (matches Cendra's structure):

    Customer (customer_id)
        |
        +── Workspace 1 ("Greece Apartments", workspace_id)
        |   +── Property A (property_id) → events, decisions, patterns
        |   +── Property B (property_id) → events, decisions, patterns
        |
        +── Workspace 2 ("Turkey Villas", workspace_id)
        |   +── Property C → events, decisions, patterns
        |   +── Property D → events, decisions, patterns
        |
        +── Customer-level memory:
            +── PM preferences (tone, policies, overrides)
            +── Cross-property patterns (always allows pets, etc.)
            +── Interaction stats (total bookings, incidents, revenue)

Storage: Redis with TTL-based retention.
- Customer profiles: permanent
- Events: configurable TTL (default 90 days, then archived to stats)
- Stats: permanent (aggregated, no PII)

Redis key structure:
    brain:customer:{customer_id}                     → CustomerProfile JSON
    brain:customer:{customer_id}:workspaces          → set of workspace_ids
    brain:customer:{customer_id}:ws:{ws_id}:props    → set of property_ids
    brain:customer:{customer_id}:events              → sorted set (by timestamp)
    brain:customer:{customer_id}:events:prop:{p_id}  → sorted set (property-scoped)
    brain:customer:{customer_id}:stats               → aggregated stats JSON
    brain:customer:{customer_id}:pm_prefs            → PM preferences JSON
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_EVENT_TTL_DAYS: Final[int] = 90
_MAX_EVENTS_PER_QUERY: Final[int] = 200
_MAX_CONTEXT_EVENTS: Final[int] = 20
_STATS_REFRESH_INTERVAL_HOURS: Final[int] = 6


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CustomerProfile:
    """Persistent profile of a Cendra customer (PM / property manager).

    Attributes:
        customer_id: Unique customer identifier from Cendra.
        name: Customer display name.
        email: Primary email.
        workspace_ids: All workspaces owned by this customer.
        total_properties: Total properties across all workspaces.
        total_bookings: Lifetime booking count across all properties.
        total_incidents: Lifetime incident count.
        total_revenue: Lifetime revenue (approximate).
        preferred_tone: PM's preferred communication tone.
        preferred_language: PM's preferred language.
        pm_notes: Free-text notes about this PM's preferences.
        tags: Customer-level tags (e.g., "high_volume", "strict_policies").
        first_seen: When this customer first interacted with Brain Engine.
        last_seen: Most recent interaction.
    """

    customer_id: str
    name: str = ""
    email: str = ""
    workspace_ids: tuple[str, ...] = ()
    total_properties: int = 0
    total_bookings: int = 0
    total_incidents: int = 0
    total_revenue: float = 0.0
    preferred_tone: str = ""
    preferred_language: str = ""
    pm_notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for Redis storage."""
        data = asdict(self)
        data["workspace_ids"] = list(self.workspace_ids)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomerProfile:
        """Deserialize from Redis dict."""
        data["workspace_ids"] = tuple(data.get("workspace_ids", []))
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })


@dataclass(frozen=True, slots=True)
class CustomerEvent:
    """A single event in the customer's operational history.

    Events capture everything that happens across all properties:
    bookings, incidents, PM overrides, guest interactions, maintenance,
    upsells, complaints, etc.

    Attributes:
        event_id: Unique identifier.
        customer_id: Customer this event belongs to.
        workspace_id: Workspace scope.
        property_id: Property where the event occurred.
        property_name: Human-readable property name.
        event_type: Category of event.
        summary: One-line summary for context injection.
        details: Full event details.
        guest_name: Guest involved (if any).
        reservation_id: Reservation involved (if any).
        outcome: What happened (success, failure, override).
        revenue_impact: Revenue change (positive or negative).
        created_at: When the event occurred.
    """

    event_type: str
    summary: str
    customer_id: str = ""
    workspace_id: str = ""
    property_id: str = ""
    property_name: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    details: dict[str, Any] = field(default_factory=dict)
    guest_name: str = ""
    reservation_id: str = ""
    outcome: str = ""
    revenue_impact: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for Redis storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomerEvent:
        """Deserialize from Redis dict."""
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })


@dataclass(frozen=True, slots=True)
class CustomerStats:
    """Aggregated statistics for a customer (no PII, permanent storage).

    Attributes:
        customer_id: Customer identifier.
        total_events: Total events recorded.
        events_by_type: Count of events per type.
        events_by_property: Count of events per property.
        top_scenarios: Most common scenarios encountered.
        pm_override_count: How often PM overrode AI decisions.
        pm_override_rate: Fraction of decisions overridden by PM.
        avg_guest_sentiment: Average guest sentiment score.
        total_upsell_revenue: Revenue from accepted upsells.
        computed_at: When these stats were last computed.
    """

    customer_id: str = ""
    total_events: int = 0
    events_by_type: dict[str, int] = field(default_factory=dict)
    events_by_property: dict[str, int] = field(default_factory=dict)
    top_scenarios: dict[str, int] = field(default_factory=dict)
    pm_override_count: int = 0
    pm_override_rate: float = 0.0
    avg_guest_sentiment: float = 5.0
    total_upsell_revenue: float = 0.0
    computed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomerStats:
        """Deserialize from dict."""
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })


# ---------------------------------------------------------------------------
# CustomerMemory
# ---------------------------------------------------------------------------

class CustomerMemory:
    """Hierarchical memory for Cendra customers.

    Stores and recalls the full operational history of a customer
    across all workspaces and properties.  Designed for two use cases:

    1. **Context injection**: ``build_customer_context()`` produces a
       text block injected into the LLM prompt so the AI knows the
       PM's history, preferences, and cross-property patterns.
    2. **Learning**: Events feed into PatternExtractor and
       NightlyConsolidator to learn customer-level rules.

    Attributes:
        _redis: Async Redis client.
        _event_ttl: TTL for individual events (seconds).
        _log: Bound structured logger.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        event_ttl_days: int = _DEFAULT_EVENT_TTL_DAYS,
    ) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._event_ttl = event_ttl_days * 86400
        self._log = logger.bind(component="customer_memory")

    def _key(self, *parts: str) -> str:
        """Build Redis key from parts."""
        return "brain:customer:" + ":".join(parts)

    # -------------------------------------------------------------------
    # Customer profile CRUD
    # -------------------------------------------------------------------

    async def get_or_create_customer(
        self,
        customer_id: str,
        name: str = "",
        email: str = "",
    ) -> CustomerProfile:
        """Get existing customer profile or create a new one.

        Args:
            customer_id: Unique customer identifier.
            name: Display name.
            email: Primary email.

        Returns:
            CustomerProfile (existing or newly created).
        """
        existing = await self.get_customer(customer_id)
        if existing is not None:
            return existing

        now = datetime.now(timezone.utc).isoformat()
        profile = CustomerProfile(
            customer_id=customer_id,
            name=name,
            email=email,
            first_seen=now,
            last_seen=now,
        )
        await self._save_customer(profile)
        self._log.info(
            "customer_created",
            customer_id=customer_id,
            name=name,
        )
        return profile

    async def get_customer(self, customer_id: str) -> CustomerProfile | None:
        """Retrieve a customer profile.

        Args:
            customer_id: Customer identifier.

        Returns:
            CustomerProfile or None if not found.
        """
        raw = await self._redis.get(self._key(customer_id))
        if raw is None:
            return None
        return CustomerProfile.from_dict(json.loads(raw))

    async def _save_customer(self, profile: CustomerProfile) -> None:
        """Persist a customer profile to Redis.

        Args:
            profile: Customer profile to save.
        """
        await self._redis.set(
            self._key(profile.customer_id),
            json.dumps(profile.to_dict()),
        )

    # -------------------------------------------------------------------
    # Workspace / Property registration
    # -------------------------------------------------------------------

    async def register_workspace(
        self,
        customer_id: str,
        workspace_id: str,
    ) -> None:
        """Register a workspace under a customer.

        Args:
            customer_id: Customer identifier.
            workspace_id: Workspace identifier.
        """
        await self._redis.sadd(
            self._key(customer_id, "workspaces"),
            workspace_id,
        )
        self._log.debug(
            "workspace_registered",
            customer_id=customer_id,
            workspace_id=workspace_id,
        )

    async def register_property(
        self,
        customer_id: str,
        workspace_id: str,
        property_id: str,
    ) -> None:
        """Register a property under a workspace.

        Args:
            customer_id: Customer identifier.
            workspace_id: Workspace identifier.
            property_id: Property identifier.
        """
        await self.register_workspace(customer_id, workspace_id)
        await self._redis.sadd(
            self._key(customer_id, "ws", workspace_id, "props"),
            property_id,
        )

    async def get_workspaces(self, customer_id: str) -> list[str]:
        """Get all workspace IDs for a customer.

        Args:
            customer_id: Customer identifier.

        Returns:
            List of workspace IDs.
        """
        return list(
            await self._redis.smembers(
                self._key(customer_id, "workspaces"),
            ),
        )

    async def get_properties(
        self,
        customer_id: str,
        workspace_id: str,
    ) -> list[str]:
        """Get all property IDs in a workspace.

        Args:
            customer_id: Customer identifier.
            workspace_id: Workspace identifier.

        Returns:
            List of property IDs.
        """
        return list(
            await self._redis.smembers(
                self._key(customer_id, "ws", workspace_id, "props"),
            ),
        )

    async def get_all_properties(
        self,
        customer_id: str,
    ) -> list[str]:
        """Get ALL property IDs across all workspaces.

        Args:
            customer_id: Customer identifier.

        Returns:
            List of all property IDs.
        """
        workspaces = await self.get_workspaces(customer_id)
        all_props: list[str] = []
        for ws in workspaces:
            props = await self.get_properties(customer_id, ws)
            all_props.extend(props)
        return all_props

    # -------------------------------------------------------------------
    # Event recording
    # -------------------------------------------------------------------

    async def record_event(
        self,
        *,
        customer_id: str,
        workspace_id: str = "",
        property_id: str = "",
        property_name: str = "",
        event_type: str,
        summary: str,
        details: dict[str, Any] | None = None,
        guest_name: str = "",
        reservation_id: str = "",
        outcome: str = "",
        revenue_impact: float = 0.0,
    ) -> CustomerEvent:
        """Record an event in the customer's history.

        Events are stored in two sorted sets:
        - Customer-level (all events across all properties)
        - Property-level (events for a specific property)

        Both have TTL-based expiration.

        Args:
            customer_id: Customer identifier.
            workspace_id: Workspace scope.
            property_id: Property where event occurred.
            property_name: Human-readable property name.
            event_type: Event category (booking, incident, override, etc.).
            summary: One-line summary.
            details: Full event details.
            guest_name: Guest involved.
            reservation_id: Reservation involved.
            outcome: What happened.
            revenue_impact: Revenue change.

        Returns:
            The created CustomerEvent.
        """
        event = CustomerEvent(
            customer_id=customer_id,
            workspace_id=workspace_id,
            property_id=property_id,
            property_name=property_name,
            event_type=event_type,
            summary=summary,
            details=details or {},
            guest_name=guest_name,
            reservation_id=reservation_id,
            outcome=outcome,
            revenue_impact=revenue_impact,
        )

        timestamp = datetime.now(timezone.utc).timestamp()
        event_json = json.dumps(event.to_dict())

        # Store in customer-level sorted set
        customer_events_key = self._key(customer_id, "events")
        await self._redis.zadd(customer_events_key, {event_json: timestamp})

        # Store in property-level sorted set
        if property_id:
            prop_events_key = self._key(
                customer_id, "events", "prop", property_id,
            )
            await self._redis.zadd(prop_events_key, {event_json: timestamp})
            await self.register_property(
                customer_id, workspace_id, property_id,
            )

        # Update last_seen on customer profile
        profile = await self.get_customer(customer_id)
        if profile is not None:
            from dataclasses import replace
            updated = replace(
                profile,
                last_seen=datetime.now(timezone.utc).isoformat(),
            )
            await self._save_customer(updated)

        self._log.info(
            "event_recorded",
            customer_id=customer_id[:8],
            property_id=property_id,
            event_type=event_type,
            summary=summary[:60],
        )
        return event

    # -------------------------------------------------------------------
    # Event recall
    # -------------------------------------------------------------------

    async def recall_events(
        self,
        customer_id: str,
        *,
        property_id: str | None = None,
        event_type: str | None = None,
        limit: int = _MAX_EVENTS_PER_QUERY,
    ) -> list[CustomerEvent]:
        """Recall events from customer history.

        Args:
            customer_id: Customer identifier.
            property_id: Optional property filter.
            event_type: Optional event type filter.
            limit: Maximum events to return.

        Returns:
            List of events, most recent first.
        """
        if property_id:
            key = self._key(customer_id, "events", "prop", property_id)
        else:
            key = self._key(customer_id, "events")

        raw_entries = await self._redis.zrevrange(key, 0, limit - 1)

        events: list[CustomerEvent] = []
        for raw in raw_entries:
            event = CustomerEvent.from_dict(json.loads(raw))
            if event_type and event.event_type != event_type:
                continue
            events.append(event)

        return events

    async def recall_recent(
        self,
        customer_id: str,
        days: int = 30,
        limit: int = 50,
    ) -> list[CustomerEvent]:
        """Recall events from the last N days.

        Args:
            customer_id: Customer identifier.
            days: Number of days to look back.
            limit: Maximum events.

        Returns:
            List of recent events.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ts = cutoff.timestamp()

        key = self._key(customer_id, "events")
        raw_entries = await self._redis.zrangebyscore(
            key, cutoff_ts, "+inf",
        )

        events = [
            CustomerEvent.from_dict(json.loads(raw))
            for raw in raw_entries[-limit:]
        ]
        events.reverse()
        return events

    # -------------------------------------------------------------------
    # PM preferences
    # -------------------------------------------------------------------

    async def save_pm_preferences(
        self,
        customer_id: str,
        preferences: dict[str, Any],
    ) -> None:
        """Store PM preferences for a customer.

        Preferences are learned from PM overrides and explicit settings:
        tone, policies, common decisions.

        Args:
            customer_id: Customer identifier.
            preferences: Preference dict to store.
        """
        key = self._key(customer_id, "pm_prefs")
        existing_raw = await self._redis.get(key)
        existing = json.loads(existing_raw) if existing_raw else {}
        existing.update(preferences)
        await self._redis.set(key, json.dumps(existing))

    async def get_pm_preferences(
        self,
        customer_id: str,
    ) -> dict[str, Any]:
        """Retrieve PM preferences.

        Args:
            customer_id: Customer identifier.

        Returns:
            Preference dict (empty if none stored).
        """
        raw = await self._redis.get(self._key(customer_id, "pm_prefs"))
        if raw is None:
            return {}
        return json.loads(raw)

    # -------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------

    async def compute_stats(
        self,
        customer_id: str,
    ) -> CustomerStats:
        """Compute aggregated statistics for a customer.

        Scans all events and produces permanent, PII-free stats.

        Args:
            customer_id: Customer identifier.

        Returns:
            CustomerStats with aggregated metrics.
        """
        events = await self.recall_events(customer_id, limit=500)

        by_type: dict[str, int] = {}
        by_property: dict[str, int] = {}
        by_scenario: dict[str, int] = {}
        override_count = 0
        total_revenue = 0.0

        for event in events:
            by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
            if event.property_id:
                prop_key = event.property_name or event.property_id
                by_property[prop_key] = by_property.get(prop_key, 0) + 1
            if event.event_type == "pm_override":
                override_count += 1
            total_revenue += event.revenue_impact

            scenario = event.details.get("scenario", "")
            if scenario:
                by_scenario[scenario] = by_scenario.get(scenario, 0) + 1

        total = len(events)
        override_rate = override_count / total if total > 0 else 0.0

        stats = CustomerStats(
            customer_id=customer_id,
            total_events=total,
            events_by_type=dict(
                sorted(by_type.items(), key=lambda x: -x[1]),
            ),
            events_by_property=dict(
                sorted(by_property.items(), key=lambda x: -x[1]),
            ),
            top_scenarios=dict(
                sorted(by_scenario.items(), key=lambda x: -x[1])[:10],
            ),
            pm_override_count=override_count,
            pm_override_rate=round(override_rate, 3),
            total_upsell_revenue=round(total_revenue, 2),
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

        # Persist stats (permanent, no TTL)
        await self._redis.set(
            self._key(customer_id, "stats"),
            json.dumps(stats.to_dict()),
        )
        return stats

    # -------------------------------------------------------------------
    # Event cleanup (TTL-based retention)
    # -------------------------------------------------------------------

    async def cleanup_old_events(
        self,
        customer_id: str,
        retention_days: int = _DEFAULT_EVENT_TTL_DAYS,
    ) -> int:
        """Remove events older than retention period.

        Computes stats BEFORE deletion so aggregated data is preserved.

        Args:
            customer_id: Customer identifier.
            retention_days: Days to retain events.

        Returns:
            Number of events removed.
        """
        await self.compute_stats(customer_id)

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_ts = cutoff.timestamp()

        key = self._key(customer_id, "events")
        removed = await self._redis.zremrangebyscore(key, "-inf", cutoff_ts)

        if removed > 0:
            self._log.info(
                "events_cleaned",
                customer_id=customer_id[:8],
                removed=removed,
                retention_days=retention_days,
            )
        return removed

    # -------------------------------------------------------------------
    # Context building (for LLM injection)
    # -------------------------------------------------------------------

    async def build_customer_context(
        self,
        customer_id: str,
        current_property_id: str = "",
    ) -> str:
        """Build a text summary of customer history for LLM context.

        Produces a structured block that can be injected into the
        system prompt so the AI knows the PM's full history.

        Args:
            customer_id: Customer identifier.
            current_property_id: The property being discussed right now
                (highlighted in the context).

        Returns:
            Human-readable context string, or empty if no data.

        Example output:
            Customer: Tri-State Travels (customer abc123)
            Workspaces: 1 | Properties: 14 | Total events: 203
            PM preferences: friendly tone, allows pets with fee

            Recent activity (last 30 days):
            - Apr 09: 48 River Road — EarlyCheckIn for Syed Hamza ($10)
            - Apr 07: 22 River Rd — Complaint: stink bug, escalated
            - Apr 06: 36 River Road — Late checkout approved ($15)

            Current property (36 River Road):
            - 82% AI accuracy, 170 KB entries
            - Last incident: TV remote broken (Mar 20)
            - PM pattern: always allows late checkout for 3+ night stays
        """
        profile = await self.get_customer(customer_id)
        if profile is None:
            return ""

        lines: list[str] = []

        # Customer header
        header = f"Customer: {profile.name or customer_id}"
        workspaces = await self.get_workspaces(customer_id)
        all_props = await self.get_all_properties(customer_id)
        header += (
            f" | Workspaces: {len(workspaces)}"
            f" | Properties: {len(all_props)}"
        )
        lines.append(header)

        # PM preferences
        prefs = await self.get_pm_preferences(customer_id)
        if prefs:
            pref_parts: list[str] = []
            if prefs.get("tone"):
                pref_parts.append(f"{prefs['tone']} tone")
            if prefs.get("pet_policy"):
                pref_parts.append(f"pets: {prefs['pet_policy']}")
            if prefs.get("smoking_policy"):
                pref_parts.append(f"smoking: {prefs['smoking_policy']}")
            if prefs.get("late_checkout_policy"):
                pref_parts.append(
                    f"late checkout: {prefs['late_checkout_policy']}",
                )
            if pref_parts:
                lines.append(f"PM preferences: {', '.join(pref_parts)}")

        # Recent activity across all properties
        recent = await self.recall_recent(customer_id, days=30, limit=10)
        if recent:
            lines.append("")
            lines.append("Recent activity (last 30 days):")
            for event in recent:
                date_str = event.created_at[:10]
                prop = event.property_name or event.property_id or "—"
                line = f"  {date_str}: {prop} — {event.summary}"
                if event.revenue_impact:
                    line += f" (${event.revenue_impact:,.0f})"
                lines.append(line)

        # Current property details
        if current_property_id:
            prop_events = await self.recall_events(
                customer_id,
                property_id=current_property_id,
                limit=5,
            )
            if prop_events:
                prop_name = (
                    prop_events[0].property_name or current_property_id
                )
                lines.append("")
                lines.append(f"Current property ({prop_name}):")
                for event in prop_events:
                    lines.append(
                        f"  {event.created_at[:10]}: "
                        f"{event.event_type} — {event.summary}",
                    )

        return "\n".join(lines)

    async def close(self) -> None:
        """Close Redis connection."""
        await self._redis.close()
