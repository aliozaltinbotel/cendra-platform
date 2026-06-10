"""Temporal Knowledge Graph — Entity-aware graph with bi-temporal modeling.

Implements concepts from:
- Zep: Temporal knowledge graphs with bi-temporal timestamps (event time + record time)
- Hindsight: Facts vs beliefs, confidence scores, opinion evolution
- A-MEM: Zettelkasten-style atomic notes linked in a knowledge graph

Every piece of knowledge is stored as either a Fact or Belief:
- Facts: Verified information (e.g., "Guest John checked in on 2026-03-10")
- Beliefs: Inferred information (e.g., "Guest John tends to request late checkouts")

Entities (guests, properties, cleaners) are nodes. Relationships are edges
with temporal validity windows and confidence scores.

Storage: Redis for graph structure + Qdrant for semantic search over knowledge.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from brain_engine.memory.kg_as_of import reconstruct_as_of
from brain_engine.streaming.emit_helpers import emit_memory_retrieved

logger = logging.getLogger(__name__)


class KnowledgeType(str, Enum):
    FACT = "fact"           # Verified, observed information
    BELIEF = "belief"       # Inferred, uncertain information
    PREFERENCE = "preference"  # Guest/entity behavioral preference
    RULE = "rule"           # Learned operational rule


class EntityType(str, Enum):
    GUEST = "guest"
    PROPERTY = "property"
    CLEANER = "cleaner"
    BOOKING = "booking"
    INCIDENT = "incident"
    CLAIM = "claim"


@dataclass
class KnowledgeNode:
    """An atomic unit of knowledge in the graph (Zettelkasten note).

    Bi-temporal: tracks both when the event happened (event_time) and
    when it was recorded in the system (record_time).
    """
    node_id: str = ""
    content: str = ""
    knowledge_type: str = KnowledgeType.FACT
    entity_type: str = ""
    entity_id: str = ""

    # Hindsight: confidence and evolution
    confidence: float = 1.0         # 0.0 - 1.0
    access_count: int = 0           # For forgetting curve
    reinforcement_count: int = 0    # Times confirmed/reinforced

    # Bi-temporal (Zep)
    event_time: str = ""            # When the event actually happened
    record_time: str = ""           # When we recorded this knowledge
    valid_from: str = ""            # Temporal validity start
    valid_until: str | None = None  # Temporal validity end (None = still valid)

    # A-MEM: Zettelkasten metadata
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = ""                # Where this knowledge came from
    linked_nodes: list[str] = field(default_factory=list)  # Related node IDs

    # Hindsight: opinion evolution
    previous_values: list[dict[str, Any]] = field(default_factory=list)
    superseded_by: str | None = None  # If updated, points to new node

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeNode:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Relationship:
    """An edge between two entities in the knowledge graph."""
    rel_id: str = ""
    source_entity: str = ""         # entity_id
    target_entity: str = ""         # entity_id
    relation_type: str = ""         # e.g., "booked", "damaged", "cleaned", "stayed_at"
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    event_time: str = ""
    record_time: str = ""
    valid_from: str = ""
    valid_until: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Relationship:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TemporalKnowledgeGraph:
    """Redis-backed temporal knowledge graph with entity awareness.

    Key structure:
        brain:kg:node:{node_id}              → KnowledgeNode JSON
        brain:kg:entity:{entity_id}:nodes    → Set of node_ids
        brain:kg:rel:{rel_id}                → Relationship JSON
        brain:kg:entity:{entity_id}:rels     → Set of rel_ids
        brain:kg:type:{knowledge_type}       → Set of node_ids
        brain:kg:keywords:{keyword}          → Set of node_ids
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        workspace_id: str = "",
    ) -> None:
        import redis.asyncio as aioredis
        from brain_engine.memory.tenant import build_prefix
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = build_prefix("brain:kg:", workspace_id)

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    # ── Knowledge Nodes ──────────────────────────────────────────────── #

    async def add_knowledge(
        self,
        content: str,
        knowledge_type: KnowledgeType = KnowledgeType.FACT,
        entity_type: str = "",
        entity_id: str = "",
        confidence: float = 1.0,
        event_time: str = "",
        keywords: list[str] | None = None,
        tags: list[str] | None = None,
        source: str = "",
        linked_nodes: list[str] | None = None,
    ) -> KnowledgeNode:
        """Add a new knowledge node to the graph."""
        now = datetime.now(timezone.utc).isoformat()
        node = KnowledgeNode(
            node_id=str(uuid.uuid4())[:12],
            content=content,
            knowledge_type=knowledge_type,
            entity_type=entity_type,
            entity_id=entity_id,
            confidence=confidence,
            event_time=event_time or now,
            record_time=now,
            valid_from=event_time or now,
            keywords=keywords or [],
            tags=tags or [],
            source=source,
            linked_nodes=linked_nodes or [],
        )

        pipe = self._redis.pipeline()

        # Store node
        pipe.set(self._key("node", node.node_id), json.dumps(node.to_dict()))

        # Index by entity
        if entity_id:
            pipe.sadd(self._key("entity", entity_id, "nodes"), node.node_id)

        # Index by type
        pipe.sadd(self._key("type", knowledge_type), node.node_id)

        # Index by keywords (A-MEM: keyword-based linking)
        for kw in node.keywords:
            pipe.sadd(self._key("keywords", kw.lower()), node.node_id)

        # Link to related nodes
        for linked_id in node.linked_nodes:
            pipe.sadd(self._key("node", node.node_id, "links"), linked_id)
            pipe.sadd(self._key("node", linked_id, "links"), node.node_id)

        await pipe.execute()
        logger.info("Added knowledge: [%s] %s (confidence=%.2f)", knowledge_type, content[:80], confidence)
        return node

    async def get_node(self, node_id: str) -> KnowledgeNode | None:
        raw = await self._redis.get(self._key("node", node_id))
        if raw:
            node = KnowledgeNode.from_dict(json.loads(raw))
            # Update access count (forgetting curve)
            node.access_count += 1
            await self._redis.set(self._key("node", node_id), json.dumps(node.to_dict()))
            return node
        return None

    async def get_entity_knowledge(
        self,
        entity_id: str,
        knowledge_type: KnowledgeType | None = None,
        min_confidence: float = 0.0,
        as_of: datetime | None = None,
    ) -> list[KnowledgeNode]:
        """Get all knowledge about a specific entity.

        When ``as_of`` is given, each node is reconstructed to the value it
        held at that wall-clock instant (transaction-time time-travel — see
        :func:`brain_engine.memory.kg_as_of.reconstruct_as_of`): nodes not
        yet recorded by then are dropped, and ``min_confidence`` /
        ``knowledge_type`` filter on the *reconstructed* value.  ``as_of=None``
        keeps the original current-view behaviour unchanged.
        """
        node_ids = await self._redis.smembers(self._key("entity", entity_id, "nodes"))
        nodes = []
        for nid in node_ids:
            node = await self.get_node(nid)
            if node is None:
                continue
            if as_of is not None:
                reconstructed = reconstruct_as_of(node, as_of)
                if reconstructed is None:
                    continue
                node = reconstructed
            if node.confidence >= min_confidence:
                if knowledge_type is None or node.knowledge_type == knowledge_type:
                    if node.superseded_by is None:  # Skip superseded
                        nodes.append(node)
        return sorted(nodes, key=lambda n: n.event_time, reverse=True)

    async def get_facts(self, entity_id: str) -> list[KnowledgeNode]:
        """Get only verified facts about an entity (Hindsight: fact/belief separation)."""
        return await self.get_entity_knowledge(entity_id, KnowledgeType.FACT)

    async def get_beliefs(self, entity_id: str) -> list[KnowledgeNode]:
        """Get inferred beliefs about an entity (Hindsight: fact/belief separation)."""
        return await self.get_entity_knowledge(entity_id, KnowledgeType.BELIEF)

    # ── Hindsight: Opinion Evolution ─────────────────────────────────── #

    async def update_knowledge(
        self,
        node_id: str,
        new_content: str,
        new_confidence: float | None = None,
        reason: str = "",
    ) -> KnowledgeNode | None:
        """Update a knowledge node, preserving history (opinion evolution).

        Instead of overwriting, the old value is archived in previous_values
        and the node content is updated. This enables tracking how beliefs
        evolve over time.
        """
        node = await self.get_node(node_id)
        if not node:
            return None

        now = datetime.now(timezone.utc).isoformat()

        # Archive old value
        node.previous_values.append({
            "content": node.content,
            "confidence": node.confidence,
            "changed_at": now,
            "reason": reason,
        })

        node.content = new_content
        if new_confidence is not None:
            node.confidence = new_confidence
        node.reinforcement_count += 1

        await self._redis.set(self._key("node", node.node_id), json.dumps(node.to_dict()))
        logger.info("Updated knowledge %s: %s → %s", node_id, node.previous_values[-1]["content"][:40], new_content[:40])
        return node

    async def reinforce_knowledge(self, node_id: str, boost: float = 0.05) -> None:
        """Reinforce a piece of knowledge (increase confidence).

        Called when evidence confirms this knowledge. Counteracts the
        forgetting curve decay.
        """
        node = await self.get_node(node_id)
        if node:
            node.confidence = min(1.0, node.confidence + boost)
            node.reinforcement_count += 1
            await self._redis.set(self._key("node", node.node_id), json.dumps(node.to_dict()))

    async def invalidate_knowledge(self, node_id: str, reason: str = "") -> None:
        """Mark knowledge as no longer valid (temporal validity end)."""
        node = await self.get_node(node_id)
        if node:
            node.valid_until = datetime.now(timezone.utc).isoformat()
            node.previous_values.append({
                "content": node.content,
                "confidence": node.confidence,
                "changed_at": node.valid_until,
                "reason": f"Invalidated: {reason}",
            })
            node.confidence = 0.0
            await self._redis.set(self._key("node", node.node_id), json.dumps(node.to_dict()))

    # ── Relationships ────────────────────────────────────────────────── #

    async def add_relationship(
        self,
        source_entity: str,
        target_entity: str,
        relation_type: str,
        properties: dict[str, Any] | None = None,
        confidence: float = 1.0,
        event_time: str = "",
    ) -> Relationship:
        """Add a relationship edge between two entities."""
        now = datetime.now(timezone.utc).isoformat()
        rel = Relationship(
            rel_id=str(uuid.uuid4())[:12],
            source_entity=source_entity,
            target_entity=target_entity,
            relation_type=relation_type,
            properties=properties or {},
            confidence=confidence,
            event_time=event_time or now,
            record_time=now,
            valid_from=event_time or now,
        )

        pipe = self._redis.pipeline()
        pipe.set(self._key("rel", rel.rel_id), json.dumps(rel.to_dict()))
        pipe.sadd(self._key("entity", source_entity, "rels"), rel.rel_id)
        pipe.sadd(self._key("entity", target_entity, "rels"), rel.rel_id)
        pipe.sadd(self._key("reltype", relation_type), rel.rel_id)
        await pipe.execute()

        logger.info("Added relationship: %s -[%s]-> %s", source_entity, relation_type, target_entity)
        return rel

    async def get_entity_relationships(
        self,
        entity_id: str,
        relation_type: str | None = None,
    ) -> list[Relationship]:
        """Get all relationships involving an entity."""
        rel_ids = await self._redis.smembers(self._key("entity", entity_id, "rels"))
        rels = []
        for rid in rel_ids:
            raw = await self._redis.get(self._key("rel", rid))
            if raw:
                rel = Relationship.from_dict(json.loads(raw))
                if relation_type is None or rel.relation_type == relation_type:
                    if rel.valid_until is None:  # Only active relationships
                        rels.append(rel)
        return sorted(rels, key=lambda r: r.event_time, reverse=True)

    # ── Multi-hop Queries (A-MEM) ────────────────────────────────────── #

    async def find_connected_knowledge(
        self,
        entity_id: str,
        max_hops: int = 2,
    ) -> list[KnowledgeNode]:
        """Find knowledge reachable within N hops from an entity.

        Implements A-MEM's multi-hop reasoning through the knowledge graph.
        """
        visited: set[str] = set()
        result: list[KnowledgeNode] = []

        # Start with direct knowledge
        direct_nodes = await self.get_entity_knowledge(entity_id)
        for node in direct_nodes:
            if node.node_id not in visited:
                visited.add(node.node_id)
                result.append(node)

        # Follow relationships for multi-hop
        current_entities = {entity_id}
        for _ in range(max_hops):
            next_entities: set[str] = set()
            for eid in current_entities:
                rels = await self.get_entity_relationships(eid)
                for rel in rels:
                    other = rel.target_entity if rel.source_entity == eid else rel.source_entity
                    if other not in current_entities:
                        next_entities.add(other)
                        hop_nodes = await self.get_entity_knowledge(other)
                        for node in hop_nodes:
                            if node.node_id not in visited:
                                visited.add(node.node_id)
                                result.append(node)
            current_entities = next_entities
            if not current_entities:
                break

        return result

    # ── Keyword Search (A-MEM) ───────────────────────────────────────── #

    async def search_by_keywords(self, keywords: list[str]) -> list[KnowledgeNode]:
        """Find knowledge nodes matching any of the given keywords."""
        t0 = time.perf_counter()
        node_ids: set[str] = set()
        for kw in keywords:
            ids = await self._redis.smembers(self._key("keywords", kw.lower()))
            node_ids.update(ids)

        nodes = []
        for nid in node_ids:
            node = await self.get_node(nid)
            if node and node.superseded_by is None:
                nodes.append(node)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        emit_memory_retrieved(
            tier="knowledge_graph",
            query=",".join(keywords),
            hits=[
                {
                    "id": getattr(n, "node_id", ""),
                    "score": float(getattr(n, "confidence", 1.0)),
                    "excerpt": getattr(n, "content", ""),
                }
                for n in nodes
            ],
            latency_ms=latency_ms,
        )
        return nodes

    # ── Context Builder ──────────────────────────────────────────────── #

    async def build_entity_context(self, entity_id: str) -> str:
        """Build a rich text context about an entity for LLM prompt injection.

        Separates facts and beliefs with confidence scores (Hindsight pattern).
        """
        facts = await self.get_facts(entity_id)
        beliefs = await self.get_beliefs(entity_id)
        rels = await self.get_entity_relationships(entity_id)

        lines: list[str] = []

        if facts:
            lines.append("Known facts:")
            for f in facts[:10]:
                lines.append(f"  - {f.content} [confidence: {f.confidence:.0%}]")

        if beliefs:
            lines.append("Inferred beliefs:")
            for b in beliefs[:5]:
                lines.append(f"  - {b.content} [confidence: {b.confidence:.0%}]")

        if rels:
            lines.append("Relationships:")
            for r in rels[:10]:
                lines.append(f"  - [{r.relation_type}] → {r.target_entity} ({r.event_time[:10]})")

        return "\n".join(lines)

    async def close(self) -> None:
        await self._redis.close()
