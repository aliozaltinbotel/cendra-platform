"""Tests for the granular ``empty_thread`` diagnosis helper.

Mümin 2026-05-12 follow-up: the audit log was emitting
:attr:`SkipReason.EMPTY_THREAD` for every thread the
:class:`EpisodeBuilder` rejected, which conflated three
operationally distinct realities:

* PM-only threads (booking confirmations, welcome blasts) where
  there is genuinely no guest question to learn from.
* Threads where the guest spoke but the PM never replied —
  these are unanswered threads that belong on the sandbox
  example-reply pipeline.
* Threads with a real guest + PM exchange that the
  ``EpisodeBuilder`` gap heuristic still rejected — these are
  the only ones worth investigating further.

This test pins the new
:func:`_diagnose_empty_thread_reason` contract so the per-cause
breakdown the production audit log shows the operator stays
trustworthy as the helper evolves.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.onboarding.bootstrap_pipeline import (
    _diagnose_empty_thread_reason,
)
from brain_engine.onboarding.event_bus import SkipReason
from brain_engine.onboarding.models import (
    ArchivedConversation,
    ArchivedMessage,
    MessageSender,
)


_BASE = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)


def _conv(*senders: MessageSender) -> ArchivedConversation:
    """Build a conversation from a sequence of sender labels."""
    msgs = tuple(
        ArchivedMessage(
            sender=sender,
            text="…",
            sent_at=_BASE,
            language="en",
        )
        for sender in senders
    )
    return ArchivedConversation(
        conversation_id="c",
        property_id="p",
        reservation_id="r",
        guest_id="g",
        messages=msgs,
        started_at=_BASE,
        ended_at=_BASE,
    )


def test_zero_messages_classifies_as_no_guest_message() -> None:
    """Empty thread → no guest spoke."""
    assert (
        _diagnose_empty_thread_reason(_conv())
        is SkipReason.NO_GUEST_MESSAGE
    )


def test_pm_only_thread_classifies_as_no_guest_message() -> None:
    """A reservation-confirmation thread carries no guest question."""
    assert (
        _diagnose_empty_thread_reason(
            _conv(MessageSender.PM, MessageSender.PM),
        )
        is SkipReason.NO_GUEST_MESSAGE
    )


def test_system_only_thread_classifies_as_no_guest_message() -> None:
    """System / bot announcements don't count as a guest question."""
    assert (
        _diagnose_empty_thread_reason(
            _conv(MessageSender.SYSTEM, MessageSender.SYSTEM),
        )
        is SkipReason.NO_GUEST_MESSAGE
    )


def test_guest_only_thread_classifies_as_no_pm_response_after_guest() -> None:
    """Unanswered thread → guest spoke but the PM never replied."""
    assert (
        _diagnose_empty_thread_reason(
            _conv(MessageSender.GUEST, MessageSender.GUEST),
        )
        is SkipReason.NO_PM_RESPONSE_AFTER_GUEST
    )


def test_pm_then_guest_classifies_as_no_pm_response_after_guest() -> None:
    """Booking confirmation followed by guest question with no PM follow-up.

    The PM message preceded the guest, so from the learning
    pipeline's perspective there is still no PM *response* to
    learn from.
    """
    assert (
        _diagnose_empty_thread_reason(
            _conv(MessageSender.PM, MessageSender.GUEST),
        )
        is SkipReason.NO_PM_RESPONSE_AFTER_GUEST
    )


def test_guest_then_pm_classifies_as_empty_thread() -> None:
    """A real exchange that ``EpisodeBuilder`` still rejected — investigate.

    Both sides spoke, the PM replied *after* the guest — but
    :meth:`EpisodeBuilder.split` returned no episodes anyway.
    Likely cause is a gap heuristic miss; this bucket is the
    operator's remaining mystery.
    """
    assert (
        _diagnose_empty_thread_reason(
            _conv(MessageSender.GUEST, MessageSender.PM),
        )
        is SkipReason.EMPTY_THREAD
    )


def test_system_then_guest_then_pm_classifies_as_empty_thread() -> None:
    """A real Q&A cycle preceded by a system notice still goes to EMPTY_THREAD."""
    assert (
        _diagnose_empty_thread_reason(
            _conv(
                MessageSender.SYSTEM,
                MessageSender.GUEST,
                MessageSender.PM,
            ),
        )
        is SkipReason.EMPTY_THREAD
    )


@pytest.mark.parametrize(
    ("senders", "expected"),
    [
        (
            (MessageSender.GUEST, MessageSender.GUEST, MessageSender.PM),
            SkipReason.EMPTY_THREAD,
        ),
        (
            (MessageSender.PM, MessageSender.GUEST),
            SkipReason.NO_PM_RESPONSE_AFTER_GUEST,
        ),
        (
            (MessageSender.GUEST,),
            SkipReason.NO_PM_RESPONSE_AFTER_GUEST,
        ),
        ((), SkipReason.NO_GUEST_MESSAGE),
    ],
)
def test_diagnosis_table(
    senders: tuple[MessageSender, ...],
    expected: SkipReason,
) -> None:
    """Table-driven sweep of the production combinations."""
    assert _diagnose_empty_thread_reason(_conv(*senders)) is expected
