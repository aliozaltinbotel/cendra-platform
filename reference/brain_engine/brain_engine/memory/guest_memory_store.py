"""Guest Memory Store — persistent per-guest learning with PostgreSQL + pgvector.

Brain Engine remembers every guest across all interactions.
Not PII (name, email, phone) — that's Cendra's data.
We store LEARNED OBSERVATIONS about each guest:

    - Language & communication preferences
    - Satisfaction trajectory across stays
    - Incident history (complaints, issues)
    - Behavioral patterns (booking habits, common requests)
    - Risk signals (late payments, damage history)
    - Learned preferences (late checkout, parking, etc.)
    - Semantic embeddings for preference-based search (pgvector)

Storage:
    PostgreSQL — structured guest data (preferences, incidents, scores)
    pgvector   — guest preference embeddings for similarity search
    Redis      — still used for working/episodic/procedural memory
    Qdrant     — still used for semantic memory (property knowledge)

This makes Brain Engine genuinely smarter with each interaction.
Second-time guest gets personalized response from first interaction.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class GuestDBLike(Protocol):
    """Database interface for guest memory storage.

    Can be implemented with asyncpg, psycopg, or in-memory dict for tests.
    """

    async def execute(self, query: str, *args: Any) -> None: ...
    async def fetchone(self, query: str, *args: Any) -> dict[str, Any] | None: ...
    async def fetchall(self, query: str, *args: Any) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class GuestMemory:
    """Everything Brain Engine has learned about a guest.

    Attributes:
        guest_id: Guest identifier (from Cendra PMS).
        total_stays: Number of completed stays.
        total_interactions: Number of message interactions.
        language: Detected preferred language code.
        communication_style: Observed style (formal/casual/emoji-heavy).
        satisfaction_scores: Score per stay (1-5).
        avg_satisfaction: Rolling average satisfaction.
        preferences: Learned preferences (dict of preference -> value).
        common_requests: Frequently asked questions/requests.
        incidents: Past incidents with dates and types.
        risk_flags: Active risk signals.
        patterns: Observed behavioral patterns.
        property_history: Properties stayed at (property_id list).
        first_seen: ISO timestamp of first interaction.
        last_seen: ISO timestamp of last interaction.
        notes: Brain Engine's internal notes about this guest.
    """

    guest_id: str = ""
    total_stays: int = 0
    total_interactions: int = 0
    language: str = "en"
    communication_style: str = "neutral"
    satisfaction_scores: list[int] = field(default_factory=list)
    avg_satisfaction: float = 0.0
    preferences: dict[str, str] = field(default_factory=dict)
    common_requests: list[str] = field(default_factory=list)
    incidents: list[dict[str, str]] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    property_history: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_returning(self) -> bool:
        """Whether guest has stayed before."""
        return self.total_stays > 0

    @property
    def is_high_risk(self) -> bool:
        """Whether guest has active risk flags."""
        return len(self.risk_flags) > 0

    @property
    def loyalty_tier(self) -> str:
        """Calculate loyalty tier from satisfaction and stays."""
        if self.total_stays >= 5 and self.avg_satisfaction >= 4.0:
            return "platinum"
        if self.total_stays >= 3 and self.avg_satisfaction >= 3.5:
            return "gold"
        if self.total_stays >= 1:
            return "silver"
        return "bronze"

    def to_context_string(self) -> str:
        """Format as context string for LLM system prompt.

        Returns:
            Human-readable guest context for LLM.
        """
        parts = [f"Returning guest: {'Yes' if self.is_returning else 'No'}"]

        if self.is_returning:
            parts.append(f"Previous stays: {self.total_stays}")
            parts.append(f"Loyalty tier: {self.loyalty_tier}")
            parts.append(f"Avg satisfaction: {self.avg_satisfaction:.1f}/5")

        if self.preferences:
            prefs = ", ".join(f"{k}: {v}" for k, v in self.preferences.items())
            parts.append(f"Known preferences: {prefs}")

        if self.language != "en":
            parts.append(f"Preferred language: {self.language}")

        if self.incidents:
            recent = self.incidents[-3:]
            incident_str = "; ".join(
                f"{i.get('type', 'issue')} ({i.get('date', '')})"
                for i in recent
            )
            parts.append(f"Recent incidents: {incident_str}")

        if self.risk_flags:
            parts.append(f"Risk flags: {', '.join(self.risk_flags)}")

        return "\n".join(parts)


# ── SQL Schema ───────────────────────────────────────────────────── #


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS guest_memories (
    guest_id        TEXT PRIMARY KEY,
    total_stays     INTEGER DEFAULT 0,
    total_interactions INTEGER DEFAULT 0,
    language        TEXT DEFAULT 'en',
    communication_style TEXT DEFAULT 'neutral',
    satisfaction_scores JSONB DEFAULT '[]',
    avg_satisfaction REAL DEFAULT 0.0,
    preferences     JSONB DEFAULT '{}',
    common_requests JSONB DEFAULT '[]',
    incidents       JSONB DEFAULT '[]',
    risk_flags      JSONB DEFAULT '[]',
    patterns        JSONB DEFAULT '[]',
    property_history JSONB DEFAULT '[]',
    first_seen      TIMESTAMPTZ,
    last_seen       TIMESTAMPTZ,
    notes           JSONB DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_guest_memories_last_seen
    ON guest_memories(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_guest_memories_risk
    ON guest_memories USING GIN(risk_flags);
CREATE INDEX IF NOT EXISTS idx_guest_memories_properties
    ON guest_memories USING GIN(property_history);
"""


