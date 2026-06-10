"""PM-chat surface over the temporal analysis core (Phase 3, PR3c).

The second surface for :class:`TemporalAnalyzer` (the first being the HTTP
endpoint).  Where the endpoint returns structured JSON, PM chat needs a
human-readable reply: this module orchestrates the same core for a property
manager's free-text question about one client and shapes the result into a
chat message.

Surface-agnostic from the transport: it takes an injected analyzer + a
timeline, knows nothing about SSE / AG-UI.  A thin wiring layer (PR3c.1)
decides *when* a PM message is a temporal question (routing) and emits the
reply as a streaming event; this module only turns *(question, scope)* into
a reply.

Reply text stays language-neutral — the analyzer's ``answer`` is already in
the property manager's language, so this adds only its bulleted findings and
never injects a hard-coded label.  Graceful: a degraded analysis (no model /
failure) yields ``None`` rather than a broken reply, so the caller simply
emits nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from brain_engine.memory.temporal_fusion import build_temporal_context

if TYPE_CHECKING:
    from datetime import datetime

    from brain_engine.memory.memory_timeline import (
        MemoryTimeline,
        TimelineScope,
    )
    from brain_engine.temporal_analysis import (
        TemporalAnalysisResult,
        TemporalAnalyzer,
    )

__all__ = [
    "TemporalPmReply",
    "format_pm_reply",
    "respond",
]


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TemporalPmReply:
    """A PM-chat reply plus the structured result behind it.

    Attributes:
        text: The chat message to show the property manager.
        result: The full :class:`TemporalAnalysisResult` (so a caller can
            also surface confidence / entry count / scope in the UI).
    """

    text: str
    result: TemporalAnalysisResult


async def respond(
    message: str,
    scope: TimelineScope,
    *,
    analyzer: TemporalAnalyzer,
    timeline: MemoryTimeline,
    as_of: datetime | None = None,
    limit: int | None = None,
) -> TemporalPmReply | None:
    """Answer a PM's temporal question as a chat reply.

    Builds the client's fused context, runs the analyzer, and shapes the
    answer into chat text.  Returns ``None`` when the analysis degraded
    (no model / failure) so the caller emits nothing.

    Args:
        message: The property manager's question.
        scope: Who the question is about (property / guest / customer).
        analyzer: The injected temporal analyzer.
        timeline: The injected memory timeline to read the past from.
        as_of: Anchor instant; ``None`` means now.
        limit: Cap on the most-recent history entries fed in.
    """
    context = await build_temporal_context(
        timeline,
        scope,
        as_of=as_of,
        limit=limit,
    )
    result = await analyzer.analyze(context, message)
    if result.analysis is None:
        logger.info(
            "temporal_pm.no_reply",
            note=result.note,
            entry_count=result.context_entry_count,
        )
        return None

    reply = format_pm_reply(result)
    logger.info(
        "temporal_pm.reply",
        entry_count=result.context_entry_count,
        chars=len(reply),
    )
    return TemporalPmReply(text=reply, result=result)


def format_pm_reply(result: TemporalAnalysisResult) -> str:
    """Shape a result into a chat message: answer, then bulleted findings.

    Returns an empty string when there is no analysis (the caller should
    not reach here in that case; see :func:`respond`).
    """
    analysis = result.analysis
    if analysis is None:
        return ""
    lines = [analysis.answer.strip()]
    findings = [item.strip() for item in analysis.key_findings if item.strip()]
    if findings:
        lines.append("")
        lines.extend(f"- {item}" for item in findings)
    return "\n".join(lines)
