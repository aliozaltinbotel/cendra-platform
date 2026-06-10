"""Value objects for the temporal analysis core (Phase 3).

The LLM's structured answer (:class:`TemporalAnalysis`) and the core's
returned envelope (:class:`TemporalAnalysisResult`) that wraps it with
provenance — the anchor, the scope, how many timeline entries fed the
analysis, and whether the LLM actually ran (so a degraded call is never
mistaken for a real answer).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from datetime import datetime

    from brain_engine.memory.memory_timeline import TimelineScope

__all__ = [
    "TemporalAnalysis",
    "TemporalAnalysisResult",
]


class TemporalAnalysis(BaseModel):
    """The structured answer the LLM returns for a temporal question.

    This is the ``output_schema`` handed to
    :meth:`~brain_engine.models.base.BaseChatModel.invoke_structured`, so
    its field descriptions double as the contract shown to the model.
    """

    answer: str = Field(
        description=(
            "Direct answer to the question, grounded only in the "
            "provided client context."
        ),
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description=(
            "Salient, context-grounded observations that support the "
            "answer; empty when the context offers none."
        ),
    )
    confidence: float = Field(
        default=0.0,
        description=(
            "Self-rated confidence in [0, 1] given the available "
            "context (lower when the context is thin or ambiguous)."
        ),
    )


@dataclass(frozen=True, slots=True)
class TemporalAnalysisResult:
    """The core's envelope around one analysis.

    Attributes:
        question: The question that was asked.
        as_of: The anchor instant of the analysed context (aware UTC).
        scope: Who the analysis is about.
        analysis: The LLM's structured answer, or ``None`` when no model
            was configured or the call failed (see ``note``).
        llm_used: ``True`` only when an LLM produced ``analysis`` — a
            degraded result is never mistaken for a real one.
        context_entry_count: How many timeline entries fed the analysis
            (provenance for how grounded the answer could be).
        note: Human-readable reason when ``analysis`` is ``None``.
    """

    question: str
    as_of: datetime
    scope: TimelineScope
    analysis: TemporalAnalysis | None
    llm_used: bool
    context_entry_count: int
    note: str = ""
