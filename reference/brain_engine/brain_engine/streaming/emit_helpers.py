"""Helpers that emit AGUI observability events via the ContextVar emitter.

All helpers are no-ops when no emitter is bound. Emitter exceptions are caught
and logged at WARN — telemetry failure never breaks the pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from brain_engine.streaming.current_emitter import get_current_emitter
from brain_engine.streaming.event_types import EventType

logger = logging.getLogger(__name__)

_EXCERPT_MAX = 400


def _truncate_hits(hits: list[dict]) -> list[dict]:
    out = []
    for h in hits:
        c = dict(h)
        excerpt = c.get("excerpt")
        if isinstance(excerpt, str) and len(excerpt) > _EXCERPT_MAX:
            c["excerpt"] = excerpt[:_EXCERPT_MAX]
        out.append(c)
    return out


def emit_intent_classified(
    intent: str, confidence: float, raw_label: Optional[str] = None
) -> None:
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.intent_classified(intent, confidence, raw_label=raw_label)
    except Exception:
        logger.warning("emit_intent_classified failed", exc_info=True)


def emit_memory_retrieved(
    tier: str, query: str, hits: list[dict], latency_ms: float
) -> None:
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.memory_retrieved(
            tier=tier,
            query=query,
            hits=_truncate_hits(hits),
            latency_ms=latency_ms,
        )
    except Exception:
        logger.warning("emit_memory_retrieved failed", exc_info=True)


def emit_rag_hit(
    query: str, source: str, docs: list[dict], latency_ms: float
) -> None:
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.rag_hit(
            query=query,
            source=source,
            docs=_truncate_hits(docs),
            latency_ms=latency_ms,
        )
    except Exception:
        logger.warning("emit_rag_hit failed", exc_info=True)


def emit_guardrail_check(
    check_name: str,
    decision: str,
    reason: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.guardrail_check(
            check_name=check_name,
            decision=decision,
            reason=reason,
            details=details,
        )
    except Exception:
        logger.warning("emit_guardrail_check failed", exc_info=True)


def emit_cognitive_mode_changed(
    from_mode: str, to_mode: str, trigger: str, reasoning: str
) -> None:
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.cognitive_mode_changed(
            from_mode=from_mode,
            to_mode=to_mode,
            trigger=trigger,
            reasoning=reasoning,
        )
    except Exception:
        logger.warning("emit_cognitive_mode_changed failed", exc_info=True)


def emit_missing_info_detected(
    *,
    question: str,
    missing_information: str,
    source_field: str,
) -> None:
    """Emit a MISSING_INFO_DETECTED SSE event.

    Args:
        question: User-facing question to ask the PM.
        missing_information: Raw missing-info text from the extractor.
        source_field: Where the gap was detected (extractor or sufficiency).
    """
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.emit(
            EventType.MISSING_INFO_DETECTED,
            {
                "question": question,
                "missing_information": missing_information,
                "source_field": source_field,
            },
        )
    except Exception:
        logger.warning("emit_missing_info_detected failed", exc_info=True)


def emit_temporal_analysis(
    *,
    text: str,
    answer: str,
    key_findings: list[str],
    confidence: float,
    context_entry_count: int,
    as_of: str,
    scope: dict[str, str],
) -> None:
    """Emit a TEMPORAL_ANALYSIS SSE event (Phase 3 PM-chat insight).

    Args:
        text: The PM-facing chat reply (answer + bulleted findings).
        answer: The analysis answer on its own.
        key_findings: Supporting observations.
        confidence: Analyzer self-rated confidence in ``[0, 1]``.
        context_entry_count: Timeline entries that fed the analysis.
        as_of: The anchor instant (ISO-8601) the context was built at.
        scope: Non-empty client identifiers the analysis is about.
    """
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.emit(
            EventType.TEMPORAL_ANALYSIS,
            {
                "text": text,
                "answer": answer,
                "key_findings": key_findings,
                "confidence": confidence,
                "context_entry_count": context_entry_count,
                "as_of": as_of,
                "scope": scope,
            },
        )
    except Exception:
        logger.warning("emit_temporal_analysis failed", exc_info=True)


def emit_stage_mismatch_detected(
    *,
    detail: str,
    scenario_id: str,
    calendar_stage: str,
    scenario_stage: str,
) -> None:
    """Emit a STAGE_MISMATCH_DETECTED SSE event.

    Surfaces the FL-16 Q5-C detection in PM Chat so the operator
    sees that Brain noticed the calendar/message stage
    contradiction (Mümin's 2026-05-18 adversarial test scenario).
    Variant A: observation only — Brain still produces a guest
    response.  This event is the visibility channel.

    Args:
        detail: Stable-format detail string
            (``"calendar=<stage> scenario=<stage>"``) as produced
            by :func:`detect_stage_mismatch`.  Pinned format so
            the PM Chat UI can pattern-match on it.
        scenario_id: Foundation slug of the matched scenario
            (e.g. ``"s3_103_guest_asks_for_wifi_password_before"``).
        calendar_stage: BookingStage value derived from the
            event's calendar snapshot (e.g. ``"post_checkout"``).
        scenario_stage: BookingStage value the catalog scenario
            expects (e.g. ``"pre_arrival"``).
    """
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.emit(
            EventType.STAGE_MISMATCH_DETECTED,
            {
                "detail": detail,
                "scenario_id": scenario_id,
                "calendar_stage": calendar_stage,
                "scenario_stage": scenario_stage,
            },
        )
    except Exception:
        logger.warning(
            "emit_stage_mismatch_detected failed",
            exc_info=True,
        )


def emit_learning_decision(
    *,
    surprise_score: float,
    should_memorize: bool,
    memory_strength: float,
    fact_type: str,
    decision: str,
) -> None:
    """Emit a LEARNING_DECISION SSE event.

    Args:
        surprise_score: 0.0-1.0 surprise score from SurpriseDetector.
        should_memorize: Brain's autonomous decision.
        memory_strength: Initial memory strength after surprise weighting.
        fact_type: One of preference|rule|info|incident.
        decision: Human-readable: "stored_to_semantic" | "ephemeral_only" | etc.
    """
    emitter = get_current_emitter()
    if emitter is None:
        return
    try:
        emitter.emit(
            EventType.LEARNING_DECISION,
            {
                "surprise_score": surprise_score,
                "should_memorize": should_memorize,
                "memory_strength": memory_strength,
                "fact_type": fact_type,
                "decision": decision,
            },
        )
    except Exception:
        logger.warning("emit_learning_decision failed", exc_info=True)
