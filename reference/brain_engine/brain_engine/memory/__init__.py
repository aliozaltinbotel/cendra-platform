"""Memory subsystem — Multi-tier cognitive memory architecture.

Implements concepts from cutting-edge research:
- Mem0: Production-ready memory layer for AI agents
- A-MEM: Zettelkasten-style linked memory with atomic notes + keyword graph
- Zep: Temporal knowledge graphs with bi-temporal modeling
- MemGPT/Letta: Virtual context management with tiered memory
- Titans: Surprise-based memorization (high-surprise events get priority)
- Hindsight: Facts vs beliefs, confidence scores, opinion evolution
- CoALA: Cognitive Architectures for Language Agents (perception-memory-reasoning-action)
- Ebbinghaus: Forgetting curve with reinforcement-based retention
- MemSearcher: Autonomous memory management decisions

Architecture:
    ┌─────────────────────────────────────────────────────┐
    │            Cognitive Controller (CoALA)              │
    │  Perception → Memory → Reasoning → Action cycle     │
    │  Dual Process: System 1 (fast) / System 2 (slow)    │
    ├─────────────────────────────────────────────────────┤
    │  Metacognitive Monitor                              │
    │  (self-monitoring, self-correction, epistemic       │
    │   awareness, reasoning traces, performance)         │
    ├─────────────────────────────────────────────────────┤
    │  Working Memory     │  Procedural Memory            │
    │  (session scratchpad│  (learned behaviors/rules)    │
    ├─────────────────────┤                               │
    │  Episodic Memory    │  Surprise Detector (Titans)   │
    │  (event history)    │  (prioritizes unusual events) │
    ├─────────────────────┤                               │
    │  Semantic Memory    │  Memory Consolidator          │
    │  (Qdrant vectors)   │  (tier migration + decay)     │
    ├─────────────────────┤                               │
    │  Knowledge Graph    │  Guest History Store          │
    │  (Zep temporal KG)  │  (persistent guest/property)  │
    └─────────────────────────────────────────────────────┘
"""

# Core memory tiers
from brain_engine.memory.working_memory import WorkingMemory
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.semantic_memory import SemanticMemory
from brain_engine.memory.context_compressor import ContextCompressor

# Advanced memory systems (research implementations)
from brain_engine.memory.knowledge_graph import TemporalKnowledgeGraph, KnowledgeType, KnowledgeNode
from brain_engine.memory.surprise_detector import SurpriseDetector, SurpriseScore
from brain_engine.memory.memory_consolidator import MemoryConsolidator
from brain_engine.memory.procedural_memory import ProceduralMemory
from brain_engine.memory.cognitive_controller import CognitiveController
from brain_engine.memory.metacognition import MetacognitiveMonitor

# Persistence layer
from brain_engine.memory.guest_history import GuestHistoryStore, GuestProfile, BookingRecord, IncidentRecord
from brain_engine.memory.event_recorder import EventRecorder

__all__ = [
    # Core tiers
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "ContextCompressor",
    # Research implementations
    "TemporalKnowledgeGraph",
    "KnowledgeType",
    "KnowledgeNode",
    "SurpriseDetector",
    "SurpriseScore",
    "MemoryConsolidator",
    "ProceduralMemory",
    "CognitiveController",
    "MetacognitiveMonitor",
    # Persistence
    "GuestHistoryStore",
    "GuestProfile",
    "BookingRecord",
    "IncidentRecord",
    "EventRecorder",
]
