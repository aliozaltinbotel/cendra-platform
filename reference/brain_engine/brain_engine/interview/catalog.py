"""Canonical question catalog for the PM interview engine.

The catalog is the in-process default knowledge base of "what Cendra
needs to learn from each PM".  Sourced from the booking-stage
checklist in the CEO V2 directive (2026-04-20) and the nine-stage
lifecycle in *AI Pattern for Devlet Brain Engine*:

- **Inquiry**: discount handling, min-stay exceptions, orphan nights,
  large-house low-guest-count anomaly, transfer/upsell selling.
- **Firming**: counter-offer floor when a guest negotiates terms.
- **Booking review**: risk flags, when to ask extra verification,
  when to require manual review.
- **Pre-arrival**: guest count mismatch, extra-person fee, check-in
  code release conditions, blockers when info missing, amenity
  exceptions.
- **Arrival**: how to handle delayed arrivals past the standard
  check-in window.
- **In-stay**: early check-in, vendor dispatch flow, complaint
  compensation, message tone, discount yes/no.
- **Mid-stay**: proactive guest check-in cadence for longer stays.
- **Exit**: photo / inspection requirement at checkout.
- **Post-stay**: late checkout approval, damage/claim flow,
  cleaning/inspection decisions.

The catalog is intentionally hand-curated rather than generated:
phrasing matters, and a manager-facing question must read like a
real conversation rather than a settings-form label.
"""

from __future__ import annotations

from typing import Final

from brain_engine.interview.models import (
    BookingStage,
    InterviewQuestion,
    QuestionPriority,
)


__all__ = ["DEFAULT_CATALOG"]


_INQUIRY_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="inquiry.discount_policy",
        stage=BookingStage.INQUIRY,
        topic="discount",
        prompt_text=(
            "When a guest asks for a discount, what is your default "
            "rule? (e.g. flat percent, only on long stays, never.)"
        ),
        priority=QuestionPriority.HIGH,
        triggered_by_events=("inquiry", "discount_request"),
    ),
    InterviewQuestion(
        qid="inquiry.min_stay_exception",
        stage=BookingStage.INQUIRY,
        topic="min_stay",
        prompt_text=(
            "Do you accept stays shorter than your minimum when they "
            "fill an orphan gap? Under what conditions?"
        ),
        priority=QuestionPriority.HIGH,
        triggered_by_events=("min_stay_exception", "orphan_night"),
    ),
    InterviewQuestion(
        qid="inquiry.large_house_low_guest_count",
        stage=BookingStage.INQUIRY,
        topic="risk_signals",
        prompt_text=(
            "When a small group books a large unit (e.g. 2 guests for "
            "a 6-bedroom house), how do you handle it?"
        ),
        priority=QuestionPriority.MEDIUM,
        triggered_by_events=("inquiry",),
    ),
    InterviewQuestion(
        qid="inquiry.transfer_or_upsell",
        stage=BookingStage.INQUIRY,
        topic="upsell",
        prompt_text=(
            "If a guest's request does not fit the unit they picked, "
            "do you offer a transfer to another listing, an upsell, or "
            "decline?"
        ),
        priority=QuestionPriority.LOW,
        triggered_by_events=("transfer_offer", "upsell"),
    ),
)

_FIRMING_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="firming.counter_offer_floor",
        stage=BookingStage.FIRMING,
        topic="negotiation",
        prompt_text=(
            "When a guest counter-offers on price, what is your "
            "absolute floor — flat percent off list, a per-night "
            "minimum, or always hold list price?"
        ),
        priority=QuestionPriority.HIGH,
        triggered_by_events=("counter_offer", "price_negotiation"),
    ),
)

_BOOKING_REVIEW_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="booking_review.risk_flags",
        stage=BookingStage.BOOKING_REVIEW,
        topic="risk_signals",
        prompt_text=(
            "What flags in a booking trigger you to ask for extra "
            "verification (ID, deposit, video call)?"
        ),
        priority=QuestionPriority.HIGH,
    ),
    InterviewQuestion(
        qid="booking_review.manual_review_required",
        stage=BookingStage.BOOKING_REVIEW,
        topic="risk_signals",
        prompt_text=(
            "Are there any reservation types you always want to "
            "review yourself before confirming?"
        ),
        priority=QuestionPriority.MEDIUM,
    ),
)

_PRE_ARRIVAL_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="pre_arrival.guest_count_mismatch",
        stage=BookingStage.PRE_ARRIVAL,
        topic="extra_fee",
        prompt_text=(
            "If the actual guest count differs from the booking, how "
            "do you handle it — extra fee, cancel, or allow with "
            "warning?"
        ),
        priority=QuestionPriority.HIGH,
        triggered_by_events=("guest_count_mismatch", "extra_person_fee"),
    ),
    InterviewQuestion(
        qid="pre_arrival.code_release_timing",
        stage=BookingStage.PRE_ARRIVAL,
        topic="code_release",
        prompt_text=(
            "Does the door code change every reservation, or stay the "
            "same? When do you send it to the guest?"
        ),
        priority=QuestionPriority.HIGH,
        triggered_by_events=("code_release", "send_access_code"),
    ),
    InterviewQuestion(
        qid="pre_arrival.amenity_exceptions",
        stage=BookingStage.PRE_ARRIVAL,
        topic="amenities",
        prompt_text=(
            "If an amenity is not in the listing (e.g. baby crib, "
            "parking spot), in what cases do you provide it anyway?"
        ),
        priority=QuestionPriority.MEDIUM,
    ),
    InterviewQuestion(
        qid="pre_arrival.blocker_on_missing_info",
        stage=BookingStage.PRE_ARRIVAL,
        topic="blockers",
        prompt_text=(
            "When a reservation lacks information you need (passport, "
            "ETA, guest count), do you block check-in or chase it "
            "softly?"
        ),
        priority=QuestionPriority.MEDIUM,
    ),
)