class GuestMemoryStore:
    """PostgreSQL-backed persistent guest memory.

    Stores and retrieves per-guest learned observations.
    Uses JSONB for flexible nested data (preferences, incidents).
    Compatible with pgvector for future embedding search.

    Args:
        db: Async database client implementing GuestDBLike.
    """

    def __init__(self, db: GuestDBLike) -> None:
        self._db = db

    async def init_schema(self) -> None:
        """Create tables and indexes if not exist."""
        await self._db.execute(SCHEMA_SQL)

    async def load(self, guest_id: str) -> GuestMemory:
        """Load guest memory from PostgreSQL.

        Returns empty GuestMemory if guest is new.

        Args:
            guest_id: Guest identifier.

        Returns:
            GuestMemory with all learned observations.
        """
        row = await self._db.fetchone(
            "SELECT * FROM guest_memories WHERE guest_id = $1",
            guest_id,
        )
        if not row:
            return GuestMemory(guest_id=guest_id)
        return _row_to_memory(row)

    async def save(self, memory: GuestMemory) -> None:
        """Persist guest memory to PostgreSQL (upsert).

        Args:
            memory: Guest memory to save.
        """
        await self._db.execute(
            _UPSERT_SQL,
            memory.guest_id,
            memory.total_stays,
            memory.total_interactions,
            memory.language,
            memory.communication_style,
            json.dumps(memory.satisfaction_scores),
            memory.avg_satisfaction,
            json.dumps(memory.preferences),
            json.dumps(memory.common_requests),
            json.dumps(memory.incidents),
            json.dumps(memory.risk_flags),
            json.dumps(memory.patterns),
            json.dumps(memory.property_history),
            memory.first_seen or None,
            memory.last_seen or None,
            json.dumps(memory.notes),
        )

    async def exists(self, guest_id: str) -> bool:
        """Check if we have memory for a guest.

        Args:
            guest_id: Guest identifier.

        Returns:
            True if guest has stored memory.
        """
        row = await self._db.fetchone(
            "SELECT 1 FROM guest_memories WHERE guest_id = $1",
            guest_id,
        )
        return row is not None

    async def record_interaction(
        self,
        guest_id: str,
        property_id: str,
        language: str = "",
        satisfaction: int | None = None,
    ) -> GuestMemory:
        """Record a guest interaction and update memory.

        Args:
            guest_id: Guest identifier.
            property_id: Property of current interaction.
            language: Detected message language.
            satisfaction: Sentiment score (1-5) if detected.

        Returns:
            Updated GuestMemory.
        """
        memory = await self.load(guest_id)
        memory = _update_interaction(memory, property_id, language, satisfaction)
        await self.save(memory)
        return memory

    async def add_preference(
        self,
        guest_id: str,
        key: str,
        value: str,
    ) -> GuestMemory:
        """Add a learned preference for a guest.

        Args:
            guest_id: Guest identifier.
            key: Preference key.
            value: Preference value.

        Returns:
            Updated GuestMemory.
        """
        memory = await self.load(guest_id)
        memory.preferences[key] = value
        memory.last_seen = _now_iso()
        await self.save(memory)
        return memory

    async def add_incident(
        self,
        guest_id: str,
        incident_type: str,
        description: str = "",
    ) -> GuestMemory:
        """Record a guest incident.

        Args:
            guest_id: Guest identifier.
            incident_type: Type (complaint, damage, noise, etc.).
            description: Incident description.

        Returns:
            Updated GuestMemory.
        """
        memory = await self.load(guest_id)
        memory.incidents.append({
            "type": incident_type,
            "description": description,
            "date": _now_iso(),
        })
        memory = _update_risk_flags(memory)
        memory.last_seen = _now_iso()
        await self.save(memory)
        return memory

    async def add_pattern(
        self,
        guest_id: str,
        pattern: str,
    ) -> GuestMemory:
        """Record an observed behavioral pattern.

        Args:
            guest_id: Guest identifier.
            pattern: Pattern description.

        Returns:
            Updated GuestMemory.
        """
        memory = await self.load(guest_id)
        if pattern not in memory.patterns:
            memory.patterns.append(pattern)
        memory.last_seen = _now_iso()
        await self.save(memory)
        return memory

    async def record_stay_completed(
        self,
        guest_id: str,
        property_id: str,
        satisfaction: int,
    ) -> GuestMemory:
        """Record a completed stay with final satisfaction score.

        Args:
            guest_id: Guest identifier.
            property_id: Property of completed stay.
            satisfaction: Overall stay satisfaction (1-5).

        Returns:
            Updated GuestMemory.
        """
        memory = await self.load(guest_id)
        memory.total_stays += 1
        memory.satisfaction_scores.append(satisfaction)
        memory.avg_satisfaction = _calculate_avg(memory.satisfaction_scores)

        if property_id not in memory.property_history:
            memory.property_history.append(property_id)

        memory.last_seen = _now_iso()
        await self.save(memory)
        return memory

    async def find_by_property(
        self,
        property_id: str,
        limit: int = 50,
    ) -> list[GuestMemory]:
        """Find guests who stayed at a property.

        Args:
            property_id: Property identifier.
            limit: Max results.

        Returns:
            List of GuestMemory for guests at this property.
        """
        rows = await self._db.fetchall(
            "SELECT * FROM guest_memories "
            "WHERE property_history @> $1::jsonb "
            "ORDER BY last_seen DESC LIMIT $2",
            json.dumps([property_id]),
            limit,
        )
        return [_row_to_memory(row) for row in rows]

    async def find_high_risk(self, limit: int = 20) -> list[GuestMemory]:
        """Find guests with active risk flags.

        Args:
            limit: Max results.

        Returns:
            List of high-risk GuestMemory.
        """
        rows = await self._db.fetchall(
            "SELECT * FROM guest_memories "
            "WHERE jsonb_array_length(risk_flags) > 0 "
            "ORDER BY last_seen DESC LIMIT $1",
            limit,
        )
        return [_row_to_memory(row) for row in rows]


