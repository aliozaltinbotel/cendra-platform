"""Value objects for the onboarding bootstrap flow.

Onboarding replays *historical* conversations and reservations through
the learning pipeline so a freshly-provisioned property does not start
from a cold cache.  These immutable records describe the shapes that
flow between the loader, the extractor, and the orchestrator service.

All objects are ``frozen=True, slots=True`` — no mutation after
construction, no hidden state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "ArchivedConversation",
    "ArchivedMessage",
    "MessageSender",
    "OnboardingReport",
    "OnboardingRequest",
    "PropertyReport",
]


class MessageSender(StrEnum):
    """Who wrote the archived message."""

    GUEST = "guest"
    PM = "pm"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ArchivedMessage:
    """A single message from a historical conversation.

    ``language`` is best-effort — the PMS does not always return a
    language tag.  Callers should treat it as a hint only.
    """

    sender: MessageSender
    text: str
    sent_at: datetime
    language: str = ""

    def is_guest(self) -> bool:
        return self.sender is MessageSender.GUEST


@dataclass(frozen=True, slots=True)
class ArchivedConversation:
    """Archived messages tied to one reservation.

    Used as the atomic unit fed to
    :class:`~brain_engine.onboarding.historical_case_extractor.HistoricalCaseExtractor`.
    Each conversation becomes at most one DecisionCase.
    """

    conversation_id: str
    property_id: str
    reservation_id: str
    guest_id: str
    messages: tuple[ArchivedMessage, ...]
    started_at: datetime
    ended_at: datetime
    channel: str = ""
    owner_id: str = ""
    guest_name: str = ""
    arrival_date: datetime | None = None
    departure_date: datetime | None = None
    reservation_data: dict[str, Any] | None = None

    def first_guest_message(self) -> ArchivedMessage | None:
        """Return the earliest guest message, if any.

        Iteration order is the conversation message order
        provided by the loader; callers MUST ensure the messages
        tuple is chronologically sorted before constructing the
        conversation (the GraphQL adapter sorts on load).
        """
        for message in self.messages:
            if message.is_guest():
                return message
        return None

    def first_pm_response(self) -> ArchivedMessage | None:
        """Return the earliest PM reply *after* the first guest message.

        Mümin 2026-05-11: the previous implementation returned the
        earliest non-guest message in the thread regardless of
        chronology, which on Hostaway / Airbnb threads is almost
        always the platform booking-confirmation email sent on
        ``createdAt`` — never the PM's actual response to the
        guest's question.  The classifier therefore saw the
        welcome text instead of the deny / approve text and fell
        through to :attr:`DecisionType.INFORM`.

        This implementation requires the returned PM message to
        have ``sent_at >= first_guest_message().sent_at`` so the
        classifier receives the real reply.  When no PM message
        post-dates the guest's first turn the method returns
        ``None`` and :class:`HistoricalCaseExtractor` skips the
        thread — the same semantics empty-thread skips already
        had.
        """
        guest = self.first_guest_message()
        if guest is None:
            return None
        for message in self.messages:
            if message.is_guest():
                continue
            if message.sent_at < guest.sent_at:
                continue
            return message
        return None


@dataclass(frozen=True, slots=True)
class OnboardingRequest:
    """Input to :class:`OnboardingService.bootstrap`.

    ``dry_run`` builds cases in memory but skips persistence, so a
    caller can preview the bootstrap volume before committing.
    """

    property_ids: tuple[str, ...]
    days: int = 180
    limit_per_property: int = 500
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class PropertyReport:
    """Per-property outcome of an onboarding run."""

    property_id: str
    conversations_loaded: int = 0
    cases_extracted: int = 0
    skipped: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class OnboardingReport:
    """Aggregate report returned by :class:`OnboardingService.bootstrap`."""

    property_reports: tuple[PropertyReport, ...]
    total_conversations: int
    total_cases: int
    total_skipped: int
    duration_seconds: float
    dry_run: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly serialisation for the HTTP layer."""
        return {
            "total_conversations": self.total_conversations,
            "total_cases": self.total_cases,
            "total_skipped": self.total_skipped,
            "duration_seconds": self.duration_seconds,
            "dry_run": self.dry_run,
            "errors": list(self.errors),
            "property_reports": [
                {
                    "property_id": report.property_id,
                    "conversations_loaded": report.conversations_loaded,
                    "cases_extracted": report.cases_extracted,
                    "skipped": report.skipped,
                    "error": report.error,
                }
                for report in self.property_reports
            ],
        }