_ARRIVAL_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="arrival.delayed_arrival_policy",
        stage=BookingStage.ARRIVAL,
        topic="arrival_window",
        prompt_text=(
            "If a guest is delayed past 22:00 with no notice, what is "
            "your default — keep waiting, leave the key in a lockbox, "
            "or charge a late-arrival fee?"
        ),
        priority=QuestionPriority.MEDIUM,
        triggered_by_events=("delayed_arrival", "late_arrival_no_eta"),
    ),
)

_IN_STAY_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="in_stay.early_checkin_policy",
        stage=BookingStage.IN_STAY,
        topic="early_checkin",
        prompt_text=(
            "When can you offer early check-in? What is the earliest "
            "hour you accept if there is no same-day cleaning conflict?"
        ),
        priority=QuestionPriority.MEDIUM,
        triggered_by_events=("early_checkin", "early_checkin_request"),
    ),
    InterviewQuestion(
        qid="in_stay.vendor_dispatch_contacts",
        stage=BookingStage.IN_STAY,
        topic="vendor",
        prompt_text=(
            "Who do you call for cleaning, plumbing, locksmith? Share "
            "phone or messaging contact for each."
        ),
        priority=QuestionPriority.HIGH,
        triggered_by_events=("vendor_dispatched", "cleaner_dispatched"),
    ),
    InterviewQuestion(
        qid="in_stay.complaint_compensation",
        stage=BookingStage.IN_STAY,
        topic="compensation",
        prompt_text=(
            "When a guest complains during the stay, what compensation "
            "are you willing to offer (refund percent, free night, "
            "gift)?"
        ),
        priority=QuestionPriority.MEDIUM,
    ),
    InterviewQuestion(
        qid="in_stay.message_tone",
        stage=BookingStage.IN_STAY,
        topic="tone",
        prompt_text=(
            "What is your default message tone — formal, friendly, or "
            "concise? Any phrases you always use?"
        ),
        priority=QuestionPriority.LOW,
    ),
)

_MID_STAY_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="mid_stay.proactive_checkin_cadence",
        stage=BookingStage.MID_STAY,
        topic="proactive_outreach",
        prompt_text=(
            "On stays of four or more nights, do you check in with the "
            "guest proactively (after night one, mid-stay), or only "
            "respond when they reach out?"
        ),
        priority=QuestionPriority.MEDIUM,
        triggered_by_events=("long_stay_started", "mid_stay_checkpoint"),
    ),
)

_EXIT_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="exit.checkout_inspection_requirement",
        stage=BookingStage.EXIT,
        topic="checkout_verification",
        prompt_text=(
            "At checkout, do you require photo confirmation from the "
            "guest or cleaner before releasing the deposit, or trust "
            "the cleaner's checklist alone?"
        ),
        priority=QuestionPriority.MEDIUM,
        triggered_by_events=("checkout_completed", "deposit_release"),
    ),
)

_POST_STAY_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        qid="post_stay.late_checkout_policy",
        stage=BookingStage.POST_STAY,
        topic="late_checkout",
        prompt_text=(
            "When can you offer late checkout? What is the latest hour "
            "you accept if the next guest arrives the same day?"
        ),
        priority=QuestionPriority.MEDIUM,
        triggered_by_events=("late_checkout", "late_checkout_request"),
    ),
    InterviewQuestion(
        qid="post_stay.damage_claim_flow",
        stage=BookingStage.POST_STAY,
        topic="damage",
        prompt_text=(
            "If there is damage after checkout, what is your usual "
            "flow — claim from deposit, contact insurance, or write "
            "it off?"
        ),
        priority=QuestionPriority.HIGH,
        triggered_by_events=("damage_report", "damage_claim"),
    ),
    InterviewQuestion(
        qid="post_stay.cleaning_inspection",
        stage=BookingStage.POST_STAY,
        topic="cleaning",
        prompt_text=(
            "After cleaning, do you inspect every unit yourself or "
            "trust the cleaner's checklist?"
        ),
        priority=QuestionPriority.MEDIUM,
    ),
)


DEFAULT_CATALOG: Final[tuple[InterviewQuestion, ...]] = (
    *_INQUIRY_QUESTIONS,
    *_FIRMING_QUESTIONS,
    *_BOOKING_REVIEW_QUESTIONS,
    *_PRE_ARRIVAL_QUESTIONS,
    *_ARRIVAL_QUESTIONS,
    *_IN_STAY_QUESTIONS,
    *_MID_STAY_QUESTIONS,
    *_EXIT_QUESTIONS,
    *_POST_STAY_QUESTIONS,
)
