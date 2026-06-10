"""Tests for the foundation-slug → Scenario enum bridge.

Locks the visible behaviour that ``case.scenario`` stops being
``GENERAL`` for ~95% of multilingual traffic once the Foundation
matcher provides a precise slug.

Two layers under test:

1. :func:`derive_scenario_from_foundation_slug` — the pure helper.
   None for empty / unknown slugs.  Most-specific patterns win
   first (booking_extension before late_checkout before generic
   stage fallback).
2. :meth:`CaseBuilder.build` — the integration.  When the
   classifier returned ``GENERAL`` *and* a foundation slug is
   supplied, the emitted case carries the derived enum.  When the
   classifier returned a non-GENERAL value, that wins — derivation
   never overrides a confident classifier.
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.patterns.models import (
    BookingStage,
    DecisionType,
    Scenario,
)
from brain_engine.patterns.scenario_derivation import (
    derive_scenario_from_foundation_slug,
)

# ── helper layer ──────────────────────────────────────────── #


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        # Booking modifications
        (
            "s7_357_guest_asks_to_stay_additional_few_hours",
            Scenario.BOOKING_EXTENSION,
        ),
        (
            "s2_74_guest_asks_to_extend_stay_after_booking",
            Scenario.BOOKING_EXTENSION,
        ),
        # Cancellation
        (
            "s2_80_guest_asks_to_cancel_reservation",
            Scenario.CANCELLATION_REQUEST,
        ),
        # Check-in / late arrival
        (
            "s1_16_guest_asks_for_early_checkin_before_arrival",
            Scenario.EARLY_CHECKIN,
        ),
        (
            "s2_64_guest_asks_to_arrive_very_late",
            Scenario.EARLY_CHECKIN,
        ),
        (
            "s3_133_guest_says_flight_delayed_and_will_be_late",
            Scenario.EARLY_CHECKIN,
        ),
        # Late checkout
        (
            "s7_366_guest_asks_for_late_checkout",
            Scenario.LATE_CHECKOUT,
        ),
        # Access codes
        (
            "s2_56_guest_asks_for_door_code_too_early",
            Scenario.ACCESS_CODE_RELEASE,
        ),
        (
            "s4_180_guest_asks_for_smart_lock_code",
            Scenario.ACCESS_CODE_RELEASE,
        ),
        # Beds / amenities
        (
            "s4_193_guest_says_sofa_bed_is_not_available",
            Scenario.EXTRA_BED_REQUEST,
        ),
        (
            "s3_111_guest_asks_for_extra_towels_before_arrival",
            Scenario.AMENITY_EXCEPTION,
        ),
        # Maintenance
        (
            "s5_237_guest_says_amenity_is_missing",
            Scenario.AMENITY_EXCEPTION,
        ),
        (
            "s3_140_guest_asks_if_there_is_construction_nearby",
            Scenario.MAINTENANCE_REQUEST,
        ),
        # People / pets / parking
        (
            "s3_145_guest_asks_if_extra_guest_can_join",
            Scenario.GUEST_COUNT_MISMATCH,
        ),
        (
            "s1_10_guest_asks_if_visitors_are_allowed",
            Scenario.GUEST_COUNT_MISMATCH,
        ),
        (
            "s2_72_guest_asks_about_parking_fees",
            Scenario.PARKING_REQUEST,
        ),
        (
            "s1_30_guest_asks_about_pet_policy",
            Scenario.PET_POLICY_EXCEPTION,
        ),
        # Complaints / refunds
        (
            "s5_276_guest_complaint_about_noise",
            Scenario.NOISE_COMPLAINT,
        ),
        # Lost item
        (
            "s8_400_guest_left_item_behind_after_checkout",
            Scenario.LOST_ITEM,
        ),
    ],
)
def test_derive_known_slugs(
    slug: str,
    expected: Scenario,
) -> None:
    """Every catalog family resolves to the expected enum."""
    assert derive_scenario_from_foundation_slug(slug) is expected


def test_derive_returns_none_for_empty() -> None:
    """Empty / ``None`` input ⇒ ``None``."""
    assert derive_scenario_from_foundation_slug(None) is None
    assert derive_scenario_from_foundation_slug("") is None


def test_derive_returns_none_for_unmapped_slug() -> None:
    """Slugs without a confident mapping stay ``None`` — caller falls back.

    The 2026-05-18 coverage expansion bumped enum coverage to
    461/473; the remaining 12 truly-unmapped catalog entries are
    documentation pseudo-scenarios (``s9_10_engineering_notes...``
    etc.) that should never be promoted to a learnable bucket.
    """
    assert (
        derive_scenario_from_foundation_slug(
            "s9_10_engineering_notes_for_classifiers",
        )
        is None
    )
    assert (
        derive_scenario_from_foundation_slug(
            "s9_13_final_principle",
        )
        is None
    )


def test_derive_ordering_booking_extension_beats_late_checkout() -> None:
    """A slug containing both keywords resolves to the more specific one.

    ``stay_additional`` is the booking_extension wording; the slug
    is intentionally lifted from the real catalog
    (``s7_357_guest_asks_to_stay_additional_few_hours``) where the
    intent is "extend the booking", not "leave a few hours late".
    """
    assert (
        derive_scenario_from_foundation_slug(
            "s7_357_guest_asks_to_stay_additional_few_hours",
        )
        is Scenario.BOOKING_EXTENSION
    )


# ── CaseBuilder integration ──────────────────────────────── #


@pytest.mark.asyncio
async def test_case_builder_overrides_general_when_slug_known() -> None:
    """Classifier GENERAL + foundation slug ⇒ derived enum lands on case."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="erken check-in mumkun mu?",
        response_text="ok",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.GENERAL,
        decision_type=DecisionType.INFORM,
        foundation_scenario_id="s1_16_guest_asks_for_early_checkin_before_arrival",
    )
    assert case.scenario is Scenario.EARLY_CHECKIN
    assert case.foundation_scenario_id == (
        "s1_16_guest_asks_for_early_checkin_before_arrival"
    )


