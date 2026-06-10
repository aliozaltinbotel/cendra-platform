"""Memory prompts + pattern gestures (GAP K).

Public surface:

- :class:`MemoryPrompt`, :class:`MemoryPromptKind`, :class:`MemorySource`
- :class:`PatternGesture`, :class:`GestureMode`
- :class:`GestureContext`, :class:`GesturePack`
- :class:`MemoryPromptExtractor`, :class:`MemoryPromptAggregator`
- :class:`PatternGestureBuilder`
- :class:`GestureService`
"""

from __future__ import annotations

from brain_engine.gestures.extractors import (
    CustomerMemoryExtractor,
    CustomerMemoryPort,
    FactsExtractor,
    FactsPort,
    GuestHistoryExtractor,
    GuestHistoryPort,
)
from brain_engine.gestures.gestures import PatternGestureBuilder
from brain_engine.gestures.models import (
    GestureContext,
    GestureMode,
    GesturePack,
    MemoryPrompt,
    MemoryPromptKind,
    MemorySource,
    PatternGesture,
)
from brain_engine.gestures.prompts import (
    MemoryPromptAggregator,
    MemoryPromptExtractor,
)
from brain_engine.gestures.service import GestureService

__all__ = [
    "CustomerMemoryExtractor",
    "CustomerMemoryPort",
    "FactsExtractor",
    "FactsPort",
    "GestureContext",
    "GuestHistoryExtractor",
    "GuestHistoryPort",
    "GestureMode",
    "GesturePack",
    "GestureService",
    "MemoryPrompt",
    "MemoryPromptAggregator",
    "MemoryPromptExtractor",
    "MemoryPromptKind",
    "MemorySource",
    "PatternGesture",
    "PatternGestureBuilder",
]
