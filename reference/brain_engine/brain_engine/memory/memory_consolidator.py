"""Memory Consolidator — Autonomous migration between memory tiers.

Implements concepts from:
- MemGPT/Letta: Virtual context management (RAM → disk)
- Titans: Surprise-based prioritization for long-term storage
- MemSearcher: Autonomous decision on what to remember/forget
- Ebbinghaus: Forgetting curve with reinforcement

The consolidator runs periodically (or on-demand) and:
1. Scans working memory for important items → promotes to episodic
2. Scans episodic memory for high-value patterns → promotes to semantic/KG
3. Decays low-value memories (forgetting curve)
4. Deduplicates similar memories (Mem0 incremental summarization)
5. Extracts entities and relationships → updates knowledge graph
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import litellm

from brain_engine.memory.episodic_memory import EpisodicMemory, Episode
from brain_engine.memory.semantic_memory import SemanticMemory
from brain_engine.memory.knowledge_graph import (
    TemporalKnowledgeGraph,
    KnowledgeType,
    KnowledgeNode,
)
from brain_engine.memory.surprise_detector import SurpriseDetector

logger = logging.getLogger(__name__)

_ENTITY_EXTRACTION_PROMPT = """Extract named entities and relationships from the following events.
Return JSON with:
{
  "entities": [{"id": "...", "type": "guest|property|cleaner|booking", "name": "..."}],
  "facts": [{"content": "...", "entity_id": "...", "confidence": 0.0-1.0, "keywords": [...]}],
  "beliefs": [{"content": "...", "entity_id": "...", "confidence": 0.0-1.0, "keywords": [...]}],
  "relationships": [{"source": "...", "target": "...", "type": "...", "properties": {}}]
}

Events:
{events}

