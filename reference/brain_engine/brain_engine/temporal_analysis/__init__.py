"""Temporal analysis core (Phase 3).

A surface-agnostic LLM reasoning layer over the fused past+present
:class:`~brain_engine.memory.temporal_context.TemporalContext` (Phase 2).
:class:`TemporalAnalyzer` answers a question about one client grounded in
their history, live operations, and upcoming operations; thin surfaces
(API endpoint, PM chat) wire a model and call it.
"""

from __future__ import annotations

from brain_engine.temporal_analysis.analyzer import TemporalAnalyzer
from brain_engine.temporal_analysis.context_format import format_context
from brain_engine.temporal_analysis.models import (
    TemporalAnalysis,
    TemporalAnalysisResult,
)

__all__ = [
    "TemporalAnalysis",
    "TemporalAnalysisResult",
    "TemporalAnalyzer",
    "format_context",
]
