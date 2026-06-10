"""The surface-agnostic temporal analysis core (Phase 3).

Given a client's fused :class:`TemporalContext` (Phase 2) and a question,
:class:`TemporalAnalyzer` renders the context deterministically, asks an
LLM for a structured answer, and returns a :class:`TemporalAnalysisResult`.
This is the first place an LLM enters the temporal path — Phases 1 and 2
are deterministic; reasoning happens only here, at read time.

The core is **surface-agnostic**: it takes an injected chat model and a
question, knowing nothing about PM-chat or HTTP.  Thin surfaces (an API
endpoint, the PM chat) wire their own model and call :meth:`analyze`.

Graceful degradation: with no model, or on any model / parse failure, the
core returns a result with ``analysis=None`` and an explanatory ``note``
instead of raising — an optional reasoning layer must never break the
caller (mirrors the narrative renderer).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog

from brain_engine.temporal_analysis.context_format import format_context
from brain_engine.temporal_analysis.models import (
    TemporalAnalysis,
    TemporalAnalysisResult,
)

if TYPE_CHECKING:
    from brain_engine.memory.temporal_context import TemporalContext
    from brain_engine.models.base import BaseChatModel

__all__ = ["TemporalAnalyzer"]


logger = structlog.get_logger(__name__)


_DEFAULT_SYSTEM_PROMPT: Final[str] = (
    "You are a property-management analyst. You receive one client's "
    "temporal context: their HISTORY (what was recorded over time), "
    "what is LIVE NOW, and what is UPCOMING. Answer the question using "
    "only this context. Rules:\n"
    "1. Ground every statement in the context; never invent facts, "
    "dates, amounts, or names.\n"
    "2. Clearly distinguish the past from what is live now and what is "
    "upcoming.\n"
    "3. If the context does not contain the answer, say so plainly "
    "rather than guessing.\n"
    "4. Be concise and specific; cite concrete dates and events."
)


class TemporalAnalyzer:
    """Reason over a fused temporal context with an injected LLM."""

    def __init__(
        self,
        chat_model: BaseChatModel | None = None,
        *,
        system_prompt: str | None = None,
    ) -> None:
        self._model = chat_model
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def analyze(
        self,
        context: TemporalContext,
        question: str,
    ) -> TemporalAnalysisResult:
        """Answer ``question`` over ``context``; degrade, never raise."""
        entry_count = len(context.history)
        model = self._model
        if model is None:
            logger.info(
                "temporal_analysis.no_model",
                entry_count=entry_count,
            )
            return self._degraded(
                context,
                question,
                entry_count,
                note="no chat model configured",
            )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": _build_user_prompt(question, context),
            },
        ]
        try:
            result = await model.invoke_structured(
                messages,
                TemporalAnalysis,
            )
        except Exception as exc:  # graceful degradation - never raise
            logger.warning(
                "temporal_analysis.failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return self._degraded(
                context,
                question,
                entry_count,
                note=f"analysis failed: {exc}",
            )

        if not isinstance(result, TemporalAnalysis):
            logger.warning(
                "temporal_analysis.unexpected_output",
                got=type(result).__name__,
            )
            return self._degraded(
                context,
                question,
                entry_count,
                note="unexpected model output type",
            )

        logger.info(
            "temporal_analysis.completed",
            entry_count=entry_count,
            confidence=result.confidence,
            findings=len(result.key_findings),
        )
        return TemporalAnalysisResult(
            question=question,
            as_of=context.as_of,
            scope=context.scope,
            analysis=result,
            llm_used=True,
            context_entry_count=entry_count,
        )

    @staticmethod
    def _degraded(
        context: TemporalContext,
        question: str,
        entry_count: int,
        *,
        note: str,
    ) -> TemporalAnalysisResult:
        """Build a no-analysis result carrying the degradation reason."""
        return TemporalAnalysisResult(
            question=question,
            as_of=context.as_of,
            scope=context.scope,
            analysis=None,
            llm_used=False,
            context_entry_count=entry_count,
            note=note,
        )


def _build_user_prompt(question: str, context: TemporalContext) -> str:
    """Embed the rendered context and the question in the user turn."""
    return f"{format_context(context)}\n\nQUESTION: {question}"
