"""Procedural Memory — Learned behavioral patterns and operational rules.

Implements concepts from:
- Hindsight: Behavioral parameters storage
- CoALA: Procedural knowledge in cognitive architectures
- MemSearcher: Learning from experience what works

Stores patterns like:
- "When guest has damage history, always request photos before checkout"
- "Late checkout for VIP guests (5+ bookings) is usually approved"
- "Property X: always check bathroom tiles (3 claims in 6 months)"

Procedures are:
1. Discovered automatically from repeated successful actions
2. Manually defined as operational rules
3. Refined over time based on outcomes
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


@dataclass
class Procedure:
    """A learned behavioral pattern or operational rule.

    Attributes:
        procedure_id: Unique identifier.
        name: Short descriptive name.
        description: What this procedure does.
        trigger_conditions: When to activate this procedure.
        actions: What actions to take.
        success_count: Times this procedure led to good outcomes.
        failure_count: Times this procedure led to bad outcomes.
        confidence: Current confidence (success_rate weighted by recency).
        source: How this procedure was discovered.
        tags: Categorization tags.
        created_at: When first created.
        last_used: When last activated.
        active: Whether this procedure is currently enabled.
        property_id: Property this rule belongs to.
        category: Rule category (guest_communication, escalation, etc.).
        rule: Human-readable rule text.
        evidence_count: Times this rule has been applied.
        immutable: If True, cannot be updated or deleted via API.
        priority: Rule priority (low, medium, high, critical).
        created_by: Who created this rule (email or system).
        updated_at: Last update timestamp.
    """
    procedure_id: str = ""
    name: str = ""
    description: str = ""
    trigger_conditions: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.5
    source: str = "manual"  # "manual", "learned", "immutable", "sop", "discovered", "refined"
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    last_used: str | None = None
    active: bool = True
    property_id: str = ""
    category: str = ""
    rule: str = ""
    evidence_count: int = 0
    immutable: bool = False
    priority: str = "medium"
    created_by: str = ""
    updated_at: str = ""

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.5
        return self.success_count / total

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["success_rate"] = self.success_rate
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Procedure:
        data.pop("success_rate", None)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ProceduralMemory:
    """Redis-backed store for learned behavioral patterns.

    Key structure:
        brain:proc:{procedure_id}         → Procedure JSON
        brain:proc:trigger:{event}        → Set of procedure_ids
        brain:proc:tag:{tag}              → Set of procedure_ids
        brain:proc:all                    → Set of all procedure_ids
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        workspace_id: str = "",
    ) -> None:
        import redis.asyncio as aioredis
        from brain_engine.memory.tenant import build_prefix
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = build_prefix("brain:proc:", workspace_id)

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    async def add_procedure(
        self,
        name: str,
        description: str,
        trigger_conditions: dict[str, Any],
        actions: list[str],
        source: str = "manual",
        tags: list[str] | None = None,
        confidence: float = 0.5,
    ) -> Procedure:
        """Add a new procedure to memory."""
        now = datetime.now(timezone.utc).isoformat()
        proc = Procedure(
            procedure_id=str(uuid.uuid4())[:8],
            name=name,
            description=description,
            trigger_conditions=trigger_conditions,
            actions=actions,
            source=source,
            tags=tags or [],
            confidence=confidence,
            created_at=now,
        )

        pipe = self._redis.pipeline()
        pipe.set(self._key(proc.procedure_id), json.dumps(proc.to_dict()))
        pipe.sadd(self._key("all"), proc.procedure_id)

        # Index by trigger events
        for event in trigger_conditions.get("events", []):
            pipe.sadd(self._key("trigger", event), proc.procedure_id)

        # Index by tags
        for tag in proc.tags:
            pipe.sadd(self._key("tag", tag), proc.procedure_id)

        await pipe.execute()
        logger.info("Added procedure: %s (%s)", name, proc.procedure_id)
        return proc

    async def get_procedure(self, procedure_id: str) -> Procedure | None:
        raw = await self._redis.get(self._key(procedure_id))
        if raw:
            return Procedure.from_dict(json.loads(raw))
        return None

    async def find_applicable_procedures(
        self, event: str, context: dict[str, Any] | None = None
    ) -> list[Procedure]:
        """Find all procedures that should activate for this event.

        Args:
            event: The triggering event type.
            context: Current context for condition matching.

        Returns:
            List of applicable procedures sorted by confidence.
        """
        t0 = time.perf_counter()
        proc_ids = await self._redis.smembers(self._key("trigger", event))
        procedures = []

        for pid in proc_ids:
            proc = await self.get_procedure(pid)
            if proc and proc.active:
                if self._matches_conditions(proc, event, context or {}):
                    procedures.append(proc)

        ranked = sorted(procedures, key=lambda p: p.confidence, reverse=True)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        emit_memory_retrieved(
            tier="procedural",
            query=f"trigger:{event}",
            hits=[
                {
                    "id": getattr(p, "id", ""),
                    "score": float(getattr(p, "confidence", 0.0)),
                    "excerpt": getattr(p, "description", "") or getattr(p, "name", ""),
                }
                for p in ranked
            ],
            latency_ms=latency_ms,
        )
        return ranked

    def _matches_conditions(
        self, proc: Procedure, event: str, context: dict[str, Any]
    ) -> bool:
        """Check if a procedure's trigger conditions match the current context."""
        conditions = proc.trigger_conditions

        # Check required context fields
        required_fields = conditions.get("required_context", {})
        for field_key, expected in required_fields.items():
            actual = context.get(field_key)
            if expected == "*":  # Any non-None value
                if actual is None:
                    return False
            elif actual != expected:
                return False

        # Check thresholds
        thresholds = conditions.get("thresholds", {})
        for field_key, threshold in thresholds.items():
            actual = context.get(field_key, 0)
            op = threshold.get("op", ">=")
            value = threshold.get("value", 0)
            if op == ">=" and actual < value:
                return False
            if op == "<=" and actual > value:
                return False
            if op == ">" and actual <= value:
                return False
            if op == "<" and actual >= value:
                return False

        return True

    async def record_outcome(
        self, procedure_id: str, success: bool, notes: str = ""
    ) -> None:
        """Record the outcome of applying a procedure.

        Updates success/failure counts and recalculates confidence.
        """
        proc = await self.get_procedure(procedure_id)
        if not proc:
            return

        if success:
            proc.success_count += 1
        else:
            proc.failure_count += 1

        # Recalculate confidence with recency weighting
        proc.confidence = proc.success_rate
        proc.last_used = datetime.now(timezone.utc).isoformat()

        await self._redis.set(self._key(proc.procedure_id), json.dumps(proc.to_dict()))
        logger.info(
            "Procedure %s outcome: %s (confidence: %.2f)",
            proc.name, "success" if success else "failure", proc.confidence,
        )

    async def get_all_procedures(self, active_only: bool = True) -> list[Procedure]:
        """Get all stored procedures."""
        proc_ids = await self._redis.smembers(self._key("all"))
        procedures = []
        for pid in proc_ids:
            proc = await self.get_procedure(pid)
            if proc:
                if not active_only or proc.active:
                    procedures.append(proc)
        return sorted(procedures, key=lambda p: p.confidence, reverse=True)

    async def seed_default_procedures(self) -> None:
        """Seed the system with default Airbnb property management procedures."""
        defaults = [
            {
                "name": "photo_check_for_damage_prone_guest",
                "description": "Request before/after photos when guest has prior damage history",
                "trigger_conditions": {
                    "events": ["guest_identified", "incident_started"],
                    "thresholds": {
                        "guest_damage_count": {"op": ">=", "value": 1},
                    },
                },
                "actions": [
                    "flag_guest_as_high_risk",
                    "request_detailed_before_photos",
                    "schedule_immediate_post_checkout_inspection",
                ],
                "tags": ["damage", "prevention", "photos"],
                "confidence": 0.8,
            },
            {
                "name": "auto_approve_late_checkout_vip",
                "description": "Auto-approve late checkout for guests with 5+ bookings and no incidents",
                "trigger_conditions": {
                    "events": ["late_checkout_requested"],
                    "thresholds": {
                        "guest_booking_count": {"op": ">=", "value": 5},
                        "guest_incident_count": {"op": "<=", "value": 0},
                    },
                },
                "actions": [
                    "approve_late_checkout",
                    "notify_cleaner_of_delay",
                ],
                "tags": ["late_checkout", "vip", "automation"],
                "confidence": 0.9,
            },
            {
                "name": "escalate_high_value_damage",
                "description": "Escalate to host when damage claim exceeds $500",
                "trigger_conditions": {
                    "events": ["damage_detected", "claim_submitted"],
                    "thresholds": {
                        "claim_amount": {"op": ">=", "value": 500},
                    },
                },
                "actions": [
                    "notify_host_immediately",
                    "request_professional_assessment",
                    "document_with_detailed_photos",
                ],
                "tags": ["damage", "escalation", "high_value"],
                "confidence": 0.85,
            },
            {
                "name": "check_bathroom_tiles_property_x",
                "description": "Always inspect bathroom tiles for properties with tile damage history",
                "trigger_conditions": {
                    "events": ["cleaning_completed", "photos_received"],
                    "required_context": {
                        "property_has_tile_damage_history": True,
                    },
                },
                "actions": [
                    "request_close_up_bathroom_photos",
                    "compare_with_previous_tile_photos",
                ],
                "tags": ["inspection", "bathroom", "tiles"],
                "confidence": 0.7,
            },
            {
                "name": "claim_deadline_reminder",
                "description": "Remind about 14-day AirCover claim deadline when damage is detected",
                "trigger_conditions": {
                    "events": ["damage_detected"],
                },
                "actions": [
                    "calculate_claim_deadline",
                    "set_reminder_at_day_10",
                    "notify_host_of_deadline",
                ],
                "tags": ["claims", "deadline", "aircover"],
                "confidence": 0.95,
            },
        ]

        for proc_data in defaults:
            existing = await self._redis.smembers(self._key("all"))
            # Check if a procedure with this name already exists
            skip = False
            for pid in existing:
                p = await self.get_procedure(pid)
                if p and p.name == proc_data["name"]:
                    skip = True
                    break
            if not skip:
                await self.add_procedure(**proc_data, source="default")

    async def find_best_match(
        self, trigger: str, context: dict[str, Any] | None = None,
    ) -> Procedure | None:
        """Find the single best matching procedure for an event.

        Args:
            trigger: The triggering event type.
            context: Current context for condition matching.

        Returns:
            Best matching Procedure or None.
        """
        procedures = await self.find_applicable_procedures(trigger, context)
        return procedures[0] if procedures else None

    async def count(self) -> int:
        """Count all active procedures.

        Returns:
            Number of active procedures.
        """
        proc_ids = await self._redis.smembers(self._key("all"))
        return len(proc_ids)

    async def cleanup(
        self,
        remove_below_confidence: float = 0.15,
        remove_unused_days: int = 60,
        remove_zero_success: bool = True,
    ) -> int:
        """Remove low-quality procedures.

        Args:
            remove_below_confidence: Deactivate below this confidence.
            remove_unused_days: Deactivate if unused for this many days.
            remove_zero_success: Remove procedures with zero successes.

        Returns:
            Number of procedures deactivated.
        """
        all_procs = await self.get_all_procedures(active_only=False)
        deactivated = 0
        now = datetime.now(timezone.utc)

        for proc in all_procs:
            should_remove = self._should_remove(
                proc, remove_below_confidence,
                remove_unused_days, remove_zero_success, now,
            )
            if should_remove:
                proc.active = False
                await self._redis.set(
                    self._key(proc.procedure_id),
                    json.dumps(proc.to_dict()),
                )
                deactivated += 1

        logger.info("Cleanup: deactivated %d procedures", deactivated)
        return deactivated

    @staticmethod
    def _should_remove(
        proc: Procedure,
        min_confidence: float,
        max_unused_days: int,
        zero_success: bool,
        now: datetime,
    ) -> bool:
        """Check if a procedure should be removed.

        Args:
            proc: The procedure to check.
            min_confidence: Minimum confidence threshold.
            max_unused_days: Maximum days without use.
            zero_success: Whether to remove zero-success procs.
            now: Current UTC datetime.

        Returns:
            True if the procedure should be deactivated.
        """
        if not proc.active:
            return False
        if proc.source in ("default", "manual", "immutable", "sop"):
            return False
        if proc.immutable:
            return False
        if proc.confidence < min_confidence:
            return True
        if zero_success and proc.success_count == 0 and proc.confidence < 0.3:
            return True
        return False

    async def aggregate_preference(
        self, approval: Any,
    ) -> None:
        """Aggregate an owner approval into a stable rule.

        Args:
            approval: Approval interaction record.
        """
        event_type = getattr(approval, "event_type", "unknown")
        existing = await self.find_best_match(event_type)

        if existing:
            is_approved = getattr(approval, "owner_approved", False)
            if is_approved:
                existing.success_count += 1
                existing.confidence = min(1.0, existing.confidence + 0.03)
            else:
                existing.failure_count += 1
                existing.confidence = max(0.1, existing.confidence - 0.05)

            await self._redis.set(
                self._key(existing.procedure_id),
                json.dumps(existing.to_dict()),
            )

    # ── Rules CRUD API ─────────────────────────────────────────────── #

    async def store_manual_rule(
        self,
        property_id: str,
        category: str,
        rule_text: str,
        confidence: float = 1.0,
        source: str = "manual",
        immutable: bool = False,
        priority: str = "medium",
        tags: list[str] | None = None,
        created_by: str = "",
    ) -> Procedure:
        """Create a manual or immutable rule for a property.

        Args:
            property_id: Property this rule belongs to.
            category: Rule category.
            rule_text: Human-readable rule text.
            confidence: Initial confidence.
            source: Rule source (manual, immutable, learned).
            immutable: Whether rule is immutable.
            priority: Priority level.
            tags: Categorization tags.
            created_by: Who created this rule.

        Returns:
            Created Procedure.
        """
        now = datetime.now(timezone.utc).isoformat()
        rule_id = f"rule_{uuid.uuid4().hex[:12]}"

        proc = Procedure(
            procedure_id=rule_id,
            name=f"{category}_{rule_id[:8]}",
            description=rule_text,
            trigger_conditions={"events": [category]},
            actions=[],
            confidence=confidence,
            source=source,
            tags=tags or [],
            created_at=now,
            active=True,
            property_id=property_id,
            category=category,
            rule=rule_text,
            evidence_count=0,
            immutable=immutable,
            priority=priority,
            created_by=created_by,
            updated_at=now,
        )

        pipe = self._redis.pipeline()
        pipe.set(self._key(proc.procedure_id), json.dumps(proc.to_dict()))
        pipe.sadd(self._key("all"), proc.procedure_id)
        pipe.sadd(self._key("property", property_id), proc.procedure_id)
        pipe.sadd(self._key("category", category), proc.procedure_id)
        for tag in proc.tags:
            pipe.sadd(self._key("tag", tag), proc.procedure_id)
        for event in proc.trigger_conditions.get("events", []):
            pipe.sadd(self._key("trigger", event), proc.procedure_id)
        await pipe.execute()

        logger.info(
            "Stored rule: %s property=%s category=%s source=%s",
            rule_id, property_id, category, source,
        )
        return proc

    async def get_rules(
        self,
        property_id: str,
        category: str | None = None,
        source: str | None = None,
    ) -> list[Procedure]:
        """Get all rules for a property, with optional filters.

        Args:
            property_id: Property to query.
            category: Filter by category.
            source: Filter by source.

        Returns:
            List of matching Procedures sorted by priority then confidence.
        """
        proc_ids = await self._redis.smembers(
            self._key("property", property_id),
        )
        rules: list[Procedure] = []

        for pid in proc_ids:
            proc = await self.get_procedure(pid)
            if not proc or not proc.active:
                continue
            if category and proc.category != category:
                continue
            if source and proc.source != source:
                continue
            rules.append(proc)

        return self.rank_rules(rules)

    async def get_rule(self, rule_id: str) -> Procedure | None:
        """Get a single rule by ID.

        Args:
            rule_id: Rule identifier.

        Returns:
            Procedure or None.
        """
        return await self.get_procedure(rule_id)

    async def update_rule(
        self,
        rule_id: str,
        updates: dict[str, Any],
    ) -> Procedure | None:
        """Update a rule. Immutable rules cannot be updated.

        Args:
            rule_id: Rule to update.
            updates: Fields to update.

        Returns:
            Updated Procedure or None if not found.

        Raises:
            ValueError: If rule is immutable.
        """
        proc = await self.get_procedure(rule_id)
        if not proc:
            return None

        if proc.immutable or proc.source == "immutable":
            raise ValueError(
                f"Cannot update immutable rule {rule_id}"
            )

        allowed_fields = {
            "rule", "category", "confidence", "priority",
            "tags", "description",
        }
        now = datetime.now(timezone.utc).isoformat()

        old_category = proc.category
        for field_name, value in updates.items():
            if field_name in allowed_fields and hasattr(proc, field_name):
                setattr(proc, field_name, value)

        if "rule" in updates:
            proc.description = updates["rule"]
        proc.updated_at = now

        # Re-index if category changed
        if "category" in updates and old_category != proc.category:
            pipe = self._redis.pipeline()
            pipe.srem(self._key("category", old_category), rule_id)
            pipe.sadd(self._key("category", proc.category), rule_id)
            # Update trigger events to match new category
            pipe.srem(self._key("trigger", old_category), rule_id)
            pipe.sadd(self._key("trigger", proc.category), rule_id)
            proc.trigger_conditions["events"] = [proc.category]
            await pipe.execute()

        await self._redis.set(
            self._key(proc.procedure_id),
            json.dumps(proc.to_dict()),
        )
        logger.info("Updated rule: %s", rule_id)
        return proc

    async def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule. Immutable rules cannot be deleted.

        Args:
            rule_id: Rule to delete.

        Returns:
            True if deleted.

        Raises:
            ValueError: If rule is immutable.
        """
        proc = await self.get_procedure(rule_id)
        if not proc:
            return False

        if proc.immutable or proc.source == "immutable":
            raise ValueError(
                f"Cannot delete immutable rule {rule_id}"
            )

        pipe = self._redis.pipeline()
        pipe.delete(self._key(rule_id))
        pipe.srem(self._key("all"), rule_id)
        if proc.property_id:
            pipe.srem(self._key("property", proc.property_id), rule_id)
        if proc.category:
            pipe.srem(self._key("category", proc.category), rule_id)
        for tag in proc.tags:
            pipe.srem(self._key("tag", tag), rule_id)
        for event in proc.trigger_conditions.get("events", []):
            pipe.srem(self._key("trigger", event), rule_id)
        await pipe.execute()

        logger.info("Deleted rule: %s", rule_id)
        return True

    @staticmethod
    def rank_rules(rules: list[Procedure]) -> list[Procedure]:
        """Rank rules by source priority then confidence.

        Priority: immutable(3) > manual(2) > learned(1) > others(0).

        Args:
            rules: Rules to rank.

        Returns:
            Sorted list.
        """
        source_priority = {
            "immutable": 4,
            "manual": 3,
            "sop": 2,
            "learned": 1,
            "default": 1,
        }
        priority_weight = {
            "critical": 4,
            "high": 3,
            "medium": 2,
            "low": 1,
        }
        return sorted(
            rules,
            key=lambda r: (
                source_priority.get(r.source, 0),
                priority_weight.get(r.priority, 0),
                r.confidence,
            ),
            reverse=True,
        )

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.close()
