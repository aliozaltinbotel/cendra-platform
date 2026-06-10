"""Derive a :class:`Scenario` enum value from a Foundation Layer slug.

The legacy ``DecisionClassifier`` is keyword-regex-based on English-only
patterns.  It falls back to :pyattr:`Scenario.GENERAL` on ~95% of
real multilingual traffic — Czech, Slovak, Polish, Russian, Turkish
messages rarely contain the English keywords the classifier looks
for.  The visible symptom: every Postman / dashboard query returns
``scenario: "general"`` even when the case is clearly about an early
check-in, a late checkout, an extra bed, or a maintenance issue.

The Foundation matcher (FL-16) does not suffer from this — its
multilingual embedding model maps the same Czech / Slovak / Polish
message to the correct 469-catalog slug (e.g. ``s3_112_*early*``,
``s7_357_*extend_stay*``).  This module bridges the two systems:
when the classifier degrades to ``GENERAL`` and a foundation slug
is available, we recover the coarser :class:`Scenario` enum from
the slug's title via a small regex table.

The table is intentionally **conservative**: only slug titles whose
intent maps cleanly onto an existing :class:`Scenario` enum get
remapped.  Anything we cannot map confidently stays ``GENERAL`` —
the new behaviour is strictly additive (more variety, never wrong
overrides).  Patterns are ordered most-specific first so a single
slug never satisfies two rules.

Used by :class:`brain_engine.patterns.case_builder.CaseBuilder` on
both the live (``ConversationService._log_decision_case``) and the
bootstrap (``HistoricalCaseExtractor``) paths so the two ingest
surfaces stay in lockstep.
"""

from __future__ import annotations

import re
from typing import Final

from brain_engine.patterns.models import Scenario

__all__ = ["derive_scenario_from_foundation_slug"]


