"""Value objects for the proactive PM-interview engine.

Cendra's onboarding is **not** a settings form: it is an open-ended
Q&A loop that asks the PM how they handle each repeatable decision
(discount policy, code-release timing, vendor list, amenity
exceptions, ...).  Answers feed the per-property behavioural pattern
store; questions never truly stop being asked because new operational
events keep revealing gaps in coverage.

These objects are the wire format between the catalog, the engine,
and any persistence backend.  All records are ``frozen=True,
slots=True`` so they cross thread / async boundaries safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Final


__all__ = [
    "AnswerSource",
    "BookingStage",
    "InterviewAnswer",
    "InterviewCoverage",
    "InterviewQuestion",
    "QuestionPriority",
    "priority_rank",
]


class BookingStage(StrEnum):
    """Nine reservation lifecycle stages from the AI Pattern doc.

    Synthesises the five-stage CEO V2 directive (2026-04-20) with the
    finer-grained lifecycle in *AI Pattern for Devlet Brain Engine*:
    ``FIRMING``, ``ARRIVAL``, ``MID_STAY`` and ``EXIT`` slot between
    the original five stages so per-stage decisions surface in the
    right place on the timeline.

    Stable wire-strings — surfaced in the Trust Meter and onboarding
    progress UI; a rename is a breaking change.
    """

    INQUIRY = "inquiry"
    FIRMING = "firming"
    BOOKING_REVIEW = "booking_review"
    PRE_ARRIVAL = "pre_arrival"
    ARRIVAL = "arrival"
    IN_STAY = "in_stay"
    MID_STAY = "mid_stay"
    EXIT = "exit"
    POST_STAY = "post_stay"


class QuestionPriority(StrEnum):
    """How urgently a question must be asked relative to the rest."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AnswerSource(StrEnum):
    """How the answer reached us.

    ``INFERRED`` is reserved for answers Cendra deduced from observed
    PM behaviour (e.g. a manager approving every late-checkout request
    under 14:00 implies a tacit policy).
    """

    TEXT = "text"
    VOICE = "voice"
    INFERRED = "inferred"


_PRIORITY_RANK: Final[dict[QuestionPriority, int]] = {
    QuestionPriority.HIGH: 0,
    QuestionPriority.MEDIUM: 1,
    QuestionPriority.LOW: 2,
}


def priority_rank(priority: QuestionPriority) -> int:
    """Return the monotonic rank of a priority (lower = sooner)."""
    return _PRIORITY_RANK[priority]


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class InterviewQuestion:
    """A single canonical question Cendra may ask the PM.

    Attributes:
        qid: Stable question identifier (``"<stage>.<topic>"`` form).
        stage: Booking lifecycle stage the question informs.
        topic: Coarse subject — used to bucket related answers.
        prompt_text: PM-facing question text in English; UI is free
            to translate at render time.
        priority: Ordering hint for ``next_question`` selection.
        depends_on_qids: Other ``qid`` values whose answer must exist
            before this question is offered.
        triggered_by_events: ``event_type`` strings that, when raised
            at runtime, justify pulling this question to the top of
            the queue (the operation just hit a case the PM has not
            documented yet).
    """

    qid: str
    stage: BookingStage
    topic: str
    prompt_text: str
    priority: QuestionPriority = QuestionPriority.MEDIUM
    depends_on_qids: tuple[str, ...] = ()
    triggered_by_events: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InterviewAnswer:
    """One PM answer for a single question.

    Multiple answers per ``qid`` are not stored — re-answering a
    question replaces the prior record so the latest policy always
    wins.  Audit history lives elsewhere (decision logger).
    """

    property_id: str
    qid: str
    answer_text: str
    source: AnswerSource
    answered_at: datetime = field(default_factory=_utc_now)
    answered_by: str = "pm"


@dataclass(frozen=True, slots=True)
class InterviewCoverage:
    """Snapshot of how much of the catalog the PM has answered.

    Attributes:
        property_id: Property the snapshot describes.
        answered_total: Distinct qids answered.
        question_total: Total questions in the catalog.
        per_stage: ``stage -> (answered, total)``.
    """

    property_id: str
    answered_total: int
    question_total: int
    per_stage: dict[BookingStage, tuple[int, int]]

    @property
    def overall_ratio(self) -> float:
        """Fraction of catalog the PM has answered (0.0–1.0)."""
        if self.question_total == 0:
            return 0.0
        return self.answered_total / self.question_total

    def stage_ratio(self, stage: BookingStage) -> float:
        """Fraction of one stage's questions answered."""
        answered, total = self.per_stage.get(stage, (0, 0))
        if total == 0:
            return 0.0
        return answered / total
