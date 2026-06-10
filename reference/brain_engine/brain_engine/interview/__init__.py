"""Proactive PM-interview engine.

Cendra's onboarding is a never-ending Q&A loop: the engine selects the
next high-priority question to ask the property manager, captures the
answer (text or voice transcript), and reports per-stage coverage so
the UI can show progress without ever forcing a settings form.

Public surface:

- :class:`InterviewQuestion` / :class:`InterviewAnswer` /
  :class:`InterviewCoverage` — wire records.
- :class:`BookingStage` / :class:`QuestionPriority` /
  :class:`AnswerSource` — enums.
- :class:`InterviewEngine` — orchestration.
- :class:`InterviewAnswerStore` Protocol +
  :class:`InMemoryInterviewAnswerStore` /
  :class:`PgInterviewAnswerStore` — persistence.
- :data:`DEFAULT_CATALOG` — the canonical question set sourced from
  the CEO V2 directive booking-stage checklist.
"""

from __future__ import annotations

from brain_engine.interview.catalog import DEFAULT_CATALOG
from brain_engine.interview.engine import InterviewEngine
from brain_engine.interview.models import (
    AnswerSource,
    BookingStage,
    InterviewAnswer,
    InterviewCoverage,
    InterviewQuestion,
    QuestionPriority,
    priority_rank,
)
from brain_engine.interview.postgres_store import (
    PgInterviewAnswerStore,
    create_interview_pool,
)
from brain_engine.interview.store import (
    InMemoryInterviewAnswerStore,
    InterviewAnswerStore,
)
from brain_engine.interview.voice import (
    AzureWhisperTranscriber,
    VoiceTranscriber,
    VoiceTranscript,
    VoiceTranscriptionError,
)

__all__ = [
    "DEFAULT_CATALOG",
    "AnswerSource",
    "BookingStage",
    "InMemoryInterviewAnswerStore",
    "InterviewAnswer",
    "InterviewAnswerStore",
    "InterviewCoverage",
    "InterviewEngine",
    "InterviewQuestion",
    "AzureWhisperTranscriber",
    "PgInterviewAnswerStore",
    "QuestionPriority",
    "VoiceTranscriber",
    "VoiceTranscript",
    "VoiceTranscriptionError",
    "create_interview_pool",
    "priority_rank",
]