# Explicit slug → Scenario mapping for the 469 hospitality catalog
# (Aybüke 2026-05-18 coverage expansion).  Built from the BOLUM 2 +
# BOLUM 4 grouping in
# ``foundation_469_oneri_senaryo_numaralari.txt`` after a per-title
# audit removed ~10 mis-grouped entries that drifted as the catalog
# grew (UTILITY_OUTAGE #77 was actually an ID refusal, etc.).
#
# Lookup order in :func:`derive_scenario_from_foundation_slug`:
# this dict is checked first (deterministic, per-slug precision);
# only slugs not in the dict (newly-added catalog entries, future
# drift) fall through to the regex table below.  That keeps the
# system self-healing: a brand-new catalog slug still gets a coarse
# bucket via the regex, then a follow-up PR can promote it to an
# explicit override if it deserves a sharper mapping.
_FOUNDATION_SLUG_OVERRIDES: Final[dict[str, Scenario]] = {
    # ── ACCESS_FAILURE (count=11) ──
    "s3_101_guest_asks_for_lockbox_code": Scenario.ACCESS_FAILURE,
    "s4_153_guest_cannot_open_lockbox": Scenario.ACCESS_FAILURE,
    "s4_155_guest_cannot_find_key": Scenario.ACCESS_FAILURE,
    "s4_180_guest_says_lockbox_is_empty": Scenario.ACCESS_FAILURE,
    "s4_181_guest_says_smart_lock_battery_is": Scenario.ACCESS_FAILURE,
    "s4_183_guest_says_key_broke_in_lock": Scenario.ACCESS_FAILURE,
    "s4_201_guest_cannot_access_building_gate": Scenario.ACCESS_FAILURE,
    "s4_203_guest_says_concierge_refuses_entry": Scenario.ACCESS_FAILURE,
    "s4_204_guest_says_id_verification_blocks_access": Scenario.ACCESS_FAILURE,
    "s4_206_guest_arrives_after_midnight_and_access": Scenario.ACCESS_FAILURE,
    "s9_458_smart_lock_integration_down": Scenario.ACCESS_FAILURE,
    # ── AI_KNOWLEDGE_GAP (count=4) ──
    "s9_433_ai_has_missing_property_knowledge": Scenario.AI_KNOWLEDGE_GAP,
    "s9_434_ai_finds_repeated_missing_info": Scenario.AI_KNOWLEDGE_GAP,
    "s9_466_repeated_accesscode_confusion_detected": Scenario.AI_KNOWLEDGE_GAP,
    "s9_468_repeated_owner_approval_delay": Scenario.AI_KNOWLEDGE_GAP,
    # ── APPLIANCE_USAGE_GUIDE (count=8) ──
    "s3_108_guest_asks_for_ac_instructions_before": Scenario.APPLIANCE_USAGE_GUIDE,
    "s3_127_guest_asks_for_temperature_to_be": Scenario.APPLIANCE_USAGE_GUIDE,
    "s5_221_guest_asks_how_to_use_washing": Scenario.APPLIANCE_USAGE_GUIDE,
    "s5_222_guest_asks_how_to_use_dishwasher": Scenario.APPLIANCE_USAGE_GUIDE,
    "s5_223_guest_asks_how_to_use_stove": Scenario.APPLIANCE_USAGE_GUIDE,
    "s5_224_guest_asks_how_to_use_tv": Scenario.APPLIANCE_USAGE_GUIDE,
    "s5_226_guest_asks_pool_instructions": Scenario.APPLIANCE_USAGE_GUIDE,
    "s5_267_guest_asks_for_thermostat_limit_override": Scenario.APPLIANCE_USAGE_GUIDE,
    # ── ARRIVAL_EVENT (count=2) ──
    "s3_95_guest_arrives_earlier_than_expected": Scenario.ARRIVAL_EVENT,
    "s4_152_guest_says_they_are_at_the": Scenario.ARRIVAL_EVENT,
    # ── BOOKING_EXTENSION (count=1; fold AVAILABILITY_OVERRIDE) ──
    "s1_7_guest_says_calendar_unavailable_but_asks": Scenario.BOOKING_EXTENSION,
    # ── CHARGEBACK_DISPUTE (count=3) ──
    "s5_285_guest_threatens_chargeback": Scenario.CHARGEBACK_DISPUTE,
    "s8_396_guest_threatens_chargeback_after_checkout": Scenario.CHARGEBACK_DISPUTE,
    "s8_408_guest_disputes_lost_key_fee": Scenario.CHARGEBACK_DISPUTE,
    # ── CHECKIN_INSTRUCTIONS (count=9) ──
    "s1_37_guest_asks_whether_self_checkin_is": Scenario.CHECKIN_INSTRUCTIONS,
    "s2_54_guest_asks_for_checkin_instructions_immediately": Scenario.CHECKIN_INSTRUCTIONS,
    "s3_100_guest_cannot_find_the_building": Scenario.CHECKIN_INSTRUCTIONS,
    "s3_123_guest_asks_for_key_pickup_by": Scenario.CHECKIN_INSTRUCTIONS,
    "s3_147_guest_asks_if_remote_checkin_video": Scenario.CHECKIN_INSTRUCTIONS,
    "s3_91_guest_asks_for_checkin_instructions_one": Scenario.CHECKIN_INSTRUCTIONS,
    "s3_92_guest_asks_for_checkin_instructions_on": Scenario.CHECKIN_INSTRUCTIONS,
    "s3_99_guest_asks_for_driving_directions": Scenario.CHECKIN_INSTRUCTIONS,
    "s4_178_guest_cannot_find_building_entrance": Scenario.CHECKIN_INSTRUCTIONS,
    # ── CHECKOUT_COMPLIANCE (count=10) ──
    "s5_225_guest_asks_trash_disposal_rules": Scenario.CHECKOUT_COMPLIANCE,
    "s7_338_guest_asks_for_checkout_instructions": Scenario.CHECKOUT_COMPLIANCE,
    "s7_340_guest_leaves_late": Scenario.CHECKOUT_COMPLIANCE,
    "s7_341_guest_asks_where_to_leave_keys": Scenario.CHECKOUT_COMPLIANCE,
    "s7_345_guest_says_they_cleaned_the_property": Scenario.CHECKOUT_COMPLIANCE,
    "s7_356_guest_does_not_check_out_on": Scenario.CHECKOUT_COMPLIANCE,
    "s7_363_guest_leaves_trash_or_dishes": Scenario.CHECKOUT_COMPLIANCE,
    "s7_368_guest_asks_for_checkout_extension_because": Scenario.CHECKOUT_COMPLIANCE,
    "s7_370_guest_asks_where_to_dispose_trash": Scenario.CHECKOUT_COMPLIANCE,
    "s7_371_guest_asks_whether_linens_should_be": Scenario.CHECKOUT_COMPLIANCE,
    # ── CHECKOUT_TURNOVER_OPS (count=2) ──
    "s7_344_guest_reports_issue_right_before_checkout": Scenario.CHECKOUT_TURNOVER_OPS,
    "s7_354_next_guest_has_sameday_arrival": Scenario.CHECKOUT_TURNOVER_OPS,
    # ── CLEANER_DISPATCH (count=1; reassigned from LUGGAGE_PACKAGE) ──
    "s6_329_weekly_cleaning_package_for_monthly_stay": Scenario.CLEANER_DISPATCH,
    # ── CLEANING_FEE_NEGOTIATION (count=2) ──
    "s1_49_guest_asks_if_owner_can_remove": Scenario.CLEANING_FEE_NEGOTIATION,
    "s6_322_extra_cleaning_fee_after_excessive_mess": Scenario.CLEANING_FEE_NEGOTIATION,
    # ── CLEANLINESS_COMPLAINT (count=6) ──
    "s4_158_guest_says_property_is_dirty": Scenario.CLEANLINESS_COMPLAINT,
    "s4_159_guest_says_bed_linens_are_missing": Scenario.CLEANLINESS_COMPLAINT,
    "s4_186_guest_says_property_smells_bad": Scenario.CLEANLINESS_COMPLAINT,
    "s4_187_guest_finds_previous_guest_belongings": Scenario.CLEANLINESS_COMPLAINT,
    "s4_188_guest_finds_pests_or_insects_at": Scenario.CLEANLINESS_COMPLAINT,
    "s5_245_guest_reports_bad_smell_or_mold": Scenario.CLEANLINESS_COMPLAINT,
    # ── CONCIERGE_LOCAL (count=17) ──
    "s1_18_guest_asks_distance_to_beach_or": Scenario.CONCIERGE_LOCAL,
    "s1_19_guest_asks_distance_to_airport_and": Scenario.CONCIERGE_LOCAL,
    "s1_41_guest_asks_for_nearest_public_transport": Scenario.CONCIERGE_LOCAL,
    "s2_61_guest_asks_for_airport_transfer_after": Scenario.CONCIERGE_LOCAL,
    "s2_66_guest_asks_for_local_recommendations_after": Scenario.CONCIERGE_LOCAL,
    "s2_83_guest_asks_for_grocery_delivery_before": Scenario.CONCIERGE_LOCAL,
    "s3_104_guest_asks_for_nearby_supermarket": Scenario.CONCIERGE_LOCAL,
    "s3_105_guest_asks_for_taxi_or_transfer": Scenario.CONCIERGE_LOCAL,
    "s3_131_guest_asks_for_public_transport_route": Scenario.CONCIERGE_LOCAL,
    "s3_135_guest_asks_for_special_dietary_grocery": Scenario.CONCIERGE_LOCAL,
    "s3_136_guest_asks_for_restaurant_booking_assistance": Scenario.CONCIERGE_LOCAL,
    "s5_219_guest_asks_for_restaurant_recommendations": Scenario.CONCIERGE_LOCAL,
    "s5_220_guest_asks_for_activity_recommendations": Scenario.CONCIERGE_LOCAL,
    "s6_307_airport_transfer_request": Scenario.CONCIERGE_LOCAL,
    "s6_317_local_tour_or_activity_request": Scenario.CONCIERGE_LOCAL,
    "s7_369_guest_asks_for_taxi_after_checkout": Scenario.CONCIERGE_LOCAL,
    "s8_389_guest_provides_useful_local_recommendation_feedback": Scenario.CONCIERGE_LOCAL,
    # ── DAMAGE_REPORT (count=1; fold GUEST_DAMAGE_SELF_REPORT) ──
    "s5_232_guest_breaks_something_and_asks_what": Scenario.DAMAGE_REPORT,
    # ── EARLY_INQUIRY_IGNORED (count=4) ──
    "s1_27_guest_sends_vague_message_with_no": Scenario.EARLY_INQUIRY_IGNORED,
    "s3_113_guest_does_not_reply_to_required": Scenario.EARLY_INQUIRY_IGNORED,
    "s5_251_guest_is_silent_after_issue_resolution": Scenario.EARLY_INQUIRY_IGNORED,
    "s5_289_guest_sends_only_photo_without_text": Scenario.EARLY_INQUIRY_IGNORED,
    # ── EQUIPMENT_RENTAL_UPSELL (count=4) ──
    "s6_328_workspace_setup_upsell": Scenario.EQUIPMENT_RENTAL_UPSELL,
    "s6_332_ev_charging_fee_request": Scenario.EQUIPMENT_RENTAL_UPSELL,
    "s6_333_beach_equipment_rental": Scenario.EQUIPMENT_RENTAL_UPSELL,
    "s6_335_ski_equipment_storage_fee": Scenario.EQUIPMENT_RENTAL_UPSELL,
    # ── ESCALATION_CONTACT_REQUEST (count=2) ──
    "s5_277_guest_asks_for_local_emergency_number": Scenario.ESCALATION_CONTACT_REQUEST,
    "s5_286_guest_asks_for_owner_contact": Scenario.ESCALATION_CONTACT_REQUEST,
    # ── GUEST_ESCALATION (count=5) ──
    "s5_250_guest_sends_emotional_angry_message": Scenario.GUEST_ESCALATION,
    "s5_270_guest_complains_about_mattress_comfort": Scenario.GUEST_ESCALATION,
    "s5_271_guest_complains_about_missing_kitchen_item": Scenario.GUEST_ESCALATION,
    "s8_380_guest_privately_complains_after_checkout": Scenario.GUEST_ESCALATION,
    "s8_397_guest_says_they_will_contact_ota": Scenario.GUEST_ESCALATION,
    # ── ID_VERIFICATION (count=7) ──
    "s1_23_guest_asks_whether_id_or_passport": Scenario.ID_VERIFICATION,
    "s2_76_guest_asks_how_to_submit_id": Scenario.ID_VERIFICATION,
    "s2_77_guest_refuses_to_submit_id_required": Scenario.ID_VERIFICATION,
    "s3_114_guest_has_not_submitted_id_or": Scenario.ID_VERIFICATION,
    "s3_116_guest_has_not_signed_rental_agreement": Scenario.ID_VERIFICATION,
    "s3_124_guest_sends_invalid_id_document": Scenario.ID_VERIFICATION,
    "s3_151_guest_asks_for_exact_unit_number": Scenario.ID_VERIFICATION,
    # ── INSTAY_LOST_ITEM_AND_RETURN (count=4) ──
    "s5_278_guest_reports_lost_personal_item_during": Scenario.INSTAY_LOST_ITEM_AND_RETURN,
    "s7_342_guest_forgets_items": Scenario.INSTAY_LOST_ITEM_AND_RETURN,
    "s8_407_guest_asks_shipping_cost_for_lost": Scenario.INSTAY_LOST_ITEM_AND_RETURN,
    "s8_410_guest_asks_for_photos_of_found": Scenario.INSTAY_LOST_ITEM_AND_RETURN,
    # ── INSTRUCTION_QUALITY_ISSUE (count=2) ──
    "s4_179_guest_says_instructions_are_confusing": Scenario.INSTRUCTION_QUALITY_ISSUE,
    "s4_200_guest_says_phone_number_in_instructions": Scenario.INSTRUCTION_QUALITY_ISSUE,
    # ── INTEGRATION_FAILURE (count=7) ──
    "s9_431_pms_data_conflicts_with_guest_message": Scenario.INTEGRATION_FAILURE,
    "s9_453_pms_calendar_sync_fails": Scenario.INTEGRATION_FAILURE,
    "s9_454_ota_reservation_imported_twice": Scenario.INTEGRATION_FAILURE,
    "s9_455_airbnb_message_arrives_without_mapped_reservation": Scenario.INTEGRATION_FAILURE,
    "s9_457_whatsapp_guest_not_linked_to_reservation": Scenario.INTEGRATION_FAILURE,
    "s9_459_cleaning_system_integration_down": Scenario.INTEGRATION_FAILURE,
    "s9_462_channel_policy_prevents_requested_action": Scenario.INTEGRATION_FAILURE,
    # ── INVOICE_REQUEST (count=8) ──
    "s1_22_guest_asks_if_invoice_is_available": Scenario.INVOICE_REQUEST,
    "s2_57_guest_asks_for_invoice_or_receipt": Scenario.INVOICE_REQUEST,
    "s3_148_guest_asks_for_invoice_details_before": Scenario.INVOICE_REQUEST,
    "s5_280_guest_asks_for_invoice_during_stay": Scenario.INVOICE_REQUEST,
    "s7_347_guest_asks_for_invoice_at_checkout": Scenario.INVOICE_REQUEST,
    "s8_390_guest_asks_for_invoice_later": Scenario.INVOICE_REQUEST,
    "s8_401_guest_asks_for_corporate_invoice_details": Scenario.INVOICE_REQUEST,
    "s8_406_guest_asks_for_receipt_for_extras": Scenario.INVOICE_REQUEST,
    # ── LISTING_DISCREPANCY (count=6) ──
    "s4_156_guest_says_address_is_wrong": Scenario.LISTING_DISCREPANCY,
    "s4_191_guest_says_unit_number_is_wrong": Scenario.LISTING_DISCREPANCY,
    "s4_196_guest_says_pool_is_closed_despite": Scenario.LISTING_DISCREPANCY,
    "s4_199_guest_says_ota_sent_wrong_address": Scenario.LISTING_DISCREPANCY,
    "s5_236_guest_says_listing_is_inaccurate": Scenario.LISTING_DISCREPANCY,
    "s8_417_guest_says_amenities_should_be_updated": Scenario.LISTING_DISCREPANCY,
    # ── LOCKOUT (count=7) ──
    "s5_233_guest_loses_key": Scenario.LOCKOUT,
    "s5_234_guest_locks_themselves_out": Scenario.LOCKOUT,
    "s6_320_lost_key_fee_assessment": Scenario.LOCKOUT,
    "s6_321_replacement_key_delivery_fee": Scenario.LOCKOUT,
    "s7_359_guest_cannot_lock_door_when_leaving": Scenario.LOCKOUT,
    "s7_360_guest_leaves_keys_in_wrong_place": Scenario.LOCKOUT,
    "s7_361_guest_takes_keys_accidentally": Scenario.LOCKOUT,
    # ── LUGGAGE_PACKAGE (count=9) ──
    "s1_36_guest_asks_about_luggage_dropoff_before": Scenario.LUGGAGE_PACKAGE,
    "s3_132_guest_asks_for_luggage_delivery_address": Scenario.LUGGAGE_PACKAGE,
    "s3_138_guest_asks_to_ship_package_to": Scenario.LUGGAGE_PACKAGE,
    "s3_96_guest_wants_luggage_dropoff_before_checkin": Scenario.LUGGAGE_PACKAGE,
    "s5_279_guest_asks_to_receive_package_at": Scenario.LUGGAGE_PACKAGE,
    "s6_326_early_luggage_dropoff_paid_offer": Scenario.LUGGAGE_PACKAGE,
    "s6_327_luggage_storage_after_checkout_paid_offer": Scenario.LUGGAGE_PACKAGE,
    "s7_343_guest_asks_luggage_storage_after_checkout": Scenario.LUGGAGE_PACKAGE,
    "s7_362_guest_leaves_luggage_inside_after_checkout": Scenario.LUGGAGE_PACKAGE,
    # ── MAINTENANCE_REQUEST (count=1; reassigned from SAFETY_SECURITY) ──
    "s5_247_guest_reports_cooling_discomfort": Scenario.MAINTENANCE_REQUEST,
    # ── MEDIA_EVIDENCE (count=2) ──
    "s4_172_guest_sends_photo_evidence_of_dirty": Scenario.MEDIA_EVIDENCE,
    "s4_173_guest_sends_video_evidence_of_access": Scenario.MEDIA_EVIDENCE,
    # ── MIDSTAY_SERVICE_REQUEST (count=6) ──
    "s5_214_guest_requests_extra_cleaning": Scenario.MIDSTAY_SERVICE_REQUEST,
    "s5_238_guest_asks_for_midstay_cleaning": Scenario.MIDSTAY_SERVICE_REQUEST,
    "s5_239_guest_asks_for_laundry_service": Scenario.MIDSTAY_SERVICE_REQUEST,
    "s5_249_guest_requests_special_local_service": Scenario.MIDSTAY_SERVICE_REQUEST,
    "s5_272_guest_asks_for_replacement_coffee_capsules": Scenario.MIDSTAY_SERVICE_REQUEST,
    "s5_274_guest_asks_for_linen_change": Scenario.MIDSTAY_SERVICE_REQUEST,
    # ── MIN_STAY_EXCEPTION (count=1) ──
    "s1_3_zeroreview_guest_asks_onenight_weekend_stay": Scenario.MIN_STAY_EXCEPTION,
    # ── MIXED_SENTIMENT_REVIEW (count=2) ──
    "s8_404_guest_left_low_rating_but_positive": Scenario.MIXED_SENTIMENT_REVIEW,
    "s8_405_guest_left_positive_rating_but_private": Scenario.MIXED_SENTIMENT_REVIEW,
    # ── MODIFICATION (count=7; fold UNIT_ASSIGNMENT_REQUEST) ──
    "s2_58_guest_asks_to_change_number_of": Scenario.MODIFICATION,
    "s2_73_guest_asks_to_shorten_stay_after": Scenario.MODIFICATION,
    "s2_85_guest_asks_for_quiet_room_or": Scenario.MODIFICATION,
    "s3_125_guest_asks_to_change_lead_guest": Scenario.MODIFICATION,
    "s3_129_guest_asks_for_bedding_configuration_change": Scenario.MODIFICATION,
    "s4_168_guest_says_they_booked_wrong_dates": Scenario.MODIFICATION,
    "s5_230_guest_asks_to_shorten_stay": Scenario.MODIFICATION,
    # ── MODIFICATION_GUEST (count=2) ──
    "s4_197_guest_asks_to_move_to_another": Scenario.MODIFICATION_GUEST,
    "s6_325_room_or_unit_upgrade_offer": Scenario.MODIFICATION_GUEST,
    # ── NEIGHBOR_COMPLAINT (count=2) ──
    "s4_167_guest_says_neighbor_complained_during_arrival": Scenario.NEIGHBOR_COMPLAINT,
    "s7_373_guest_says_neighbor_complained_during_departure": Scenario.NEIGHBOR_COMPLAINT,
    # ── OFF_HOURS_HANDLING (count=2) ──
    "s1_2_samenight_inquiry_after_midnight_from_local": Scenario.OFF_HOURS_HANDLING,
    "s3_112_guest_asks_if_they_can_check": Scenario.OFF_HOURS_HANDLING,
    # ── OFF_PLATFORM_CONTACT (count=3) ──
    "s1_28_guest_sends_suspicious_message_asking_to": Scenario.OFF_PLATFORM_CONTACT,
    "s1_6_guest_asks_to_pay_outside_platform": Scenario.OFF_PLATFORM_CONTACT,
    "s2_87_guest_asks_to_communicate_via_whatsapp": Scenario.OFF_PLATFORM_CONTACT,
    # ── ORPHAN_NIGHT_EXCEPTION (count=1) ──
    "s6_324_orphan_night_extension_offer": Scenario.ORPHAN_NIGHT_EXCEPTION,
    # ── OWNER_INTERNAL (count=10) ──
    "s9_427_owner_asks_about_current_reservation": Scenario.OWNER_INTERNAL,
    "s9_428_owner_blocks_dates": Scenario.OWNER_INTERNAL,
    "s9_429_owner_asks_revenue_question": Scenario.OWNER_INTERNAL,
    "s9_430_pm_changes_sop_manually": Scenario.OWNER_INTERNAL,
    "s9_447_owner_asks_to_change_house_rule": Scenario.OWNER_INTERNAL,
    "s9_448_owner_asks_to_block_dates_for": Scenario.OWNER_INTERNAL,
    "s9_449_owner_asks_for_incident_summary": Scenario.OWNER_INTERNAL,
    "s9_452_finance_flags_unpaid_extra_fee": Scenario.OWNER_INTERNAL,
    "s9_463_property_listing_data_conflicts_with_sop": Scenario.OWNER_INTERNAL,
    "s9_469_night_shift_handoff_missing_context": Scenario.OWNER_INTERNAL,
    # ── PAYMENT_ISSUE (count=7) ──
    "s1_44_guest_asks_for_split_payment_or": Scenario.PAYMENT_ISSUE,
    "s1_45_guest_asks_to_reserve_without_paying": Scenario.PAYMENT_ISSUE,
    "s2_67_payment_verification_issue_after_booking": Scenario.PAYMENT_ISSUE,
    "s2_89_guest_says_payment_failed_but_ota": Scenario.PAYMENT_ISSUE,
    "s5_282_guest_says_card_charged_incorrectly": Scenario.PAYMENT_ISSUE,
    "s9_456_bookingcom_virtual_card_issue": Scenario.PAYMENT_ISSUE,
    "s9_460_payment_link_failed": Scenario.PAYMENT_ISSUE,
    # ── PLUMBING_ISSUE (count=2) ──
    "s5_256_guest_says_shower_drain_clogged": Scenario.PLUMBING_ISSUE,
    "s5_257_guest_says_toilet_blocked": Scenario.PLUMBING_ISSUE,
    # ── POSTSTAY_FEEDBACK_AND_DISPUTE (count=4) ──
    "s8_388_guest_mentions_recurring_property_issue": Scenario.POSTSTAY_FEEDBACK_AND_DISPUTE,
    "s8_393_guest_claims_item_was_missing_before": Scenario.POSTSTAY_FEEDBACK_AND_DISPUTE,
    "s8_394_guest_says_cleaning_issue_ruined_stay": Scenario.POSTSTAY_FEEDBACK_AND_DISPUTE,
    "s8_398_guest_leaves_private_note_about_weak": Scenario.POSTSTAY_FEEDBACK_AND_DISPUTE,
    # ── PREARRIVAL_INFO_DISCLOSURE (count=4) ──
    "s2_55_guest_asks_for_full_address_immediately": Scenario.PREARRIVAL_INFO_DISCLOSURE,
    "s2_79_guest_asks_for_early_access_to": Scenario.PREARRIVAL_INFO_DISCLOSURE,
    "s3_103_guest_asks_for_wifi_password_before": Scenario.PREARRIVAL_INFO_DISCLOSURE,
    "s3_121_guest_asks_for_exact_address_before": Scenario.PREARRIVAL_INFO_DISCLOSURE,
    # ── PRIVACY_CONCERN (count=4) ──
    "s5_243_guest_reports_camera_or_privacy_concern": Scenario.PRIVACY_CONCERN,
    "s5_276_guest_requests_privacy_noentry_during_stay": Scenario.PRIVACY_CONCERN,
    "s8_413_guest_reports_privacy_concern_after_stay": Scenario.PRIVACY_CONCERN,
    "s8_414_guest_reports_suspected_camera_after_stay": Scenario.PRIVACY_CONCERN,
    # ── PROPERTY_FEATURE_INQUIRY (count=3) ──
    "s3_128_guest_asks_if_windows_have_blackout": Scenario.PROPERTY_FEATURE_INQUIRY,
    "s3_130_guest_asks_for_accessible_entrance_details": Scenario.PROPERTY_FEATURE_INQUIRY,
    "s3_137_guest_asks_for_workspace_deskchair_confirmation": Scenario.PROPERTY_FEATURE_INQUIRY,
    # ── PROPERTY_INFO_REQUEST (count=26) ──
    "s1_14_guest_asks_about_jacuzzi_usage_rules": Scenario.PROPERTY_INFO_REQUEST,
    "s1_15_guest_asks_about_standard_checkin_time": Scenario.PROPERTY_INFO_REQUEST,
    "s1_20_guest_asks_if_children_are_allowed": Scenario.PROPERTY_INFO_REQUEST,
    "s1_25_guest_asks_whether_the_area_is": Scenario.PROPERTY_INFO_REQUEST,
    "s1_26_guest_asks_for_exact_address_before": Scenario.PROPERTY_INFO_REQUEST,
    "s1_29_guest_asks_to_visit_the_property": Scenario.PROPERTY_INFO_REQUEST,
    "s1_31_guest_asks_if_cameras_exist_inside": Scenario.PROPERTY_INFO_REQUEST,
    "s1_33_guest_asks_about_accessibility_or_stepfree": Scenario.PROPERTY_INFO_REQUEST,
    "s1_34_guest_asks_about_wifi_speed_for": Scenario.PROPERTY_INFO_REQUEST,
    "s1_38_guest_asks_if_smoking_is_allowed": Scenario.PROPERTY_INFO_REQUEST,
    "s1_39_guest_asks_if_they_can_host": Scenario.PROPERTY_INFO_REQUEST,
    "s1_40_guest_asks_for_quiet_hours_policy": Scenario.PROPERTY_INFO_REQUEST,
    "s1_42_guest_asks_if_property_is_suitable": Scenario.PROPERTY_INFO_REQUEST,
    "s1_43_guest_asks_if_property_is_suitable": Scenario.PROPERTY_INFO_REQUEST,
    "s1_46_guest_asks_for_photos_or_video": Scenario.PROPERTY_INFO_REQUEST,
    "s1_47_guest_asks_for_building_floor_and": Scenario.PROPERTY_INFO_REQUEST,
    "s1_9_guest_asks_for_party_or_event": Scenario.PROPERTY_INFO_REQUEST,
    "s2_80_guest_asks_for_house_rules_summary": Scenario.PROPERTY_INFO_REQUEST,
    "s2_84_guest_asks_for_remote_work_setup": Scenario.PROPERTY_INFO_REQUEST,
    "s2_90_guest_asks_whether_security_cameras_are": Scenario.PROPERTY_INFO_REQUEST,
    "s3_141_guest_asks_if_elevator_is_working": Scenario.PROPERTY_INFO_REQUEST,
    "s4_177_guest_cannot_reach_elevator": Scenario.PROPERTY_INFO_REQUEST,
    "s5_227_guest_asks_jacuzzi_instructions": Scenario.PROPERTY_INFO_REQUEST,
    "s5_263_guest_asks_whether_they_can_host": Scenario.PROPERTY_INFO_REQUEST,
    "s5_292_guest_asks_for_cannabis_or_prohibited": Scenario.PROPERTY_INFO_REQUEST,
    "s5_293_guest_asks_if_smoking_on_balcony": Scenario.PROPERTY_INFO_REQUEST,
    # ── PROPERTY_READINESS_CHECK (count=3) ──
    "s3_109_guest_asks_if_pool_is_ready": Scenario.PROPERTY_READINESS_CHECK,
    "s3_134_guest_asks_whether_they_can_enter": Scenario.PROPERTY_READINESS_CHECK,
    "s3_97_guest_asks_if_cleaning_is_finished": Scenario.PROPERTY_READINESS_CHECK,
    # ── PROXY_BOOKING_RISK (count=1) ──
    "s1_50_guest_asks_if_local_resident_can": Scenario.PROXY_BOOKING_RISK,
    # ── QUALITY_ACCEPTANCE (count=2) ──
    "s9_426_guest_says_vendor_issue_is_not": Scenario.QUALITY_ACCEPTANCE,
    "s9_440_inspector_approves_property_with_minor_issue": Scenario.QUALITY_ACCEPTANCE,
    # ── REFUND_REQUEST (count=7) ──
    "s4_198_guest_asks_for_immediate_refund_at": Scenario.REFUND_REQUEST,
    "s5_235_guest_asks_for_refund_during_stay": Scenario.REFUND_REQUEST,
    "s5_269_guest_asks_for_early_checkout_and": Scenario.REFUND_REQUEST,
    "s7_346_guest_asks_for_refund_after_stay": Scenario.REFUND_REQUEST,
    "s8_381_guest_asks_for_refund_after_checkout": Scenario.REFUND_REQUEST,
    "s8_411_guest_says_refund_promised_by_staff": Scenario.REFUND_REQUEST,
    "s9_451_finance_needs_approval_for_refund": Scenario.REFUND_REQUEST,
    # ── REPEAT_BOOKING (count=4) ──
    "s8_382_guest_asks_to_book_again": Scenario.REPEAT_BOOKING,
    "s8_400_guest_asks_if_property_will_be": Scenario.REPEAT_BOOKING,
    "s8_415_guest_wants_loyalty_offer_for_repeat": Scenario.REPEAT_BOOKING,
    "s8_416_guest_asks_to_block_same_unit": Scenario.REPEAT_BOOKING,
    # ── RESERVATION_DATA_GAP (count=7) ──
    "s2_51_instant_booking_confirmed_with_complete_guest": Scenario.RESERVATION_DATA_GAP,
    "s2_52_booking_confirmed_but_guest_has_incomplete": Scenario.RESERVATION_DATA_GAP,
    "s2_53_booking_confirmed_and_guest_asks_what": Scenario.RESERVATION_DATA_GAP,
    "s2_68_reservation_imported_from_pms_with_missing": Scenario.RESERVATION_DATA_GAP,
    "s2_69_reservation_imported_from_pms_with_missing": Scenario.RESERVATION_DATA_GAP,
    "s2_70_reservation_imported_with_missing_arrival_time": Scenario.RESERVATION_DATA_GAP,
    "s2_86_guest_says_booking_was_made_for": Scenario.RESERVATION_DATA_GAP,
    # ── REVIEW_MANAGEMENT (count=7) ──
    "s4_170_guest_is_angry_and_threatens_bad": Scenario.REVIEW_MANAGEMENT,
    "s8_378_guest_leaves_good_review": Scenario.REVIEW_MANAGEMENT,
    "s8_379_guest_leaves_bad_review": Scenario.REVIEW_MANAGEMENT,
    "s8_386_pm_wants_to_request_review": Scenario.REVIEW_MANAGEMENT,
    "s8_387_pm_wants_to_respond_to_public": Scenario.REVIEW_MANAGEMENT,
    "s8_402_guest_requests_review_removal_or_change": Scenario.REVIEW_MANAGEMENT,
    "s8_403_owner_asks_why_review_score_dropped": Scenario.REVIEW_MANAGEMENT,
    # ── SAFETY_EMERGENCY (count=12) ──
    "s4_184_guest_says_alarm_is_ringing": Scenario.SAFETY_EMERGENCY,
    "s4_185_guest_says_smoke_detector_is_beeping": Scenario.SAFETY_EMERGENCY,
    "s4_190_guest_reports_safety_concern_at_entrance": Scenario.SAFETY_EMERGENCY,
    "s4_207_guest_says_there_is_someone_inside": Scenario.SAFETY_EMERGENCY,
    "s4_208_guest_says_property_appears_occupied": Scenario.SAFETY_EMERGENCY,
    "s4_209_guest_reports_gas_smell": Scenario.SAFETY_EMERGENCY,
    "s5_241_guest_asks_for_medical_help": Scenario.SAFETY_EMERGENCY,
    "s5_261_guest_reports_neighbor_harassment": Scenario.SAFETY_EMERGENCY,
    "s5_262_guest_reports_suspicious_person_near_property": Scenario.SAFETY_EMERGENCY,
    "s5_294_guest_reports_appliance_sparks_or_electrical": Scenario.SAFETY_EMERGENCY,
    "s5_295_guest_reports_carbon_monoxide_alarm": Scenario.SAFETY_EMERGENCY,
    "s8_412_guest_reports_injury_after_stay": Scenario.SAFETY_EMERGENCY,
    # ── SAFETY_SECURITY_CONCERN (count=3) ──
    "s4_189_guest_says_windows_or_doors_do": Scenario.SAFETY_SECURITY_CONCERN,
    "s5_242_guest_reports_safety_or_security_concern": Scenario.SAFETY_SECURITY_CONCERN,
    "s5_244_guest_reports_pest_or_insect_issue": Scenario.SAFETY_SECURITY_CONCERN,
    # ── SECURITY_DEPOSIT (count=8) ──
    "s1_24_guest_asks_whether_security_deposit_is": Scenario.SECURITY_DEPOSIT,
    "s2_75_guest_asks_if_deposit_can_be": Scenario.SECURITY_DEPOSIT,
    "s3_115_guest_has_not_paid_security_deposit": Scenario.SECURITY_DEPOSIT,
    "s3_150_guest_asks_to_delay_security_deposit": Scenario.SECURITY_DEPOSIT,
    "s7_348_guest_asks_about_deposit_return": Scenario.SECURITY_DEPOSIT,
    "s7_372_guest_asks_whether_deposit_is_held": Scenario.SECURITY_DEPOSIT,
    "s8_392_guest_asks_for_deposit_return_status": Scenario.SECURITY_DEPOSIT,
    "s9_461_security_deposit_provider_failed": Scenario.SECURITY_DEPOSIT,
    # ── SPECIAL_REQUEST (count=8) ──
    "s2_65_guest_asks_for_birthday_or_anniversary": Scenario.SPECIAL_REQUEST,
    "s5_273_guest_asks_for_baby_equipment_midstay": Scenario.SPECIAL_REQUEST,
    "s6_314_baby_equipment_fee_request": Scenario.SPECIAL_REQUEST,
    "s6_315_romantic_setup_request": Scenario.SPECIAL_REQUEST,
    "s6_316_birthday_setup_request": Scenario.SPECIAL_REQUEST,
    "s6_318_breakfast_or_grocery_package_request": Scenario.SPECIAL_REQUEST,
    "s6_334_baby_stroller_rental": Scenario.SPECIAL_REQUEST,
    "s6_336_private_chef_request": Scenario.SPECIAL_REQUEST,
    # ── TAX_INQUIRY (count=2) ──
    "s3_149_guest_asks_about_city_tax_or": Scenario.TAX_INQUIRY,
    "s5_281_guest_asks_for_city_tax_explanation": Scenario.TAX_INQUIRY,
    # ── UTILITY_OUTAGE (count=8) ──
    "s4_164_guest_says_there_is_no_electricity": Scenario.UTILITY_OUTAGE,
    "s4_165_guest_says_there_is_no_water": Scenario.UTILITY_OUTAGE,
    "s5_252_guest_says_wifi_is_slow": Scenario.UTILITY_OUTAGE,
    "s5_253_guest_says_wifi_is_completely_down": Scenario.UTILITY_OUTAGE,
    "s5_254_guest_asks_for_router_reset_instructions": Scenario.UTILITY_OUTAGE,
    "s5_255_guest_says_hot_water_intermittent": Scenario.UTILITY_OUTAGE,
    "s5_258_guest_says_appliance_tripped_fuse": Scenario.UTILITY_OUTAGE,
    "s5_259_guest_says_power_outage_affects_whole": Scenario.UTILITY_OUTAGE,
    # ── VENDOR_DISPATCH (count=5) ──
    "s9_423_vendor_asks_for_more_information": Scenario.VENDOR_DISPATCH,
    "s9_424_vendor_changes_arrival_time": Scenario.VENDOR_DISPATCH,
    "s9_425_vendor_says_issue_is_resolved": Scenario.VENDOR_DISPATCH,
    "s9_442_vendor_requests_guest_phone_number": Scenario.VENDOR_DISPATCH,
    "s9_443_vendor_cannot_enter_property_due_to": Scenario.VENDOR_DISPATCH,
    # ── VENDOR_NEGOTIATION (count=2) ──
    "s9_441_vendor_quote_exceeds_approval_threshold": Scenario.VENDOR_NEGOTIATION,
    "s9_464_recurring_vendor_delay_pattern_detected": Scenario.VENDOR_NEGOTIATION,
    # ── VISITOR_OCCUPANCY_POLICY (count=3) ──
    "s1_8_guest_asks_to_bring_more_people": Scenario.VISITOR_OCCUPANCY_POLICY,
    "s3_106_guest_asks_if_they_can_invite": Scenario.VISITOR_OCCUPANCY_POLICY,
    "s3_146_guest_asks_for_quiethours_reminder_for": Scenario.VISITOR_OCCUPANCY_POLICY,
}