# ── SQL Templates ────────────────────────────────────────────────── #


_UPSERT_SQL = """
INSERT INTO guest_memories (
    guest_id, total_stays, total_interactions, language,
    communication_style, satisfaction_scores, avg_satisfaction,
    preferences, common_requests, incidents, risk_flags,
    patterns, property_history, first_seen, last_seen, notes,
    updated_at
) VALUES (
    $1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9::jsonb,
    $10::jsonb, $11::jsonb, $12::jsonb, $13::jsonb, $14, $15,
    $16::jsonb, NOW()
)
ON CONFLICT (guest_id) DO UPDATE SET
    total_stays = EXCLUDED.total_stays,
    total_interactions = EXCLUDED.total_interactions,
    language = EXCLUDED.language,
    communication_style = EXCLUDED.communication_style,
    satisfaction_scores = EXCLUDED.satisfaction_scores,
    avg_satisfaction = EXCLUDED.avg_satisfaction,
    preferences = EXCLUDED.preferences,
    common_requests = EXCLUDED.common_requests,
    incidents = EXCLUDED.incidents,
    risk_flags = EXCLUDED.risk_flags,
    patterns = EXCLUDED.patterns,
    property_history = EXCLUDED.property_history,
    first_seen = COALESCE(EXCLUDED.first_seen, guest_memories.first_seen),
    last_seen = EXCLUDED.last_seen,
    notes = EXCLUDED.notes,
    updated_at = NOW()
"""


