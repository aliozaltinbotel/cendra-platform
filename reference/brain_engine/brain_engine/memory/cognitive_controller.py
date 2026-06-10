"""Cognitive Controller — CoALA-inspired cognitive architecture for the Brain Engine.

Implements concepts from:
- CoALA (Princeton/Berkeley): Cognitive Architectures for Language Agents
  - Perception → Memory → Reasoning → Action decision cycle
  - Dual Process Theory: System 1 (fast/intuitive) vs System 2 (slow/deliberate)
- Metacognition: Self-monitoring and self-correction
- Theory of Mind: Modeling mental states of guests

The Cognitive Controller is the "brain" that orchestrates all memory systems.
It decides:
1. What to remember (autonomous memory management)
2. How to retrieve context (multi-strategy retrieval)
3. When to reason carefully vs act quickly (dual process)
4. What the user/guest likely needs (theory of mind)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from brain_engine.memory.working_memory import WorkingMemory
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.semantic_memory import SemanticMemory
from brain_engine.memory.knowledge_graph import TemporalKnowledgeGraph, KnowledgeType
from brain_engine.memory.guest_history import GuestHistoryStore
from brain_engine.memory.surprise_detector import SurpriseDetector
from brain_engine.memory.procedural_memory import ProceduralMemory
from brain_engine.memory.memory_consolidator import MemoryConsolidator
from brain_engine.memory.metacognition import MetacognitiveMonitor
from brain_engine.streaming.emit_helpers import emit_cognitive_mode_changed

logger = logging.getLogger(__name__)


@dataclass
class CognitiveState:
    """Current cognitive state of the agent.

    Tracks attention, confidence, and reasoning mode (System 1/2).
    """
    # Dual Process Theory
    reasoning_mode: str = "system1"  # "system1" (fast) or "system2" (deliberate)

    # Attention
    current_focus: str = ""          # What the agent is currently focused on
    attention_entities: list[str] = field(default_factory=list)  # Active entity IDs

    # Metacognition
    confidence_level: float = 0.8    # How confident the agent is in its current reasoning
    uncertainty_flags: list[str] = field(default_factory=list)

    # Theory of Mind
    guest_emotional_state: str = "neutral"  # neutral, frustrated, happy, confused, angry
    guest_urgency: str = "normal"           # low, normal, high, critical

    # Context
    active_procedures: list[str] = field(default_factory=list)
    surprise_alerts: list[str] = field(default_factory=list)


class CognitiveController:
    """CoALA-inspired cognitive architecture controller.

    Orchestrates all memory systems through a cognitive decision cycle:
    1. PERCEIVE: Analyze incoming event/message
    2. REMEMBER: Retrieve relevant context from all memory tiers
    3. REASON: Decide on response strategy (System 1 or 2)
    4. ACT: Execute decision and update memory

    Args:
        working: Working memory (current session scratchpad).
        episodic: Episodic memory (event history).
        semantic: Semantic memory (long-term knowledge).
        knowledge_graph: Temporal knowledge graph.
        guest_history: Guest/booking/incident store.
        surprise_detector: Surprise analysis engine.
        procedural: Procedural memory (behavioral patterns).
        consolidator: Memory tier migration manager.
    """

    def __init__(
        self,
        working: WorkingMemory,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        knowledge_graph: TemporalKnowledgeGraph,
        guest_history: GuestHistoryStore,
        surprise_detector: SurpriseDetector,
        procedural: ProceduralMemory,
        consolidator: MemoryConsolidator,
        active_process_store: Any | None = None,
        azure_search: Any | None = None,
    ) -> None:
        self._working = working
        self._episodic = episodic
        self._semantic = semantic
        self._kg = knowledge_graph
        self._guest_history = guest_history
        self._surprise = surprise_detector
        self._procedural = procedural
        self._consolidator = consolidator
        self._active_processes = active_process_store
        self._azure_search = azure_search
        self._metacognition = MetacognitiveMonitor()
        self._state = CognitiveState()

    @property
    def state(self) -> CognitiveState:
        return self._state

    @property
    def metacognition(self) -> MetacognitiveMonitor:
        return self._metacognition

    # ── 1. PERCEIVE ──────────────────────────────────────────────────── #

    async def perceive(
        self,
        event: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze an incoming event and determine its significance.

        This is the first step of the cognitive cycle. It:
        - Analyzes surprise level
        - Determines reasoning mode (System 1 or 2)
        - Updates attention and focus
        """
        ctx = metadata or {}

        # Surprise analysis (Titans)
        surprise = await self._surprise.analyze_event(event, context=ctx)

        # Determine reasoning mode (Dual Process Theory)
        previous_mode = self._state.reasoning_mode
        if surprise.raw_score >= 0.7 or ctx.get("urgency") == "critical":
            new_mode = "system2"  # Slow, deliberate
        else:
            new_mode = "system1"  # Fast, pattern-based
        self._state.reasoning_mode = new_mode
        if new_mode != previous_mode:
            trigger = (
                "urgency_critical" if ctx.get("urgency") == "critical"
                else "surprise_threshold"
            )
            emit_cognitive_mode_changed(
                from_mode=previous_mode,
                to_mode=new_mode,
                trigger=trigger,
                reasoning=(
                    f"surprise={surprise.raw_score:.3f} "
                    f"urgency={ctx.get('urgency', 'normal')} event={event}"
                ),
            )

        # Update attention
        self._state.current_focus = event
        entity_ids = []
        for key in ("guest_id", "property_id", "booking_id", "incident_id"):
            if ctx.get(key):
                entity_ids.append(ctx[key])
        self._state.attention_entities = entity_ids

        # Theory of Mind: Estimate guest emotional state
        if event in ("guest_dispute", "complaint"):
            self._state.guest_emotional_state = "frustrated"
            self._state.guest_urgency = "high"
        elif event == "damage_detected":
            self._state.guest_urgency = "high"
        elif event == "incident_resolved":
            self._state.guest_emotional_state = "neutral"
            self._state.guest_urgency = "normal"

        # Surprise alerts
        if surprise.category in ("surprising", "shocking"):
            self._state.surprise_alerts = surprise.factors

        perception = {
            "event": event,
            "surprise": {
                "score": surprise.raw_score,
                "category": surprise.category,
                "factors": surprise.factors,
                "should_memorize": surprise.should_memorize,
            },
            "reasoning_mode": self._state.reasoning_mode,
            "attention_entities": entity_ids,
            "guest_state": self._state.guest_emotional_state,
        }

        logger.info(
            "Perceived: %s (surprise=%.2f, mode=%s)",
            event, surprise.raw_score, self._state.reasoning_mode,
        )
        return perception

    # ── 2. REMEMBER (Multi-Strategy Retrieval) ───────────────────────── #

    async def remember(
        self,
        query: str = "",
        entity_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Retrieve relevant context from all memory tiers.

        Implements Hindsight's multi-strategy retrieval:
        1. Recency: Recent episodes from episodic memory
        2. Relevance: Semantic search over long-term knowledge
        3. Entity context: Knowledge graph facts/beliefs about entities
        4. History: Guest/property history from persistent store
        5. Procedures: Applicable behavioral patterns

        Returns a unified context dict for the reasoning step.
        """
        entities = entity_ids or self._state.attention_entities
        context: dict[str, Any] = {}

        # Strategy 1: Recency — Recent episodes
        recent_episodes = await self._episodic.get_recent(n=10)
        context["recent_events"] = [
            {"event": ep.event, "content": ep.content, "time": ep.timestamp.isoformat()}
            for ep in recent_episodes
        ]

        # Strategy 2: Relevance — Semantic search
        if query:
            semantic_results = await self._semantic.search(query, top_k=5, score_threshold=0.3)
            context["relevant_knowledge"] = [
                {"text": r.text, "score": r.score, "metadata": r.metadata}
                for r in semantic_results
            ]

        # Strategy 3: Entity knowledge graph
        for eid in entities:
            facts = await self._kg.get_facts(eid)
            beliefs = await self._kg.get_beliefs(eid)
            if facts or beliefs:
                context[f"entity_{eid}"] = {
                    "facts": [{"content": f.content, "confidence": f.confidence} for f in facts[:5]],
                    "beliefs": [{"content": b.content, "confidence": b.confidence} for b in beliefs[:3]],
                }

        # Strategy 4: Guest/property history
        for eid in entities:
            guest_ctx = await self._guest_history.build_guest_context(eid)
            if guest_ctx:
                context[f"guest_history_{eid}"] = guest_ctx
            property_ctx = await self._guest_history.build_property_context(eid)
            if property_ctx:
                context[f"property_history_{eid}"] = property_ctx

        # Strategy 5: Applicable procedures (ranked by source priority)
        event = self._state.current_focus
        if event:
            procedures = await self._procedural.find_applicable_procedures(
                event, metadata={"entity_ids": entities}
            )
            # Rank by source priority: immutable > manual > learned
            ranked = self._rank_procedures_by_source(procedures)
            context["applicable_procedures"] = [
                {
                    "name": p.name,
                    "description": p.description,
                    "actions": p.actions,
                    "confidence": p.confidence,
                    "source": getattr(p, "source", "unknown"),
                    "priority": getattr(p, "priority", "medium"),
                }
                for p in ranked[:5]
            ]

        # Strategy 6: Active processes (what is happening RIGHT NOW)
        context["active_processes"] = await self._retrieve_active_processes(
            entities,
        )

        # Strategy 7: SOP documents (behavioral instructions, not just info)
        if query:
            context["sop_instructions"] = await self._retrieve_sop(
                query, entities,
            )

        # Strategy 8: Azure Cognitive Search (Cendra external KB)
        if query and self._azure_search:
            context["azure_search_results"] = await self._retrieve_azure(
                query, entities,
            )

        logger.info(
            "Retrieved context: %d strategies, %d entities",
            len(context), len(entities),
        )
        return context

    async def _retrieve_active_processes(
        self,
        entity_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Retrieve active processes for relevant entities.

        Args:
            entity_ids: Entity IDs (property_id, guest_id, etc.).

        Returns:
            List of active process summaries.
        """
        if not self._active_processes:
            return []

        processes: list[dict[str, Any]] = []
        for eid in entity_ids:
            try:
                active = await self._active_processes.get_active(
                    property_id=eid,
                )
                for proc in active:
                    processes.append({
                        "process_id": proc["process_id"],
                        "type": proc["type"],
                        "status": proc["status"],
                        "property_id": proc["property_id"],
                        "deadline": proc.get("deadline", ""),
                        "participants": [
                            p.get("contact_id", "")
                            for p in proc.get("participants", [])
                        ],
                        "started_at": proc.get("started_at", ""),
                    })
            except Exception:
                logger.debug(
                    "No active processes for entity %s", eid,
                )
        return processes

    async def _retrieve_azure(
        self,
        query: str,
        entity_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Retrieve from Cendra's Azure Cognitive Search.

        Args:
            query: Search query.
            entity_ids: Entity IDs (first property_id used as filter).

        Returns:
            List of search result dicts.
        """
        try:
            property_id = entity_ids[0] if entity_ids else ""
            results = await self._azure_search.search(
                query=query,
                property_id=property_id,
                top_k=5,
            )
            return [
                {
                    "text": r.get("text", ""),
                    "score": r.get("score", 0),
                    "source": r.get("source", ""),
                    "source_type": r.get("source_type", ""),
                    "chunk_id": r.get("chunk_id", ""),
                }
                for r in results
            ]
        except Exception:
            logger.debug("Azure Search retrieval failed", exc_info=True)
            return []

    async def _retrieve_sop(
        self,
        query: str,
        entity_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Retrieve SOP documents as behavioral instructions.

        SOP results get EQUAL priority to procedural rules, not
        just informational knowledge.

        Args:
            query: Search query.
            entity_ids: Entity IDs for property filtering.

        Returns:
            List of SOP instruction dicts.
        """
        try:
            sop_results = await self._semantic.search(
                query=query,
                top_k=3,
                score_threshold=0.3,
                metadata_filter={"category": "sop"},
            )
            return [
                {
                    "text": r.text,
                    "score": r.score,
                    "type": "sop_instruction",
                    "metadata": r.metadata,
                }
                for r in sop_results
            ]
        except Exception:
            logger.debug("SOP retrieval failed", exc_info=True)
            return []

    @staticmethod
    def _rank_procedures_by_source(
        procedures: list[Any],
    ) -> list[Any]:
        """Rank procedures by source priority then confidence.

        Priority order: immutable(3) > manual(2) > learned/other(1).

        Args:
            procedures: Unranked procedures.

        Returns:
            Procedures sorted by source priority, then confidence.
        """
        source_weight = {"immutable": 3, "manual": 2, "learned": 1}
        return sorted(
            procedures,
            key=lambda p: (
                source_weight.get(getattr(p, "source", ""), 0),
                getattr(p, "confidence", 0),
            ),
            reverse=True,
        )

    # ── 3. REASON ────────────────────────────────────────────────────── #

    async def reason(
        self,
        perception: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Determine the best course of action based on perception and context.

        System 1 (fast): Use procedural memory patterns directly.
        System 2 (slow): Gather more context, consider alternatives.

        Metacognitive oversight reviews and corrects the decision before returning.

        Returns a decision dict with recommended actions.
        """
        mode = self._state.reasoning_mode
        confidence_before = self._state.confidence_level

        decision: dict[str, Any] = {
            "reasoning_mode": mode,
            "confidence": self._state.confidence_level,
            "recommended_actions": [],
            "context_summary": "",
            "metacognition": {},
        }

        # ── Metacognition: Pre-assessment ────────────────────────────────
        context_quality = {
            "recent_events_count": len(context.get("recent_events", [])),
            "relevant_knowledge_count": len(context.get("relevant_knowledge", [])),
            "entity_context_available": any(
                k.startswith("entity_") for k in context
            ),
            "procedures_count": len(context.get("applicable_procedures", [])),
        }
        assessment = self._metacognition.assess_confidence(
            event=perception.get("event", ""),
            reasoning_mode=mode,
            context_quality=context_quality,
        )

        # Auto-escalate System 1 → System 2 if metacognition recommends it
        if assessment["should_switch_to_system2"] and mode != "system2":
            previous_mode = mode
            mode = "system2"
            self._state.reasoning_mode = "system2"
            decision["reasoning_mode"] = "system2"
            decision["metacognition"]["mode_escalated"] = True
            emit_cognitive_mode_changed(
                from_mode=previous_mode,
                to_mode="system2",
                trigger="metacognition_escalation",
                reasoning=(
                    f"flags={assessment.get('flags', [])}, "
                    f"event={perception.get('event', '')}"
                ),
            )

        # ── Epistemic awareness: track what we know/don't know ────────
        if context_quality["entity_context_available"]:
            for key in context:
                if key.startswith("entity_"):
                    self._metacognition.register_known_fact(
                        f"Entity context available for {key}"
                    )
        for flag in assessment.get("flags", []):
            if "missing" in flag or "no_" in flag:
                self._metacognition.register_unknown(flag)

        # System 1: Pattern matching (fast)
        if mode == "system1":
            procedures = context.get("applicable_procedures", [])
            if procedures:
                best = procedures[0]
                decision["recommended_actions"] = best["actions"]
                decision["confidence"] = best["confidence"]
                decision["context_summary"] = f"Applying procedure: {best['name']}"
            else:
                decision["recommended_actions"] = ["proceed_default"]
                decision["confidence"] = 0.7

        # System 2: Deliberate reasoning (slow)
        elif mode == "system2":
            # Gather additional con



            surprise = perception.get("surprise", {})

            decision["metacognition"]["surprise_level"] = surprise.get("score", 0)
            decision["metacognition"]["surprise_factors"] = surprise.get("factors", [])

            # Consider guest emotional state (Theory of Mind)
            guest_state = self._state.guest_emotional_state
            if guest_state == "frustrated":
                decision["recommended_actions"].append("acknowledge_frustration")
                decision["recommended_actions"].append("prioritize_resolution")
            elif guest_state == "angry":
                decision["recommended_actions"].append("escalate_to_human")

            # Add procedure-based actions
            procedures = context.get("applicable_procedures", [])
            for proc in procedures:
                decision["recommended_actions"].extend(proc["actions"])

            # Remove duplicates while preserving order
            seen = set()
            unique_actions = []
            for action in decision["recommended_actions"]:
                if action not in seen:
                    seen.add(action)
                    unique_actions.append(action)
            decision["recommended_actions"] = unique_actions

            decision["confidence"] = min(
                0.9,
                sum(p["confidence"] for p in procedures) / max(1, len(procedures))
            ) if procedures else 0.5

        # ── Metacognition: Self-correction ────────────────────────────────
        decision = self._metacognition.evaluate_and_correct(
            decision, perception, context,
        )

        # Record reasoning trace
        confidence_after = decision["confidence"]
        corrections = decision.get("metacognition", {}).get("corrections", [])
        trace = self._metacognition.record_trace(
            event=perception.get("event", ""),
            reasoning_mode=decision["reasoning_mode"],
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            decision_summary=", ".join(decision.get("recommended_actions", [])[:3]),
            correction_applied=bool(corrections),
            correction_reason="; ".join(corrections) if corrections else "",
        )
        decision["metacognition"]["trace_id"] = trace.step_id

        # Update uncertainty flags
        if confidence_after < 0.4:
            self._state.uncertainty_flags.append(
                f"Low confidence on {self._state.current_focus}"
            )

        self._state.confidence_level = confidence_after
        return decision

    # ── 4. ACT ───────────────────────────────────────────────────────── #

    async def act(
        self,
        decision: dict[str, Any],
        event: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Execute the decision and update all memory systems.

        After acting:
        - Record the episode in episodic memory
        - Update knowledge graph if needed
        - Run consolidation if due
        """
        ctx = metadata or {}

        # Record in episodic memory
        await self._episodic.add_episode(
            event=event,
            content=content,
            metadata={
                "reasoning_mode": decision.get("reasoning_mode", "system1"),
                "confidence": decision.get("confidence", 0.5),
                "actions": decision.get("recommended_actions", []),
                **ctx,
            },
        )

        # Immediate consolidation for high-surprise events
        surprise = decision.get("metacognition", {}).get("surprise_level", 0)
        if surprise >= 0.6:
            await self._consolidator.process_event_immediately(event, content, ctx)

        # Run periodic consolidation if due
        if self._consolidator.should_consolidate():
            await self._consolidator.consolidate()

    # ── Full Cognitive Cycle ─────────────────────────────────────────── #

    async def process(
        self,
        event: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the full cognitive cycle: Perceive → Remember → Reason → Act.

        This is the main entry point for processing any event through
        the cognitive architecture.

        Returns the reasoning decision.
        """
        # 1. Perceive
        perception = await self.perceive(event, content, metadata)

        # 2. Remember
        query = content or event
        context = await self.remember(query=query)

        # 3. Reason
        decision = await self.reason(perception, context)

        # 4. Act
        await self.act(decision, event, content, metadata)

        return {
            "perception": perception,
            "decision": decision,
            "cognitive_state": {
                "reasoning_mode": self._state.reasoning_mode,
                "confidence": self._state.confidence_level,
                "guest_state": self._state.guest_emotional_state,
                "attention": self._state.attention_entities,
            },
            "metacognition": self._metacognition.introspection_report(),
        }

    # ── Build Full Context for LLM ──────────────────────────────────── #

    async def build_full_context(
        self,
        query: str = "",
        entity_ids: list[str] | None = None,
    ) -> str:
        """Build a comprehensive context string for LLM prompt injection.

        Combines all memory tiers into a single text block that can be
        injected into the LLM system prompt. This is the main interface
        between the cognitive architecture and the language model.
        """
        context = await self.remember(query=query, entity_ids=entity_ids)
        parts: list[str] = []

        # Recent events
        recent = context.get("recent_events", [])
        if recent:
            parts.append("=== Recent Events ===")
            for ev in recent[-5:]:
                parts.append(f"  [{ev['event']}] {ev['content']}")

        # Relevant knowledge
        knowledge = context.get("relevant_knowledge", [])
        if knowledge:
            parts.append("\n=== Relevant Knowledge ===")
            for k in knowledge:
                parts.append(f"  {k['text']} (relevance: {k['score']:.0%})")

        # Entity context
        for key, value in context.items():
            if key.startswith("entity_"):
                eid = key.replace("entity_", "")
                parts.append(f"\n=== Entity: {eid} ===")
                for fact in value.get("facts", []):
                    parts.append(f"  FACT: {fact['content']} [{fact['confidence']:.0%}]")
                for belief in value.get("beliefs", []):
                    parts.append(f"  BELIEF: {belief['content']} [{belief['confidence']:.0%}]")

        # Guest/property history
        for key, value in context.items():
            if key.startswith("guest_history_") or key.startswith("property_history_"):
                parts.append(f"\n=== {key.replace('_', ' ').title()} ===")
                parts.append(f"  {value}")

        # Active processes (HIGH PRIORITY — what is happening NOW)
        active = context.get("active_processes", [])
        if active:
            parts.append("\n=== Active Processes (Current) ===")
            for proc in active:
                parts.append(
                    f"  [{proc['type']}] {proc['process_id']} — "
                    f"status: {proc['status']}, "
                    f"deadline: {proc.get('deadline', 'none')}"
                )

        # SOP instructions (behavioral directives, not just info)
        sop = context.get("sop_instructions", [])
        if sop:
            parts.append("\n=== SOP Instructions (Follow These) ===")
            for s in sop:
                parts.append(f"  {s['text']} (relevance: {s['score']:.0%})")

        # Azure Search results (Cendra external KB)
        azure = context.get("azure_search_results", [])
        if azure:
            parts.append("\n=== Property Knowledge (Azure Search) ===")
            for r in azure:
                parts.append(
                    f"  {r['text']} (source: {r.get('source', '?')}, "
                    f"score: {r.get('score', 0):.2f})"
                )

        # Applicable procedures
        procedures = context.get("applicable_procedures", [])
        if procedures:
            parts.append("\n=== Recommended Procedures ===")
            for p in procedures:
                parts.append(f"  {p['name']}: {p['description']}")
                for action in p["actions"]:
                    parts.append(f"    → {action}")

        return "\n".join(parts)