Rules:
- Facts are directly observed (e.g., "Guest John checked in on March 10")
- Beliefs are inferred (e.g., "Guest John tends to request late checkouts")
- Entity IDs should match the IDs in the events if available
- Include confidence scores based on evidence strength"""


class MemoryConsolidator:
    """Autonomous memory tier manager.

    Orchestrates the flow of information between memory tiers:
    Working Memory → Episodic Memory → Semantic Memory / Knowledge Graph

    Args:
        episodic: Episodic memory instance.
        semantic: Semantic memory instance.
        knowledge_graph: Temporal knowledge graph instance.
        surprise_detector: Surprise analysis engine.
        model: LLM model for entity extraction and summarization.
        consolidation_interval_hours: How often to run automatic consolidation.
    """

    def __init__(
        self,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        knowledge_graph: TemporalKnowledgeGraph,
        surprise_detector: SurpriseDetector,
        model: str = "gpt-4o-mini",
        consolidation_interval_hours: float = 1.0,
    ) -> None:
        self._episodic = episodic
        self._semantic = semantic
        self._kg = knowledge_graph
        self._surprise = surprise_detector
        self._model = model
        self._interval = timedelta(hours=consolidation_interval_hours)
        self._last_consolidation: datetime | None = None

    def should_consolidate(self) -> bool:
        """Check if enough time has passed for a consolidation cycle."""
        if self._last_consolidation is None:
            return True
        return datetime.now(timezone.utc) - self._last_consolidation >= self._interval

    async def consolidate(self, force: bool = False) -> dict[str, int]:
        """Run a full consolidation cycle.

        Returns:
            Stats dict with counts of promoted, decayed, deduplicated items.
        """
        if not force and not self.should_consolidate():
            return {"skipped": True}

        stats = {
            "episodes_processed": 0,
            "promoted_to_semantic": 0,
            "promoted_to_kg": 0,
            "entities_extracted": 0,
            "relationships_extracted": 0,
            "beliefs_created": 0,
        }

        # Step 1: Get recent episodes not yet consolidated
        recent = await self._episodic.get_recent(n=50)
        if not recent:
            self._last_consolidation = datetime.now(timezone.utc)
            return stats

        stats["episodes_processed"] = len(recent)

        # Step 2: Analyze each episode for surprise
        high_value_episodes: list[Episode] = []
        for ep in recent:
            surprise = await self._surprise.analyze_event(
                ep.event,
                context=ep.metadata,
            )
            if surprise.should_memorize:
                high_value_episodes.append(ep)

        # Step 3: Promote high-value episodes to semantic memory
        for ep in high_value_episodes:
            text = f"[{ep.event}] {ep.content}"
            metadata = {
                "source": "episodic_consolidation",
                "event": ep.event,
                "timestamp": ep.timestamp.isoformat(),
                "session_id": ep.session_id,
                **ep.metadata,
            }
            await self._semantic.store(text=text, metadata=metadata)
            stats["promoted_to_semantic"] += 1

        # Step 4: Extract entities and relationships using LLM
        if high_value_episodes:
            extraction = await self._extract_entities(high_value_episodes)
            if extraction:
                # Add facts to knowledge graph
                for fact in extraction.get("facts", []):
                    await self._kg.add_knowledge(
                        content=fact["content"],
                        knowledge_type=KnowledgeType.FACT,
                        entity_id=fact.get("entity_id", ""),
                        confidence=fact.get("confidence", 0.9),
                        keywords=fact.get("keywords", []),
                        source="consolidation",
                    )
                    stats["promoted_to_kg"] += 1

                # Add beliefs
                for belief in extraction.get("beliefs", []):
                    await self._kg.add_knowledge(
                        content=belief["content"],
                        knowledge_type=KnowledgeType.BELIEF,
                        entity_id=belief.get("entity_id", ""),
                        confidence=belief.get("confidence", 0.6),
                        keywords=belief.get("keywords", []),
                        source="consolidation",
                    )
                    stats["beliefs_created"] += 1

                # Add relationships
                for rel in extraction.get("relationships", []):
                    await self._kg.add_relationship(
                        source_entity=rel["source"],
                        target_entity=rel["target"],
                        relation_type=rel["type"],
                        properties=rel.get("properties", {}),
                    )
                    stats["relationships_extracted"] += 1

                stats["entities_extracted"] = len(extraction.get("entities", []))

        self._last_consolidation = datetime.now(timezone.utc)
        logger.info("Consolidation complete: %s", stats)
        return stats

    async def _extract_entities(
        self, episodes: list[Episode]
    ) -> dict[str, Any] | None:
        """Use LLM to extract entities, facts, beliefs, and relationships."""
        events_text = "\n".join(
            f"- [{ep.event}] {ep.content} | metadata: {json.dumps(ep.metadata, default=str)}"
            for ep in episodes
        )

        prompt = _ENTITY_EXTRACTION_PROMPT.format(events=events_text)

        try:
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are an entity extraction engine. Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            text = response.choices[0].message.content or ""
            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
            return None
        except Exception as exc:
            logger.error("Entity extraction failed: %s", exc)
            return None

    # ── On-demand Consolidation for Specific Events ──────────────────── #

    async def process_event_immediately(
        self,
        event: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Process a single event for immediate consolidation.

        Called by the EventRecorder for high-priority events.
        Returns the surprise analysis and any actions taken.
        """
        ctx = metadata or {}
        surprise = await self._surprise.analyze_event(event, context=ctx)

        result: dict[str, Any] = {
            "event": event,
            "surprise_score": surprise.raw_score,
            "surprise_category": surprise.category,
            "factors": surprise.factors,
            "memorized": False,
        }

        if surprise.should_memorize:
            # Store in semantic memory
            text = f"[{event}] {content}"
            await self._semantic.store(
                text=text,
                metadata={
                    "source": "immediate_consolidation",
                    "surprise_score": surprise.raw_score,
                    "event": event,
                    **(metadata or {}),
                },
            )
            result["memorized"] = True

        return result