# Pattern -> Scenario enum.  Order matters: most specific first.
# The slug format is ``s{stage}_{N}_{snake_case_title}`` so we only
# pattern-match on the title segment (everything after the first
# numeric prefix).  Anchors are forgiving so future catalog edits
# that rename slugs slightly keep working without code churn.
_SLUG_TO_SCENARIO: Final[tuple[tuple[re.Pattern[str], Scenario], ...]] = (
    # ── booking-modification family (must precede check-in / check-out) ──
    (
        re.compile(
            r"(?:extend_stay|stay_additional|extra_night|"
            r"booking_extension|stay_one_more|stay_extra)",
        ),
        Scenario.BOOKING_EXTENSION,
    ),
    (
        re.compile(r"cancel|cancellation"),
        Scenario.CANCELLATION_REQUEST,
    ),
    (
        re.compile(
            r"(?:late_check.?out|checkout_later|stay_until|"
            r"leave_later)",
        ),
        Scenario.LATE_CHECKOUT,
    ),
    (
        re.compile(
            r"(?:early_check.?in|arrive_before_checkin|"
            r"check_in_earlier|early_arrival|"
            r"arrive_very_late|late_arrival|flight_delayed|"
            r"arrival_time_changed)",
        ),
        Scenario.EARLY_CHECKIN,
    ),
    # ── access codes ─────────────────────────────────────────────── #
    (
        re.compile(
            r"(?:access_code|door_code|key_code|smart_lock_code|"
            r"entry_code|building_code)",
        ),
        Scenario.ACCESS_CODE_RELEASE,
    ),
    # ── physical things in the apartment ─────────────────────────── #
    (
        re.compile(
            r"(?:sofa_bed|extra_bed|bed_count|additional_bed|"
            r"fold.*bed|crib|baby_cot)",
        ),
        Scenario.EXTRA_BED_REQUEST,
    ),
    (
        re.compile(
            r"(?:amenity|towel|toiletries|coffee_machine|kettle|"
            r"hairdryer|iron|missing_item)",
        ),
        Scenario.AMENITY_EXCEPTION,
    ),
    # ── operational ──────────────────────────────────────────────── #
    (
        re.compile(
            r"(?:maintenance|not_working|broken|leak|construction|"
            r"repair|water_pressure|heating|ac_not_working)",
        ),
        Scenario.MAINTENANCE_REQUEST,
    ),
    (
        re.compile(
            r"(?:cleaner|cleaning_team|cleaning_request|"
            r"clean_room|housekeeping)",
        ),
        Scenario.CLEANER_DISPATCH,
    ),
    # ── policy negotiation ───────────────────────────────────────── #
    (
        re.compile(
            r"(?:guest_count|extra_guest|more_guests|"
            r"additional_person|visitor|bring_friend)",
        ),
        Scenario.GUEST_COUNT_MISMATCH,
    ),
    (re.compile(r"parking"), Scenario.PARKING_REQUEST),
    (
        re.compile(
            r"(?:_pet_|_pets_|_dog_|_cat_|"
            r"^pet_|^pets_|^dog_|^cat_|"
            r"_pet$|_pets$|_dog$|_cat$|pet_policy)",
        ),
        Scenario.PET_POLICY_EXCEPTION,
    ),
    # ── complaints / refunds (specific first, generic last) ───────── #
    (
        re.compile(
            r"(?:discount|price_lower|cheap|negotiat|"
            r"price_drop|reduce_price)",
        ),
        Scenario.DISCOUNT_REQUEST,
    ),
    (
        re.compile(r"(?:noise|loud|disturb|party_next_door)"),
        Scenario.NOISE_COMPLAINT,
    ),
    (
        re.compile(r"(?:damage|broken_window|broken_door|stain)"),
        Scenario.DAMAGE_REPORT,
    ),
    (
        re.compile(
            r"(?:complaint|unhappy|refund_because|compensation|"
            r"review_negative|disappointed)",
        ),
        Scenario.COMPLAINT_COMPENSATION,
    ),
    # ── post-stay ────────────────────────────────────────────────── #
    (
        re.compile(
            r"(?:lost_item|left.*behind|left_item|forgot_item|"
            r"forgot_belong|forgotten_in_apartment|"
            r"lost.*apartment)",
        ),
        Scenario.LOST_ITEM,
    ),
)


