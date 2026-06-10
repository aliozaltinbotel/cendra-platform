"""Tests for the bootstrap message-selection + time-reference fixes.

Mümin 2026-05-11: a Hostaway property archive carries the booking-
confirmation email as the first non-guest message (sent at
``createdAt``), followed weeks later by the guest's actual
question and the PM's reply.  The original
:meth:`ArchivedConversation.first_pm_response` returned the
booking confirmation regardless of chronology, so the classifier
saw the welcome text instead of the deny / approve text and the
case landed as :attr:`DecisionType.INFORM` by fallback.  The same
historical replay also dated ``hours_before_checkin`` from
extraction wall-clock rather than the message ``sent_at``, so a
6-day-before-check-in reply showed up as ``1012`` hours (~42
days) in the snapshot.

These tests pin both fixes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.onboarding.models import (
    ArchivedConversation,
    ArchivedMessage,
    MessageSender,
)


def _msg(
    *,
    sender: MessageSender,
    text: str,
    at: datetime,
) -> ArchivedMessage:
    """Build a minimal archived message."""
    return ArchivedMessage(
        sender=sender,
        text=text,
        sent_at=at,
    )


def _conv(
    *,
    messages: tuple[ArchivedMessage, ...],
    check_in: datetime | None = None,
    check_out: datetime | None = None,
) -> ArchivedConversation:
    """Build a minimal archived conversation."""
    started_at = (
        messages[0].sent_at if messages else datetime(
            2026, 5, 1, tzinfo=timezone.utc,
        )
    )
    ended_at = (
        messages[-1].sent_at if messages else started_at
    )
    return ArchivedConversation(
        conversation_id="conv-1",
        property_id="214216",
        reservation_id="56556018",
        guest_id="guest-1",
        messages=messages,
        started_at=started_at,
        ended_at=ended_at,
        arrival_date=check_in,
        departure_date=check_out,
    )


# ── Bug 1 — first_pm_response selects post-guest reply ────── #


def test_pm_reply_after_guest_is_returned() -> None:
    """The actual reply (after guest turn) wins over the welcome."""
    welcome = _msg(
        sender=MessageSender.PM,
        text="Thank you so much for booking our place...",
        at=datetime(2026, 3, 23, 13, 24, 18, tzinfo=timezone.utc),
    )
    guest = _msg(
        sender=MessageSender.GUEST,
        text="Hi, can I get the door code for tomorrow?",
        at=datetime(2026, 6, 16, 15, 0, tzinfo=timezone.utc),
    )
    reply = _msg(
        sender=MessageSender.PM,
        text="Unfortunately, I can't give you the code right now.",
        at=datetime(2026, 6, 16, 15, 5, tzinfo=timezone.utc),
    )
    conv = _conv(messages=(welcome, guest, reply))
    assert conv.first_pm_response() == reply


def test_welcome_email_is_not_returned_as_reply() -> None:
    """The Mümin failure case: welcome must not look like a reply."""
    welcome = _msg(
        sender=MessageSender.PM,
        text="Thank you for booking ...",
        at=datetime(2026, 3, 23, 13, 24, 18, tzinfo=timezone.utc),
    )
    guest = _msg(
        sender=MessageSender.GUEST,
        text="What time is check-in?",
        at=datetime(2026, 6, 16, 15, 0, tzinfo=timezone.utc),
    )
    # PM never replied — only the welcome exists.
    conv = _conv(messages=(welcome, guest))
    assert conv.first_pm_response() is None


def test_no_guest_message_returns_none() -> None:
    """An archive with no guest turn cannot have a PM reply."""
    welcome = _msg(
        sender=MessageSender.PM,
        text="Thank you for booking ...",
        at=datetime(2026, 3, 23, tzinfo=timezone.utc),
    )
    conv = _conv(messages=(welcome,))
    assert conv.first_pm_response() is None


def test_empty_messages_returns_none() -> None:
    """An empty conversation safely returns ``None``."""
    conv = _conv(messages=())
    assert conv.first_pm_response() is None
    assert conv.first_guest_message() is None


def test_first_reply_after_guest_when_multiple_pm_turns() -> None:
    """Pick the earliest PM message that post-dates the guest turn."""
    welcome = _msg(
        sender=MessageSender.PM,
        text="Thank you for booking ...",
        at=datetime(2026, 3, 23, tzinfo=timezone.utc),
    )
    guest = _msg(
        sender=MessageSender.GUEST,
        text="When is check-in?",
        at=datetime(2026, 6, 16, 15, 0, tzinfo=timezone.utc),
    )
    reply1 = _msg(
        sender=MessageSender.PM,
        text="Unfortunately, I can't help now.",
        at=datetime(2026, 6, 16, 15, 5, tzinfo=timezone.utc),
    )
    reply2 = _msg(
        sender=MessageSender.PM,
        text="Actually here is the code: 1234",
        at=datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc),
    )
    conv = _conv(messages=(welcome, guest, reply1, reply2))
    assert conv.first_pm_response() == reply1


def test_pm_at_exact_guest_timestamp_is_returned() -> None:
    """A PM message stamped at the guest instant still counts."""
    same_instant = datetime(
        2026, 6, 16, 15, 0, tzinfo=timezone.utc,
    )
    guest = _msg(
        sender=MessageSender.GUEST,
        text="Hi",
        at=same_instant,
    )
    pm = _msg(
        sender=MessageSender.PM,
        text="Hello",
        at=same_instant,
    )
    conv = _conv(messages=(guest, pm))
    assert conv.first_pm_response() == pm


def test_first_guest_message_unchanged() -> None:
    """Bug-1 fix must not regress the guest-side selector."""
    g1 = _msg(
        sender=MessageSender.GUEST,
        text="first",
        at=datetime(2026, 6, 16, tzinfo=timezone.utc),
    )
    g2 = _msg(
        sender=MessageSender.GUEST,
        text="second",
        at=datetime(2026, 6, 17, tzinfo=timezone.utc),
    )
    conv = _conv(messages=(g1, g2))
    assert conv.first_guest_message() == g1


# ── Bug 2 — case_builder accepts decision_at override ────── #


@pytest.mark.asyncio
async def test_case_builder_uses_decision_at_for_hours_before_checkin(
    tmp_path,
) -> None:
    """Historical replay anchors hours_before_checkin to message time."""
    from brain_engine.patterns.case_builder import CaseBuilder
    from brain_engine.patterns.models import (
        BookingStage,
        DecisionType,
        Scenario,
    )

    from brain_engine.patterns.feature_builder import FeatureBuilder
    builder = CaseBuilder(feature_builder=FeatureBuilder())

    # check_in 2026-06-22, message sent 2026-06-16 -> 6 days = 144 h.
    pms_data = {
        "reservation_id": "56556018",
        "check_in": "2026-06-22T15:00:00+00:00",
        "check_out": "2026-06-24T10:00:00+00:00",
        "adults": 3,
        "children": 0,
        "status": "modified",
    }
    message_sent_at = datetime(
        2026, 6, 16, 15, 0, tzinfo=timezone.utc,
    )

    case = await builder.build(
        message_text="Hi, can I get the door code?",
        response_text="Unfortunately, I can't.",
        property_id="214216",
        owner_id="",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.DENY,
        pms_data=pms_data,
        decision_at=message_sent_at,
    )

    hbc = case.pms_snapshot.get("hours_before_checkin")
    assert hbc is not None
    # 6-day window: 144 hours +/- a small float drift.
    assert 140.0 <= hbc <= 150.0


@pytest.mark.asyncio
async def test_case_builder_default_decision_at_uses_wall_clock(
    tmp_path,
) -> None:
    """Live callers (no decision_at) keep the prior wall-clock anchor."""
    from brain_engine.patterns.case_builder import CaseBuilder
    from brain_engine.patterns.models import (
        BookingStage,
        DecisionType,
        Scenario,
    )

    from brain_engine.patterns.feature_builder import FeatureBuilder
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    # check_in far in the future so wall-clock anchors to a large
    # positive ``hours_before_checkin``.
    pms_data = {
        "reservation_id": "any",
        "check_in": "2099-01-01T00:00:00+00:00",
        "check_out": "2099-01-02T00:00:00+00:00",
        "adults": 2,
        "children": 0,
        "status": "active",
    }
    case = await builder.build(
        message_text="Hi",
        response_text="ok",
        property_id="p",
        owner_id="",
        stage=BookingStage.PRE_BOOKING,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.INFORM,
        pms_data=pms_data,
    )
    hbc = case.pms_snapshot.get("hours_before_checkin")
    assert hbc is not None
    # Far-future check-in => a very large positive lead time.
    assert hbc > 100_000.0
