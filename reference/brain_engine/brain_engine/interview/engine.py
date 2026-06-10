"""Runtime engine for the proactive PM interview loop.

Selects the next question Cendra should ask, records the PM's answer,
and reports per-stage coverage.  Pure orchestration over a catalog +
a store; no I/O of its own.

Selection rules:

1. ``next_question`` returns the unanswered, dependency-satisfied
   question with the highest priority (HIGH < MEDIUM < LOW); ties
   break on stable ``qid`` lexical order so the surface is
   deterministic.
2. ``next_question_for_event`` is the same selection restricted to
   questions whose ``triggered_by_events`` includes the event.  The
   dispatcher pulls this when an operation just hit a case the PM has
   not documented yet — surfacing the question in the same chat
   thread as the action.
3. ``record_answer`` upserts and emits a structured info log so the
   answer is observable in audit pipelines without touching the store.
"""

from __future__ import annotations

import structlog

from brain_engine.interview.catalog import DEFAULT_CATALOG
from brain_engine.interview.models import (
    AnswerSource,
    BookingStage,
    InterviewAnswer,
    InterviewCoverage,
    InterviewQuestion,
    priority_rank,
)
from brain_engine.interview.store import InterviewAnswerStore


__all__ = ["InterviewEngine"]


logger = structlog.get_logger(__name__)


class InterviewEngine:
    """Orchestrates question selection and answer capture.

    Construction is cheap; instantiate per request when convenient.
    The catalog defaults to :data:`DEFAULT_CATALOG` but can be
    overridden for tests or tenant-specific catalogs.
    """

    def __init__(
        self,
        *,
        store: InterviewAnswerStore,
        catalog: tuple[InterviewQuestion, ...] | None = None,
    ) -> None:
        self._store = store
        self._catalog = catalog or DEFAULT_CATALOG
        self._index: dict[str, InterviewQuestion] = {
            question.qid: question for question in self._catalog
        }
        self._log = logger.bind(component="interview_engine")

    async def next_question(
        self,
        *,
        property_id: str,
        stage: BookingStage | None = None,
    ) -> InterviewQuestion | None:
        """Return the next question to ask, or ``None`` when caught up.

        Args:
            property_id: Property whose answers gate the selection.
            stage: Restrict the selection to a single lifecycle stage
                (the PM may want to focus an onboarding pass).
        """
        answered = await self._answered_qids(property_id)
        candidates = [
            question for question in self._catalog
            if question.qid not in answered
            and (stage is None or question.stage is stage)
            and self._dependencies_met(question, answered)
        ]
        return _pick_best(candidates)

    async def next_questions(
        self,
        *,
        property_id: str,
        max_count: int,
        stage: BookingStage | None = None,
    ) -> tuple[InterviewQuestion, ...]:
        """Return up to ``max_count`` highest-priority unanswered questions.

        Mirrors :meth:`next_question` but returns a ranked batch, used
        by the sandbox surface to let the PM preview several catalog
        entries at once (Mümin's onboarding step 13 — three sample
        questions).  ``max_count`` is clamped into ``[1, len(catalog)]``.
        """
        if max_count < 1:
            return ()
        answered = await self._answered_qids(property_id)
        candidates = [
            question for question in self._catalog
            if question.qid not in answered
            and (stage is None or question.stage is stage)
            and self._dependencies_met(question, answered)
        ]
        candidates.sort(key=lambda q: (priority_rank(q.priority), q.qid))
        return tuple(candidates[:max_count])

    async def next_question_for_event(
        self,
        *,
        property_id: str,
        event_type: str,
    ) -> InterviewQuestion | None:
        """Return the highest-priority question triggered by ``event_type``.

        Args:
            property_id: Property whose answers gate the selection.
            event_type: Live operational event (e.g. ``"orphan_night"``)
                whose handling depends on PM policy.
        """
        if not event_type:
            return None
        answered = await self._answered_qids(property_id)
        candidates = [
            question for question in self._catalog
            if event_type in question.triggered_by_events
            and question.qid not in answered
            and self._dependencies_met(question, answered)
        ]
        return _pick_best(candidates)

    async def record_answer(
        self,
        *,
        property_id: str,
        qid: str,
        answer_text: str,
        source: AnswerSource = AnswerSource.TEXT,
        answered_by: str = "pm",
    ) -> InterviewAnswer:
        """Persist a PM answer and return the stored record.

        Raises:
            ValueError: ``qid`` is not part of the active catalog.
        """
        if qid not in self._index:
            raise ValueError(f"Unknown question id: {qid!r}")
        clean_text = answer_text.strip()
        if not clean_text:
            raise ValueError("answer_text must not be blank")
        answer = InterviewAnswer(
            property_id=property_id,
            qid=qid,
            answer_text=clean_text,
            source=source,
            answered_by=answered_by,
        )
        await self._store.put(answer)
        self._log.info(
            "interview.answer_recorded",
            property_id=property_id,
            qid=qid,
            source=source.value,
            answered_by=answered_by,
        )
        return answer

    async def coverage(
        self,
        *,
        property_id: str,
    ) -> InterviewCoverage:
        """Return per-stage and overall answer coverage for a property."""
        answered = await self._answered_qids(property_id)
        per_stage: dict[BookingStage, tuple[int, int]] = {}
        for stage in BookingStage:
            stage_questions = [
                question for question in self._catalog
                if question.stage is stage
            ]
            answered_count = sum(
                1 for question in stage_questions
                if question.qid in answered
            )
            per_stage[stage] = (answered_count, len(stage_questions))
        return InterviewCoverage(
            property_id=property_id,
            answered_total=len(answered),
            question_total=len(self._catalog),
            per_stage=per_stage,
        )

    def question(self, qid: str) -> InterviewQuestion | None:
        """Return the catalog question by ``qid``, or ``None``."""
        return self._index.get(qid)

    # ── Helpers ──────────────────────────────────────────── #

    async def _answered_qids(self, property_id: str) -> set[str]:
        rows = await self._store.list_for_property(property_id)
        return {row.qid for row in rows}

    @staticmethod
    def _dependencies_met(
        question: InterviewQuestion,
        answered: set[str],
    ) -> bool:
        if not question.depends_on_qids:
            return True
        return all(dep in answered for dep in question.depends_on_qids)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _pick_best(
    candidates: list[InterviewQuestion],
) -> InterviewQuestion | None:
    """Return the highest-priority candidate; ``qid`` breaks ties."""
    if not candidates:
        return None
    candidates.sort(key=lambda q: (priority_rank(q.priority), q.qid))
    return candidates[0]