def derive_scenario_from_foundation_slug(
    slug: str | None,
) -> Scenario | None:
    """Return the :class:`Scenario` for ``slug`` or ``None`` when ambiguous.

    Args:
        slug: A foundation_scenario_id such as
            ``"s1_16_guest_asks_for_early_checkin_before_arrival"``.
            ``None`` or empty string ⇒ ``None``.

    Returns:
        The matching :class:`Scenario` enum value.  ``None`` when
        nothing matches — callers must keep their current
        :pyattr:`Scenario.GENERAL` fallback in that case.

    Lookup order:

    1. :data:`_FOUNDATION_SLUG_OVERRIDES` — the per-slug catalog
       map that the Aybüke 2026-05-18 coverage expansion built
       from the 469 hospitality scenarios.  Exact match wins
       immediately (lowercased).
    2. :data:`_SLUG_TO_SCENARIO` — the regex fallback that
       catches catalog drift (a freshly-added scenario that does
       not yet have an explicit override still gets a coarse
       bucket via the title keywords).

    The function is intentionally pure (no I/O) so it can be
    called on every case-build without measurable overhead.  Dict
    lookup is O(1); regex iteration is bounded by the table
    length (~17 entries).
    """
    if not slug:
        return None
    lowered = slug.lower()
    override = _FOUNDATION_SLUG_OVERRIDES.get(lowered)
    if override is not None:
        return override
    for pattern, scenario in _SLUG_TO_SCENARIO:
        if pattern.search(lowered):
            return scenario
    return None
