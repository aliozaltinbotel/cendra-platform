"""Developer-facing debug surface.

Reference: ``brain_engine_advisory.md`` §10.1.

The replay engine is the only public symbol today; expansion will
land here as the §10 medium-priority items ship.
"""

from brain_engine.debug.replay_engine import (
    ConversationReplayEngine,
    InMemoryReplayEngine,
    ReplayBreakpoint,
    ReplayResult,
    ReplaySnapshot,
    ReplayTrace,
    StateModifier,
)

__all__ = [
    "ConversationReplayEngine",
    "InMemoryReplayEngine",
    "ReplayBreakpoint",
    "ReplayResult",
    "ReplaySnapshot",
    "ReplayTrace",
    "StateModifier",
]