@pytest.mark.asyncio
async def test_case_builder_keeps_general_when_slug_unmapped() -> None:
    """GENERAL stays GENERAL when the slug has no enum mapping.

    Uses one of the 12 documentation pseudo-scenarios remaining
    after the 2026-05-18 coverage expansion — those should never
    map to a learnable bucket.
    """
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="notes",
        response_text="welcome",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.POST_CHECKOUT,
        scenario=Scenario.GENERAL,
        decision_type=DecisionType.INFORM,
        foundation_scenario_id="s9_12_qa_notes",
    )
    assert case.scenario is Scenario.GENERAL


@pytest.mark.asyncio
async def test_case_builder_keeps_general_without_foundation_slug() -> None:
    """Pre-W1 cases (no foundation slug) keep GENERAL unchanged."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="legacy text",
        response_text="ok",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.IN_STAY,
        scenario=Scenario.GENERAL,
        decision_type=DecisionType.INFORM,
    )
    assert case.scenario is Scenario.GENERAL
    assert case.foundation_scenario_id is None


@pytest.mark.asyncio
async def test_case_builder_keeps_classifier_when_not_general() -> None:
    """A confident non-GENERAL classifier is never overridden."""
    builder = CaseBuilder(feature_builder=FeatureBuilder())
    case = await builder.build(
        message_text="access code please",
        response_text="here",
        property_id="prop-1",
        owner_id="owner-1",
        stage=BookingStage.IN_STAY,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        decision_type=DecisionType.INFORM,
        # Foundation slug would map to BOOKING_EXTENSION — but
        # classifier was confident so it wins.
        foundation_scenario_id=(
            "s7_357_guest_asks_to_stay_additional_few_hours"
        ),
    )
    assert case.scenario is Scenario.ACCESS_CODE_RELEASE


# ── 2026-05-18 Foundation 469 coverage expansion ──────────── #
#
# These tests pin Aybüke's 2026-05-18 mapping audit. The
# explicit slug-override dict in scenario_derivation.py was
# generated from BOLUM 2 + BOLUM 4 of
# ``foundation_469_oneri_senaryo_numaralari.txt`` after the
# coverage audit verified 461/473 (97.5%) of the live catalog
# now lands on a real Scenario enum (was 148/473 = 31%).


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        # ── BOLUM 4 — new buckets ──
        (
            "s2_55_guest_asks_for_full_address_immediately",
            Scenario.PREARRIVAL_INFO_DISCLOSURE,
        ),
        (
            "s3_97_guest_asks_if_cleaning_is_finished",
            Scenario.PROPERTY_READINESS_CHECK,
        ),
        (
            "s4_152_guest_says_they_are_at_the",
            Scenario.ARRIVAL_EVENT,
        ),
        (
            "s3_128_guest_asks_if_windows_have_blackout",
            Scenario.PROPERTY_FEATURE_INQUIRY,
        ),
        (
            "s1_8_guest_asks_to_bring_more_people",
            Scenario.VISITOR_OCCUPANCY_POLICY,
        ),
        (
            "s1_2_samenight_inquiry_after_midnight_from_local",
            Scenario.OFF_HOURS_HANDLING,
        ),
        (
            "s1_50_guest_asks_if_local_resident_can",
            Scenario.PROXY_BOOKING_RISK,
        ),
        (
            "s6_322_extra_cleaning_fee_after_excessive_mess",
            Scenario.CLEANING_FEE_NEGOTIATION,
        ),
        (
            "s5_238_guest_asks_for_midstay_cleaning",
            Scenario.MIDSTAY_SERVICE_REQUEST,
        ),
        (
            "s4_189_guest_says_windows_or_doors_do",
            Scenario.SAFETY_SECURITY_CONCERN,
        ),
        (
            "s4_179_guest_says_instructions_are_confusing",
            Scenario.INSTRUCTION_QUALITY_ISSUE,
        ),
        (
            "s5_278_guest_reports_lost_personal_item_during",
            Scenario.INSTAY_LOST_ITEM_AND_RETURN,
        ),
        (
            "s8_394_guest_says_cleaning_issue_ruined_stay",
            Scenario.POSTSTAY_FEEDBACK_AND_DISPUTE,
        ),
        (
            "s7_344_guest_reports_issue_right_before_checkout",
            Scenario.CHECKOUT_TURNOVER_OPS,
        ),
        (
            "s5_277_guest_asks_for_local_emergency_number",
            Scenario.ESCALATION_CONTACT_REQUEST,
        ),
        # ── BOLUM 2 — new buckets ──
        (
            "s1_6_guest_asks_to_pay_outside_platform",
            Scenario.OFF_PLATFORM_CONTACT,
        ),
        (
            "s4_209_guest_reports_gas_smell",
            Scenario.SAFETY_EMERGENCY,
        ),
        (
            "s5_253_guest_says_wifi_is_completely_down",
            Scenario.UTILITY_OUTAGE,
        ),
        (
            "s5_257_guest_says_toilet_blocked",
            Scenario.PLUMBING_ISSUE,
        ),
        (
            "s4_153_guest_cannot_open_lockbox",
            Scenario.ACCESS_FAILURE,
        ),
        (
            "s3_91_guest_asks_for_checkin_instructions_one",
            Scenario.CHECKIN_INSTRUCTIONS,
        ),
        (
            "s2_89_guest_says_payment_failed_but_ota",
            Scenario.PAYMENT_ISSUE,
        ),
        (
            "s1_22_guest_asks_if_invoice_is_available",
            Scenario.INVOICE_REQUEST,
        ),
        (
            "s1_24_guest_asks_whether_security_deposit_is",
            Scenario.SECURITY_DEPOSIT,
        ),
        (
            "s3_149_guest_asks_about_city_tax_or",
            Scenario.TAX_INQUIRY,
        ),
        (
            "s5_285_guest_threatens_chargeback",
            Scenario.CHARGEBACK_DISPUTE,
        ),
        (
            "s5_235_guest_asks_for_refund_during_stay",
            Scenario.REFUND_REQUEST,
        ),
        (
            "s1_23_guest_asks_whether_id_or_passport",
            Scenario.ID_VERIFICATION,
        ),
        (
            "s4_156_guest_says_address_is_wrong",
            Scenario.LISTING_DISCREPANCY,
        ),
        (
            "s5_222_guest_asks_how_to_use_dishwasher",
            Scenario.APPLIANCE_USAGE_GUIDE,
        ),
        (
            "s4_158_guest_says_property_is_dirty",
            Scenario.CLEANLINESS_COMPLAINT,
        ),
        (
            "s5_220_guest_asks_for_activity_recommendations",
            Scenario.CONCIERGE_LOCAL,
        ),
        (
            "s1_36_guest_asks_about_luggage_dropoff_before",
            Scenario.LUGGAGE_PACKAGE,
        ),
        (
            "s8_379_guest_leaves_bad_review",
            Scenario.REVIEW_MANAGEMENT,
        ),
        (
            "s8_382_guest_asks_to_book_again",
            Scenario.REPEAT_BOOKING,
        ),
        (
            "s9_428_owner_blocks_dates",
            Scenario.OWNER_INTERNAL,
        ),
        (
            "s9_453_pms_calendar_sync_fails",
            Scenario.INTEGRATION_FAILURE,
        ),
        (
            "s1_20_guest_asks_if_children_are_allowed",
            Scenario.PROPERTY_INFO_REQUEST,
        ),
        (
            "s5_250_guest_sends_emotional_angry_message",
            Scenario.GUEST_ESCALATION,
        ),
        (
            "s6_325_room_or_unit_upgrade_offer",
            Scenario.MODIFICATION_GUEST,
        ),
        (
            "s5_233_guest_loses_key",
            Scenario.LOCKOUT,
        ),
        (
            "s7_340_guest_leaves_late",
            Scenario.CHECKOUT_COMPLIANCE,
        ),
        (
            "s2_51_instant_booking_confirmed_with_complete_guest",
            Scenario.RESERVATION_DATA_GAP,
        ),
        (
            "s4_167_guest_says_neighbor_complained_during_arrival",
            Scenario.NEIGHBOR_COMPLAINT,
        ),
        (
            "s4_172_guest_sends_photo_evidence_of_dirty",
            Scenario.MEDIA_EVIDENCE,
        ),
        (
            "s6_328_workspace_setup_upsell",
            Scenario.EQUIPMENT_RENTAL_UPSELL,
        ),
        (
            "s5_243_guest_reports_camera_or_privacy_concern",
            Scenario.PRIVACY_CONCERN,
        ),
        (
            "s9_433_ai_has_missing_property_knowledge",
            Scenario.AI_KNOWLEDGE_GAP,
        ),
        (
            "s8_404_guest_left_low_rating_but_positive",
            Scenario.MIXED_SENTIMENT_REVIEW,
        ),
    ],
)
def test_derive_2026_05_18_expansion(
    slug: str,
    expected: Scenario,
) -> None:
    """At least one representative slug per new enum maps correctly."""
    assert derive_scenario_from_foundation_slug(slug) is expected


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        # Singleton folds — saved one enum each by mapping to an
        # existing bucket the singleton naturally belongs to.
        (
            "s2_85_guest_asks_for_quiet_room_or",
            Scenario.MODIFICATION,
        ),
        (
            "s1_7_guest_says_calendar_unavailable_but_asks",
            Scenario.BOOKING_EXTENSION,
        ),
        (
            "s5_232_guest_breaks_something_and_asks_what",
            Scenario.DAMAGE_REPORT,
        ),
    ],
)
def test_derive_singleton_folds(
    slug: str,
    expected: Scenario,
) -> None:
    """Singleton scenarios fold into existing semantic neighbours."""
    assert derive_scenario_from_foundation_slug(slug) is expected


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        # Reassignments — Aybüke's file misclassified these by
        # # number after the catalog grew; the per-title audit
        # placed them in the correct bucket.
        (
            "s2_77_guest_refuses_to_submit_id_required",
            Scenario.ID_VERIFICATION,
        ),
        (
            "s4_203_guest_says_concierge_refuses_entry",
            Scenario.ACCESS_FAILURE,
        ),
        (
            "s5_247_guest_reports_cooling_discomfort",
            Scenario.MAINTENANCE_REQUEST,
        ),
        (
            "s5_274_guest_asks_for_linen_change",
            Scenario.MIDSTAY_SERVICE_REQUEST,
        ),
        (
            "s6_329_weekly_cleaning_package_for_monthly_stay",
            Scenario.CLEANER_DISPATCH,
        ),
        (
            "s7_338_guest_asks_for_checkout_instructions",
            Scenario.CHECKOUT_COMPLIANCE,
        ),
        (
            "s7_341_guest_asks_where_to_leave_keys",
            Scenario.CHECKOUT_COMPLIANCE,
        ),
    ],
)
def test_derive_reassignments_after_audit(
    slug: str,
    expected: Scenario,
) -> None:
    """Audit-driven reassignments override Aybüke's # number drift."""
    assert derive_scenario_from_foundation_slug(slug) is expected


