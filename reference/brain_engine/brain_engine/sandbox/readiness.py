"""V1 onboarding sandbox readiness gate (step 15).

Mümin's onboarding flow asks the PM three sample questions in the
sandbox; once the PM has answered them, the property is "ready for
live" and the UI may flip the property's mode away from sandbox.

The gate is deliberately lightweight — Brain Engine is not the
governance layer here; the gate's job is to surface a *signal*, not
to enforce a state machine.  Acceptance criteria:

* The signal is derived from a single source of truth — the
  ``interview_answers`` table managed by
  :class:`InterviewAnswerStore`.  No mirror state, no shadow flag on
  the property profile.
* The signal is a pure function of stored answers: re-computing must
  always give the same answer; flipping back to "not ready" by
  retracting an answer must work without manual cleanup.
* "Ready" means *the PM has supplied at least N non-empty answers*
  for that property.  N defaults to the sandbox preview size (3) so
  the count matches the questions the UI showed them.

Anything richer (per-question content checks, qid-specific gates) is
deliberately out of scope — the V1 contract is just a count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from brain_engine.interview.store import InterviewAnswerStore


__all__ = [
    "DEFAULT_SANDBOX_REQUIRED_ANSWERS",
    "SandboxReadiness",
    "SandboxReadinessService",
]


logger = logging.getLogger(__name__)


DEFAULT_SANDBOX_REQUIRED_ANSWERS: Final[int] = 3


@dataclass(frozen=True, slots=True)
class SandboxReadiness:
    """Wire-stable snapshot of one property's sandbox gate.

    Attributes:
        property_id: Property the snapshot describes.
        ready: ``True`` once the PM has supplied at least
            ``required_count`` non-empty answers.
        answered_count: Distinct ``qid`` values the PM has answered
            for this property (empty answers do not count).
        required_count: Threshold the gate compares ``answered_count``
            against — equal to the sandbox preview size.
        last_answer_at: Most recent answer timestamp, or ``None`` when
            no answers exist yet.  UI uses this to surface "answered
            X minutes ago" hints next to the gate.
    """

    property_id: str
    ready: bool
    answered_count: int
    required_count: int
    last_answer_at: datetime | None


class SandboxReadinessService:
    """Compute the sandbox readiness signal for a property.

    The service is intentionally a thin read-side projection — it
    owns no state and never writes.  Construction is cheap; instantiate
    per request when convenient.
    """

    def __init__(
        self,
        store: InterviewAnswerStore,
        *,
        required: int = DEFAULT_SANDBOX_REQUIRED_ANSWERS,
    ) -> None:
        """Initialise SandboxReadinessService.

        Args:
            store: Backing store for :class:`InterviewAnswer` rows.
            required: Minimum distinct non-empty answers needed to
                flip the gate.  Must be at least 1; values below 1
                are clamped silently so a misconfiguration cannot
                make every property "ready" by accident.
        """
        self._store = store
        self._required = max(1, required)

    @property
    def required(self) -> int:
        """Return the configured answer threshold."""
        return self._required

    async def compute(
        self,
        property_id: str,
    ) -> SandboxReadiness:
        """Return the current :class:`SandboxReadiness` snapshot.

        Args:
            property_id: Property to evaluate.
        """
        rows = await self._store.list_for_property(property_id)
        non_empty = [row for row in rows if row.answer_text.strip()]
        # Distinct qids only — re-answering the same question must
        # not double-count toward the gate.
        unique_qids = {row.qid for row in non_empty}
        last_answer = (
            max((row.answered_at for row in non_empty), default=None)
        )
        return SandboxReadiness(
            property_id=property_id,
            ready=len(unique_qids) >= self._required,
            answered_count=len(unique_qids),
            required_count=self._required,
            last_answer_at=last_answer,
        )
