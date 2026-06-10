"""Property-timeline narrative subsystem.

Public package exports grow across the four commits that close Gap #2.
This first commit ships the value objects, adapters, and composer; the
text/LLM/voice renderers and :class:`NarrativeService` are added in
later commits.
"""

from __future__ import annotations

from brain_engine.narrative.composition import TimelineComposer
from brain_engine.narrative.errors import (
    NarrativeCompositionError,
    NarrativeError,
    TimelineSourceError,
    VoiceSynthesisUnavailable,
)
from brain_engine.narrative.models import (
    EventKind,
    Narrative,
    RenderStyle,
    TimelineEvent,
    TimelineRange,
)
from brain_engine.narrative.ownership import (
    OwnershipLookupStore,
    PropertyOwnershipResolver,
)
from brain_engine.narrative.sources import (
    CustomerMemoryTimelineSource,
    DecisionCaseTimelineSource,
    GuestHistoryTimelineSource,
    TimelineSource,
)
from brain_engine.narrative.unified_sources import (
    UnifiedReservationsTimelineSource,
)
from brain_engine.narrative.llm_renderer import LLMNarrativeRenderer
from brain_engine.narrative.service import NarrativeService
from brain_engine.narrative.text_renderer import TextRenderer
from brain_engine.narrative.voice_renderer import VoiceRenderer

__all__ = [
    "CustomerMemoryTimelineSource",
    "DecisionCaseTimelineSource",
    "EventKind",
    "GuestHistoryTimelineSource",
    "LLMNarrativeRenderer",
    "Narrative",
    "NarrativeCompositionError",
    "NarrativeError",
    "NarrativeService",
    "OwnershipLookupStore",
    "PropertyOwnershipResolver",
    "RenderStyle",
    "TextRenderer",
    "TimelineComposer",
    "TimelineEvent",
    "TimelineRange",
    "TimelineSource",
    "TimelineSourceError",
    "UnifiedReservationsTimelineSource",
    "VoiceRenderer",
    "VoiceSynthesisUnavailable",
]