def test_derive_catalog_coverage_threshold() -> None:
    """Live 469 catalog must hit ≥90% non-GENERAL coverage.

    Regression bound: a future refactor that accidentally drops
    the slug-override dict would silently crater coverage back to
    the ~31% baseline.  This test loads the actual shipped
    foundation MD and walks every scenario through the bridge —
    if the dict integration breaks, the count crashes and the
    assertion fails loudly with the actual coverage number.
    """
    from pathlib import Path

    from brain_engine.patterns.foundation_registry import (
        load_foundation_scenarios,
    )

    foundation_doc = (
        Path(__file__).resolve().parent.parent
        / "Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md"
    )
    scenarios = load_foundation_scenarios(foundation_doc)
    mapped = sum(
        1
        for sc in scenarios
        if derive_scenario_from_foundation_slug(sc.scenario_id) is not None
    )
    coverage_pct = mapped / len(scenarios) * 100
    assert coverage_pct >= 90.0, (
        f"Catalog coverage regressed to {coverage_pct:.1f}% "
        f"({mapped}/{len(scenarios)}); expected ≥90% after the "
        f"2026-05-18 expansion."
    )


def test_derive_documentation_pseudo_scenarios_stay_general() -> None:
    """``s9_10..s9_13`` documentation entries must stay unmapped.

    The catalog's MD includes a few section-header pseudo-rows
    (``Engineering Notes``, ``Product Notes``, ``QA Notes``,
    ``Final Principle``) that share the slug shape but are not
    real scenarios.  Promoting them to a learnable bucket would
    poison the miner with non-actionable cases.
    """
    for slug in (
        "s9_10_engineering_notes_for_classifiers",
        "s9_11_product_notes_for_approval_ux",
        "s9_12_qa_notes",
        "s9_13_final_principle",
    ):
        assert derive_scenario_from_foundation_slug(slug) is None, slug
