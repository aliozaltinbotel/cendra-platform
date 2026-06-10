"""Foundation Analysis Orchestrator (FL-16).

The :class:`brain_engine.analysis.orchestrator.FoundationAnalysisOrchestrator`
is the single entry point for running an incoming operational event
through the Foundation Layer pipeline:

    classify  →  match_foundation  →  guardrail_stub  →  mine_stub  →
    route_stub  →  log_origin

The orchestrator is *contract-defining* in Sprint 2 — its real value
comes when Sprints 3-5 fill the stub steps with the safety gates
(FL-05), memory-routing logic (FL-04), confidence weights (FL-06),
and source-reliability ranking (FL-07).  Wiring this in early lets
each downstream PR slot a single concrete step into the pipeline
without touching the orchestrator's signature.

Public surface:

* :class:`~brain_engine.analysis.models.AnalysisEvent` — typed
  input describing one upstream event (message, reservation
  change, PMS event, vendor update, review, task event, payment
  event).
* :class:`~brain_engine.analysis.models.AnalysisResult` — full
  pipeline output including the :class:`PatternOrigin` trail that
  the caller attaches to a :class:`DecisionCase` or
  :class:`PatternRule`.
* :class:`~brain_engine.analysis.orchestrator.FoundationAnalysisOrchestrator`
  — the pipeline driver itself.
"""

from __future__ import annotations

from brain_engine.analysis.iterative_questioning import (
    DEFAULT_QUESTIONS_PER_SCENARIO,
    DEFAULT_TOP_K,
    IterativeQuestion,
    build_clarifying_questions,
    render_question_prompt,
)
from brain_engine.analysis.models import (
    AnalysisEvent,
    AnalysisEventType,
    AnalysisResult,
    FoundationMatch,
    FoundationMatchCandidate,
    MemoryTier,
    memory_type_label_to_tier,
)
from brain_engine.analysis.orchestrator import (
    FoundationAnalysisOrchestrator,
)

__all__ = [
    "DEFAULT_QUESTIONS_PER_SCENARIO",
    "DEFAULT_TOP_K",
    "AnalysisEvent",
    "AnalysisEventType",
    "AnalysisResult",
    "FoundationAnalysisOrchestrator",
    "FoundationMatch",
    "FoundationMatchCandidate",
    "IterativeQuestion",
    "MemoryTier",
    "build_clarifying_questions",
    "memory_type_label_to_tier",
    "render_question_prompt",
]
