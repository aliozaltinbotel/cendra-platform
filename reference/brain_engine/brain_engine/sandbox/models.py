"""Value objects for the onboarding sandbox surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = ["UnansweredThread"]


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class UnansweredThread:
    """One unanswered guest thread plus its generated example reply.

    Attributes:
        conversation_id: Stable identifier of the conversation;
            doubles as the sandbox row key.
        property_id: Property channel identifier the thread belongs to.
        last_guest_message: Verbatim text of the most recent guest
            message — the prompt the example reply must answer.
        last_guest_sent_at: Timestamp of the last guest message.
        example_reply: Generated candidate reply.  Always non-empty;
            the template generator returns a deterministic placeholder
            when no LLM is wired.
        generated_by: Stable generator identifier (``"template"`` for
            the deterministic fallback, ``"openai:<model>"`` when an
            LLM produced the reply).
        language: Best-effort language tag inherited from the guest
            message (``""`` when unknown).
        generated_at: When the sandbox row was produced.
        needs_review_reason: Comma-separated rule names from
            :func:`brain_engine.sandbox.review_heuristics.classify_review_need`
            (``"contains_time,contains_secret"`` etc.).  Empty when no
            suspicious-fact pattern was detected.  The sandbox UI uses
            this to surface the riskiest candidates first; the PM
            still reviews every row.
    """

    conversation_id: str
    property_id: str
    last_guest_message: str
    last_guest_sent_at: datetime
    example_reply: str
    generated_by: str
    language: str = ""
    generated_at: datetime = field(default_factory=_utc_now)
    needs_review_reason: str = ""