# ── Update helpers ───────────────────────────────────────────────── #


def _update_interaction(
    memory: GuestMemory,
    property_id: str,
    language: str,
    satisfaction: int | None,
) -> GuestMemory:
    """Update memory with new interaction data.

    Args:
        memory: Current guest memory.
        property_id: Property of interaction.
        language: Detected language.
        satisfaction: Sentiment score if available.

    Returns:
        Updated GuestMemory.
    """
    now = _now_iso()
    memory.total_interactions += 1
    memory.last_seen = now

    if not memory.first_seen:
        memory.first_seen = now

    if language and language != "en":
        memory.language = language

    if property_id and property_id not in memory.property_history:
        memory.property_history.append(property_id)

    if satisfaction is not None:
        memory.satisfaction_scores.append(satisfaction)
        memory.avg_satisfaction = _calculate_avg(memory.satisfaction_scores)

    return memory


def _update_risk_flags(memory: GuestMemory) -> GuestMemory:
    """Recalculate risk flags from incident history.

    Args:
        memory: Guest memory with incidents.

    Returns:
        Updated memory with current risk flags.
    """
    memory.risk_flags = []

    damage_count = sum(
        1 for i in memory.incidents if i.get("type") == "damage"
    )
    if damage_count >= 2:
        memory.risk_flags.append("repeated_damage")

    complaint_count = sum(
        1 for i in memory.incidents if i.get("type") == "complaint"
    )
    if complaint_count >= 3:
        memory.risk_flags.append("frequent_complaints")

    noise_count = sum(
        1 for i in memory.incidents if i.get("type") == "noise"
    )
    if noise_count >= 2:
        memory.risk_flags.append("noise_history")

    return memory


def _calculate_avg(scores: list[int]) -> float:
    """Calculate rolling average from score list.

    Args:
        scores: List of satisfaction scores.

    Returns:
        Average score, or 0.0 if empty.
    """
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 2)


def _row_to_memory(row: dict[str, Any]) -> GuestMemory:
    """Convert database row to GuestMemory.

    Args:
        row: Database row dict.

    Returns:
        Reconstructed GuestMemory.
    """
    return GuestMemory(
        guest_id=row.get("guest_id", ""),
        total_stays=row.get("total_stays", 0),
        total_interactions=row.get("total_interactions", 0),
        language=row.get("language", "en"),
        communication_style=row.get("communication_style", "neutral"),
        satisfaction_scores=_parse_json(row.get("satisfaction_scores"), []),
        avg_satisfaction=float(row.get("avg_satisfaction", 0.0)),
        preferences=_parse_json(row.get("preferences"), {}),
        common_requests=_parse_json(row.get("common_requests"), []),
        incidents=_parse_json(row.get("incidents"), []),
        risk_flags=_parse_json(row.get("risk_flags"), []),
        patterns=_parse_json(row.get("patterns"), []),
        property_history=_parse_json(row.get("property_history"), []),
        first_seen=_ts_to_iso(row.get("first_seen")),
        last_seen=_ts_to_iso(row.get("last_seen")),
        notes=_parse_json(row.get("notes"), []),
    )


def _parse_json(value: Any, default: Any) -> Any:
    """Parse JSON string or return value as-is if already parsed.

    Args:
        value: JSON string or already-parsed value.
        default: Default if None.

    Returns:
        Parsed value.
    """
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _ts_to_iso(value: Any) -> str:
    """Convert timestamp to ISO string.

    Args:
        value: Timestamp, datetime, or string.

    Returns:
        ISO 8601 string, or empty string.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
