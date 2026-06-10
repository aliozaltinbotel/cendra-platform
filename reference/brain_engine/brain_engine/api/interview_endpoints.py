"""Interview Endpoints — proactive PM Q&A surface for the V2 UI.

Exposes the four core operations of :class:`InterviewEngine` over HTTP:

1. ``POST /api/v1/interview/next-question`` — open-ended pull (optional
   stage filter).  Used by the onboarding card in the mobile UI.
2. ``POST /api/v1/interview/next-question-for-event`` — event-triggered
   pull.  The dispatcher calls this when an operation has just hit a
   case the PM has not documented yet, so the question can surface in
   the same chat thread as the action.
3. ``POST /api/v1/interview/answer`` — record a PM answer (text or
   voice transcript).  Idempotent: re-answering replaces the prior row.
4. ``GET /api/v1/interview/coverage/{property_id}`` — per-stage and
   overall progress so the Trust Meter strip can render coverage bars.

The router uses a tiny dependency dict populated by ``server.py`` at
lifespan start; this mirrors the pattern already in place for the
workflow and Cendra adapter routers.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from brain_engine.interview import (
    AnswerSource,
    BookingStage,
    InterviewEngine,
    VoiceTranscriber,
    VoiceTranscriptionError,
)


__all__ = [
    "configure_interview_deps",
    "router",
]


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/interview", tags=["Interview"])


# Shared deps — injected from server.py at lifespan start.
_deps: dict[str, Any] = {}


def configure_interview_deps(deps: dict[str, Any]) -> None:
    """Inject shared dependencies.

    Args:
        deps: Dependency dict from server startup.  Must contain the
            key ``"interview_engine"`` mapped to a live
            :class:`InterviewEngine`.
    """
    _deps.update(deps)


def _engine() -> InterviewEngine:
    """Return the configured :class:`InterviewEngine` or 503."""
    engine = _deps.get("interview_engine")
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="InterviewEngine not configured",
        )
    return engine


def _transcriber() -> VoiceTranscriber:
    """Return the configured :class:`VoiceTranscriber` or 503."""
    transcriber = _deps.get("voice_transcriber")
    if transcriber is None:
        raise HTTPException(
            status_code=503,
            detail="VoiceTranscriber not configured",
        )
    return transcriber


# ── Request / Response models ─────────────────────────────────── #


class NextQuestionRequest(BaseModel):
    """Open-ended next-question pull.

    Attributes:
        property_id: Property whose answers gate selection.
        stage: Optional booking stage filter.  When omitted, the
            highest-priority eligible question across all stages wins.
    """

    property_id: str
    stage: BookingStage | None = None


class EventQuestionRequest(BaseModel):
    """Event-triggered next-question pull.

    Attributes:
        property_id: Property whose answers gate selection.
        event_type: Live operational event (e.g. ``"orphan_night"``)
            whose handling depends on PM policy.
    """

    property_id: str
    event_type: str = Field(min_length=1)


class RecordAnswerRequest(BaseModel):
    """PM answer payload.

    Attributes:
        property_id: Property the answer applies to.
        qid: Catalog question identifier.
        answer_text: Free-form PM response.  Whitespace trimmed.
        source: How the answer reached us — text input, voice
            transcript, or behaviour-inferred.
        answered_by: Identifier of who answered (default ``"pm"``).
    """

    property_id: str
    qid: str
    answer_text: str
    source: AnswerSource = AnswerSource.TEXT
    answered_by: str = "pm"


class QuestionResponse(BaseModel):
    """Wire form of :class:`InterviewQuestion` for the UI.

    Attributes:
        qid: Catalog identifier.
        stage: Booking lifecycle stage.
        topic: Coarse subject for grouping related answers.
        prompt_text: PM-facing question text.
        priority: Selection ordering hint.
        depends_on_qids: Other ``qid`` values that must be answered
            first before this question becomes eligible.
        triggered_by_events: Event types that can pull this question
            to the top of the queue.
    """

    qid: str
    stage: BookingStage
    topic: str
    prompt_text: str
    priority: str
    depends_on_qids: tuple[str, ...]
    triggered_by_events: tuple[str, ...]


class AnswerResponse(BaseModel):
    """Wire form of :class:`InterviewAnswer`."""

    property_id: str
    qid: str
    answer_text: str
    source: AnswerSource
    answered_by: str
    answered_at: str


class VoiceAnswerResponse(BaseModel):
    """Wire form of a voice-captured answer.

    Carries the persisted :class:`InterviewAnswer` fields plus
    transcription metadata so the UI can render the detected
    language and duration next to the transcript.
    """

    property_id: str
    qid: str
    answer_text: str
    source: AnswerSource
    answered_by: str
    answered_at: str
    detected_language: str = ""
    duration_seconds: float = 0.0


class CoverageResponse(BaseModel):
    """Wire form of :class:`InterviewCoverage`.

    Per-stage entries map the stage value to ``(answered, total)``.
    """

    property_id: str
    answered_total: int
    question_total: int
    overall_ratio: float
    per_stage: dict[str, tuple[int, int]]
    stage_ratios: dict[str, float]


# ── Endpoints ─────────────────────────────────────────────────── #


@router.post("/next-question", response_model=QuestionResponse | None)
async def next_question(
    payload: NextQuestionRequest,
) -> QuestionResponse | None:
    """Return the highest-priority unanswered question, or ``None``."""
    engine = _engine()
    question = await engine.next_question(
        property_id=payload.property_id,
        stage=payload.stage,
    )
    if question is None:
        return None
    return QuestionResponse(
        qid=question.qid,
        stage=question.stage,
        topic=question.topic,
        prompt_text=question.prompt_text,
        priority=question.priority.value,
        depends_on_qids=question.depends_on_qids,
        triggered_by_events=question.triggered_by_events,
    )


@router.post(
    "/next-question-for-event",
    response_model=QuestionResponse | None,
)
async def next_question_for_event(
    payload: EventQuestionRequest,
) -> QuestionResponse | None:
    """Return the highest-priority question triggered by ``event_type``."""
    engine = _engine()
    question = await engine.next_question_for_event(
        property_id=payload.property_id,
        event_type=payload.event_type,
    )
    if question is None:
        return None
    return QuestionResponse(
        qid=question.qid,
        stage=question.stage,
        topic=question.topic,
        prompt_text=question.prompt_text,
        priority=question.priority.value,
        depends_on_qids=question.depends_on_qids,
        triggered_by_events=question.triggered_by_events,
    )


@router.post("/answer", response_model=AnswerResponse)
async def record_answer(payload: RecordAnswerRequest) -> AnswerResponse:
    """Persist a PM answer and return the stored record."""
    engine = _engine()
    try:
        answer = await engine.record_answer(
            property_id=payload.property_id,
            qid=payload.qid,
            answer_text=payload.answer_text,
            source=payload.source,
            answered_by=payload.answered_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnswerResponse(
        property_id=answer.property_id,
        qid=answer.qid,
        answer_text=answer.answer_text,
        source=answer.source,
        answered_by=answer.answered_by,
        answered_at=answer.answered_at.isoformat(),
    )


@router.post("/answer-voice", response_model=VoiceAnswerResponse)
async def record_voice_answer(
    property_id: str = Form(...),
    qid: str = Form(...),
    answered_by: str = Form(default="pm"),
    audio: UploadFile = File(...),
) -> VoiceAnswerResponse:
    """Transcribe a voice memo and persist the resulting answer.

    The UI uploads an audio file (``audio/*``) alongside the
    property + qid.  The configured :class:`VoiceTranscriber` turns
    it into text and the :class:`InterviewEngine` persists it with
    :attr:`AnswerSource.VOICE`.  Transcription failures surface as
    502 so the UI can retry; empty / invalid input maps to 400.
    """
    engine = _engine()
    transcriber = _transcriber()
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="audio file is empty")
    filename = audio.filename or "voice-memo"
    content_type = audio.content_type or "application/octet-stream"
    try:
        transcript = await transcriber.transcribe(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
        )
    except VoiceTranscriptionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        answer = await engine.record_answer(
            property_id=property_id,
            qid=qid,
            answer_text=transcript.text,
            source=AnswerSource.VOICE,
            answered_by=answered_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return VoiceAnswerResponse(
        property_id=answer.property_id,
        qid=answer.qid,
        answer_text=answer.answer_text,
        source=answer.source,
        answered_by=answer.answered_by,
        answered_at=answer.answered_at.isoformat(),
        detected_language=transcript.language,
        duration_seconds=transcript.duration_seconds,
    )


@router.get(
    "/coverage/{property_id}",
    response_model=CoverageResponse,
)
async def coverage(property_id: str) -> CoverageResponse:
    """Return per-stage and overall answer coverage for a property."""
    engine = _engine()
    snapshot = await engine.coverage(property_id=property_id)
    per_stage = {
        stage.value: counts for stage, counts in snapshot.per_stage.items()
    }
    stage_ratios = {
        stage.value: snapshot.stage_ratio(stage)
        for stage in snapshot.per_stage
    }
    return CoverageResponse(
        property_id=snapshot.property_id,
        answered_total=snapshot.answered_total,
        question_total=snapshot.question_total,
        overall_ratio=snapshot.overall_ratio,
        per_stage=per_stage,
        stage_ratios=stage_ratios,
    )
