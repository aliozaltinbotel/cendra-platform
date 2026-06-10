# ruff: noqa: RUF001
# RUF001 (ambiguous unicode) is suppressed file-wide because the
# alias strings are reproduced verbatim from the xlsx workbook.
# Quoting characters such as the right-single-quote codepoint are
# intentional source fidelity,
# not stylistic drift, and must not be silently rewritten.
"""173 canonical Key Data fields registry from the xlsx workbook.

The Botel Guest Journey workbook (sheet ``Key Data fields``) lists
the canonical names — and known aliases — of every datum the
property-manager playbook expects from the PMS, the property profile,
or the guest record.  Examples: ``reservation_record``,
``deposit_amount``, ``los_thresholds``, ``identity_policy``,
``psp_currencies``.

The audit reference (`.context/coverage_audit.md`) documented "174
fields" by counting workbook rows including the header; the actual
data-row count is 173, captured by :data:`EXPECTED_FIELD_COUNT`.

Each entry is a frozen :class:`DataField` row:

* ``category``    — workbook grouping (e.g. ``"Reservation &
  Availability"``, ``"Pricing & Deposits"``)
* ``canonical``   — the canonical field name; this is the registry
  key and the term the rest of the codebase should reference
* ``aliases``     — alternative names that downstream PMS feeds use
  for the same field
* ``pms_source``  — example PMS source path (sparse: only 30/173
  rows carry this); ``None`` when the workbook did not specify
* ``importance``  — 1-5 prioritisation score from the workbook;
  fields with ``importance >= 4`` are critical for the AI's
  decision quality and should not be left absent at runtime

The audit notes that ~20/133 critical (importance ≥ 4) fields appear
by name in the codebase today; this registry is the single
authoritative inventory the rest of the engine can compare against.

Drift guard: the import fails fast when xlsx row count drifts from
:data:`EXPECTED_FIELD_COUNT`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DATA_FIELDS",
    "DATA_FIELDS_BY_CANONICAL",
    "EXPECTED_FIELD_COUNT",
    "DataField",
    "fields_by_category",
    "fields_by_importance",
    "lookup",
    "lookup_by_alias",
]


@dataclass(frozen=True, slots=True)
class DataField:
    """One row of the xlsx ``Key Data fields`` sheet."""

    category: str
    canonical: str
    aliases: tuple[str, ...]
    pms_source: str | None
    importance: int


DATA_FIELDS: Final[tuple[DataField, ...]] = (
    DataField(
        category="Reservation & Availability",
        canonical="reservation_record",
        aliases=("reservation record", "booking details"),
        pms_source="reservation JSON",
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="property_identifier",
        aliases=("property_name", "unit_id"),
        pms_source="propertyId",
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="stay_dates",
        aliases=("check_in_date", "check_out_date"),
        pms_source="arrival, departure",
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="guest_counts",
        aliases=("adults", "children", "infants", "guests"),
        pms_source="occupancy.adults/children/infants",
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="min_stay",
        aliases=("minimum_stay", "LOS minimum"),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="max_occupancy",
        aliases=("occupancy_limits",),
        pms_source="personCapacity",
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="availability_calendar",
        aliases=("availability", "inventory"),
        pms_source="nightsCount / hasAvailability",
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="back_to_back_flag",
        aliases=("back-to-back booking flag", "same-day turn"),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="unit_adjacency",
        aliases=("preference for adjacent units", "adjacency feasibility"),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="repricing_rules",
        aliases=("availability & reprice policy",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="cancellation_policy_by_rate_plan",
        aliases=("Policy by rate plan", "cancel policy"),
        pms_source="cancellationPolicyId",
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="check_in_time",
        aliases=("standard check-in time",),
        pms_source="plannedArrival",
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="check_out_time",
        aliases=("standard check-out time",),
        pms_source="plannedDeparture",
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="access_release_window",
        aliases=("time gate", "release window"),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="access_system",
        aliases=("provider/app for codes/keys",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="key_return_method",
        aliases=("key drop/return instructions",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="access_auto_revoke_time",
        aliases=("auto-revoke time after checkout",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="grace_period",
        aliases=("grace period for entry/exit",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Times & Access",
        canonical="security_rules",
        aliases=("security/onsite handover rules",),
        pms_source="descriptions→ inferred",
        importance=5,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="turnover_calendar",
        aliases=("cleaning/turnover calendar",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="housekeeping_status",
        aliases=("live HK status", "readiness"),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="cleaning_SLA",
        aliases=("HK SLA", "service level"),
        pms_source="descriptions→ inferred",
        importance=4,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="cleaner_schedule",
        aliases=("cleaner rota/schedule",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="readiness_ETA",
        aliases=("ready time estimate",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="capacity_limits_per_service",
        aliases=("capacity caps per day/service",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="on_site_capacity",
        aliases=("on-site storage/ops capacity",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="rate_rules",
        aliases=("pricing rules",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="los_thresholds",
        aliases=("length-of-stay thresholds",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="lead_time_rules",
        aliases=("lead-time discount rules",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="floor_ADR",
        aliases=("minimum ADR guardrail",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="dynamic_rates",
        aliases=("current dynamic rates",),
        pms_source="pricingSettings.defaultDailyPrice",
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="rates_fees_taxes_currency",
        aliases=("Rates/fees/taxes", "currency"),
        pms_source="listingCurrency / currency",
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="fee_table_ECI",
        aliases=("early check-in fees",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="fee_table_LCO",
        aliases=("late check-out fees",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="fee_micro_extension",
        aliases=("micro-extension fee",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="addon_fee_table",
        aliases=("add-on fees", "groceries/crib/etc."),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="comp_policy_and_caps",
        aliases=("complimentary policy & caps",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="goodwill_caps",
        aliases=("goodwill credit caps",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="fines_policy",
        aliases=("fines schedule (noise/smoking/etc.)",),
        pms_source="descriptions→ inferred",
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="blackout_dates",
        aliases=("discount/offer blackout dates",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="min_LOS_for_offers",
        aliases=("min LOS for promo codes",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="invoice_capability",
        aliases=("can we issue invoices (Y/N)",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="legal_entity_fields",
        aliases=("company name", "VAT/GST", "address"),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="billing_profile",
        aliases=("saved billing details",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="tax_scheme",
        aliases=("VAT/GST scheme/class",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="final_folio",
        aliases=("closing folio document",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="folio_status",
        aliases=("paid/unpaid", "balance"),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="folio_breakdown",
        aliases=("line items", "evidence references"),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="invoice_tool",
        aliases=("invoicing system",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="invoice_link_or_email",
        aliases=("invoice delivery link/email",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="psp_allowed_methods",
        aliases=("allowed payment methods per property/currency",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="psp_currencies",
        aliases=("accepted currencies policy",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="psp_link_generator",
        aliases=("secure payment link generator",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="psp_payment_link",
        aliases=("pay/balance link",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="balance_due",
        aliases=("amount outstanding",),
        pms_source="remainingAmount",
        importance=5,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="payment_status",
        aliases=("paid/pending/failed",),
        pms_source="remainingAmount vs amount",
        importance=5,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="payment_retry_logic",
        aliases=("retry schedule & limits",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Payments (PSP)",
        canonical="psp_refund_SLA",
        aliases=("refund release SLA",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Deposits",
        canonical="deposit_required_flag",
        aliases=("deposit needed Y/N",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Deposits",
        canonical="deposit_amount",
        aliases=("deposit amount",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Deposits",
        canonical="deposit_method",
        aliases=("hold method (pre-auth/charge)",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Deposits",
        canonical="deposit_status",
        aliases=("set/cleared",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Deposits",
        canonical="deposit_release_SLA",
        aliases=("deposit release timeframe",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="identity_policy",
        aliases=("what’s accepted to identify guests",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="age_minimum",
        aliases=("minimum age",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="risk_flags",
        aliases=("party/stag risk indicators",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="reservation_contact_data",
        aliases=("email", "phone on booking"),
        pms_source="customers[].mail + phone",
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="two_factor_method",
        aliases=("2FA channel/method",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="kyc_provider_link",
        aliases=("KYC verification link",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="kyc_status",
        aliases=("KYC pass/fail/pending",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="adults_count",
        aliases=("number of adults to verify",),
        pms_source="occupancy.adults",
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="identity_match_rules",
        aliases=("match booking holder vs requester",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Access Control",
        canonical="lock_logs",
        aliases=("smart-lock logs",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Access Control",
        canonical="smart_lock_logs",
        aliases=("lock event history (alias)",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Access Control",
        canonical="backup_access_methods",
        aliases=("backup code", "remote unlock", "physical key"),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Access Control",
        canonical="lock_battery_telemetry",
        aliases=("telemetry (if any)",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Wayfinding & Guides",
        canonical="wayfinding_pack",
        aliases=("map/photos/video for entrance",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Wayfinding & Guides",
        canonical="building_entry_notes",
        aliases=("entry notes/instructions",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Wayfinding & Guides",
        canonical="checkin_pack_timegated",
        aliases=("check-in guide contents (time-gated)",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Wayfinding & Guides",
        canonical="quick_setup_snippets",
        aliases=("Wi-Fi/thermostat quick setup",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="address_property",
        aliases=("full address",),
        pms_source="address / street / city / state / zipCode / countryCode",
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="guidebook",
        aliases=("local guidebook content",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="amenity_map",
        aliases=("what’s included",),
        pms_source="amenities",
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="amenity_dataset",
        aliases=("detailed amenity specs (e.g.", "Wi-Fi speed)"),
        pms_source="amenities",
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="official_amenity_list",
        aliases=("source of truth for listed items",),
        pms_source="amenities",
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="house_rules_by_unit",
        aliases=("unit-specific rules",),
        pms_source="descriptions→ inferred",
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="accessibility_attributes",
        aliases=("stairs", "elevator", "door widths"),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="ev_charging_details",
        aliases=("EV charger availability & connector type",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="property_area",
        aliases=("property size", "sq.m", "sq.ft"),
        pms_source="areaSquareFeet",
        importance=4,
    ),
    DataField(
        category="Parking & Transport",
        canonical="parking_inventory",
        aliases=("on-site/assigned bays",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Parking & Transport",
        canonical="parking_spot_assignment_map",
        aliases=("spot map",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Parking & Transport",
        canonical="parking_height_limits",
        aliases=("bay/garage height limits",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Parking & Transport",
        canonical="partner_storage_directory",
        aliases=("partner storage list & hours",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Parking & Transport",
        canonical="liability_text_storage",
        aliases=("liability disclaimer for storage",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Partners & Add-ons",
        canonical="addon_inventory",
        aliases=("crib", "high-chair", "stair gate", "etc."),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Partners & Add-ons",
        canonical="addon_cutoff_times",
        aliases=("order/booking cutoffs",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Partners & Add-ons",
        canonical="partner_API_details",
        aliases=("API/vendor endpoints for transfers/groceries",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Partners & Add-ons",
        canonical="partner_availability",
        aliases=("capacity/operating hours",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Partners & Add-ons",
        canonical="seat_types",
        aliases=("child seats", "boosters"),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Partners & Add-ons",
        canonical="pickup_window",
        aliases=("transfer pickup time window",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Partners & Add-ons",
        canonical="partner_pricing",
        aliases=("pricing from partner",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Allergy & Special Needs",
        canonical="hypoallergenic_inventory",
        aliases=("feather-free/unscented sets",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Allergy & Special Needs",
        canonical="cleaning_notes_allergy",
        aliases=("special cleaning notes",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Allergy & Special Needs",
        canonical="allergy_escalation_path",
        aliases=("who/how to escalate",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Allergy & Special Needs",
        canonical="medical_power_refrigeration",
        aliases=("outlet availability", "mini-fridge", "extensions"),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Parcels & Storage",
        canonical="storage_capacity",
        aliases=("parcel/luggage capacity",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Parcels & Storage",
        canonical="storage_hours",
        aliases=("opening hours",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Parcels & Storage",
        canonical="parcel_acceptance_rules",
        aliases=("what’s allowed/not allowed",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Connectivity",
        canonical="isp_status",
        aliases=("ISP network status",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Connectivity",
        canonical="router_status",
        aliases=("local router status",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Connectivity",
        canonical="speed_thresholds",
        aliases=("min acceptable Mbps",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Connectivity",
        canonical="monitor_timers",
        aliases=("stability timers",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Connectivity",
        canonical="router_reboot_steps",
        aliases=("safe reboot steps",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Maintenance",
        canonical="device_telemetry",
        aliases=("HVAC/appliances telemetry",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Maintenance",
        canonical="triage_script",
        aliases=("procedures to triage issues",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Maintenance",
        canonical="vendor_SLA",
        aliases=("maintenance vendor SLA",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Maintenance",
        canonical="temp_equipment_policy",
        aliases=("temporary heaters/fans policy",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Maintenance",
        canonical="parts_inventory",
        aliases=("available parts",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Maintenance",
        canonical="technician_SLA",
        aliases=("tech visit SLA",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Noise & Conduct",
        canonical="quiet_hours",
        aliases=("quiet times",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Noise & Conduct",
        canonical="noise_policy",
        aliases=("noise rules", "monitoring"),
        pms_source="descriptions→ inferred",
        importance=4,
    ),
    DataField(
        category="Noise & Conduct",
        canonical="relocation_rules",
        aliases=("when relocation allowed",),
        pms_source="descriptions→ inferred",
        importance=4,
    ),
    DataField(
        category="Noise & Conduct",
        canonical="violation_log",
        aliases=("warnings/violations history",),
        pms_source="descriptions→ inferred",
        importance=4,
    ),
    DataField(
        category="Smoking",
        canonical="smoking_zones",
        aliases=("where smoking allowed",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Smoking",
        canonical="smoking_detection",
        aliases=("detectors/policies",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Smoking",
        canonical="smoking_fines",
        aliases=("fine amounts/evidence policy",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Utilities & Outages",
        canonical="building_status",
        aliases=("utility outage status",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Utilities & Outages",
        canonical="utility_contacts",
        aliases=("provider contact details",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Utilities & Outages",
        canonical="outage_SLA",
        aliases=("response/restore SLA",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Utilities & Outages",
        canonical="outage_comp_tiers",
        aliases=("comp tiers for outages",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Check-out & Post",
        canonical="staff_handover_rules",
        aliases=("cleaner handover", "exit rules"),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Check-out & Post",
        canonical="lost_and_found_log",
        aliases=("HK notes", "L&F system"),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Check-out & Post",
        canonical="shipping_options_costs",
        aliases=("L&F shipping options & costs",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Check-out & Post",
        canonical="micro_extension_rules",
        aliases=("back-to-back flag", "cleaner capacity", "micro fee"),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Check-out & Post",
        canonical="inspection_status",
        aliases=("post-stay inspection status",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Check-out & Post",
        canonical="incident_log",
        aliases=("incidents/damages log",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Retention & Offers",
        canonical="crm_tier",
        aliases=("guest tier",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Retention & Offers",
        canonical="crm_status",
        aliases=("customer status",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Retention & Offers",
        canonical="promo_rules",
        aliases=("eligibility", "stacking", "blackout", "min LOS"),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Compliance & Privacy",
        canonical="dsr_workflow",
        aliases=("data subject request workflow",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Compliance & Privacy",
        canonical="identity_verification_for_DSR",
        aliases=("DSR identity check rules",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Compliance & Privacy",
        canonical="dsr_SLA",
        aliases=("DSR timelines",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Safety",
        canonical="safety_keywords_trigger",
        aliases=("terms that force escalation",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Safety",
        canonical="emergency_playbook",
        aliases=("emergency handling steps",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Safety",
        canonical="local_emergency_numbers",
        aliases=("police/fire/medical numbers",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Safety",
        canonical="on_call_roster",
        aliases=("after-hours contacts/rota",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Support",
        canonical="contact_roster",
        aliases=("onsite contact list",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Support",
        canonical="support_hours_matrix",
        aliases=("language & hours matrix",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Insurance",
        canonical="coverage_policy",
        aliases=("insurance/coverage summary",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Insurance",
        canonical="coverage_provider",
        aliases=("provider details",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Audit & Logging",
        canonical="rule_version",
        aliases=("active rule version at decision time",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Audit & Logging",
        canonical="decision_inputs_snapshot",
        aliases=("input values used for decision",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Audit & Logging",
        canonical="decision_timestamps",
        aliases=("timestamps for audit trail",),
        pms_source=None,
        importance=2,
    ),
    DataField(
        category="Media",
        canonical="media_library",
        aliases=("property photos", "floorplans", "videos"),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Safety",
        canonical="neighbourhood_safety_notes",
        aliases=("safety of area", "night safety notes"),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Safety",
        canonical="camera_locations",
        aliases=("locations of external CCTV/cameras",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Compliance & Privacy",
        canonical="camera_policy",
        aliases=("camera policy", "what is recorded"),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Maintenance",
        canonical="pest_treatment_policy",
        aliases=("pest control policy", "when/how"),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="cleaning_follow_up_SLA",
        aliases=("SLA for follow-up cleaning",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Billing & Invoicing",
        canonical="payment_provider_logs",
        aliases=("PSP logs visible to ops",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Property Info",
        canonical="construction_schedule_notes",
        aliases=("known nearby works schedule",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Access Control",
        canonical="safe_type",
        aliases=("safe model/type", "override method"),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Access Control",
        canonical="safe_override_procedure",
        aliases=("how to open safe if code forgotten",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Property Info",
        canonical="workspace_attributes",
        aliases=("desk", "chair", "monitor suitability"),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Media",
        canonical="photos_workspace",
        aliases=("photos of workspace/desk area",),
        pms_source=None,
        importance=3,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="channel_pricing_rules",
        aliases=("pricing rules per channel",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="direct_pricing_rules",
        aliases=("direct-only pricing rules",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Pricing & Billing",
        canonical="parity_policy",
        aliases=("rate parity policy across channels",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Identity & KYC",
        canonical="kyc_requirements",
        aliases=("what ID is needed for check-in",),
        pms_source=None,
        importance=5,
    ),
    DataField(
        category="Operations (HK & Turnover)",
        canonical="house_care_policy",
        aliases=("policy on furniture moves", "wear & tear"),
        pms_source="agreementtext → inferred",
        importance=4,
    ),
    DataField(
        category="Reservation & Availability",
        canonical="hold_policy",
        aliases=("hold without payment rules", "soft hold rules"),
        pms_source="agreementtext → inferred",
        importance=5,
    ),
    DataField(
        category="Compliance & Privacy",
        canonical="address_disclosure_policy",
        aliases=("when to share exact address",),
        pms_source=None,
        importance=4,
    ),
    DataField(
        category="Noise & Conduct",
        canonical="night_noise_notes",
        aliases=("bars/traffic noise at night",),
        pms_source="agreementtext → inferred",
        importance=4,
    ),
)


DATA_FIELDS_BY_CANONICAL: Final[Mapping[str, DataField]] = {
    f.canonical: f for f in DATA_FIELDS
}


# Workbook drift guard.  Source: xlsx ``Key Data fields`` sheet had
# 173 data rows on 2026-05-04 (canonical names are unique).
EXPECTED_FIELD_COUNT: Final = 173

if len(DATA_FIELDS) != EXPECTED_FIELD_COUNT:
    raise RuntimeError(
        f"data_fields_registry: expected {EXPECTED_FIELD_COUNT} "
        f"entries, got {len(DATA_FIELDS)}; xlsx drift detected"
    )

if len(DATA_FIELDS_BY_CANONICAL) != EXPECTED_FIELD_COUNT:
    raise RuntimeError(
        "data_fields_registry: duplicate canonical name detected"
    )


def lookup(canonical: str) -> DataField | None:
    """Return the field with the given ``canonical`` name."""
    return DATA_FIELDS_BY_CANONICAL.get(canonical)


def lookup_by_alias(name: str) -> DataField | None:
    """Resolve any alias (or the canonical name) to a :class:`DataField`.

    Returns ``None`` when ``name`` matches neither a canonical key
    nor any alias.  The first canonical match wins, then aliases are
    scanned in registry order.
    """
    direct = DATA_FIELDS_BY_CANONICAL.get(name)
    if direct is not None:
        return direct
    for f in DATA_FIELDS:
        if name in f.aliases:
            return f
    return None


def fields_by_category(category: str) -> tuple[DataField, ...]:
    """Return all fields whose ``category`` matches exactly."""
    return tuple(f for f in DATA_FIELDS if f.category == category)


def fields_by_importance(minimum: int) -> tuple[DataField, ...]:
    """Return all fields with ``importance >= minimum``.

    Pass ``minimum=4`` to get the 133 critical fields the playbook
    flags as essential for runtime decisions.
    """
    return tuple(f for f in DATA_FIELDS if f.importance >= minimum)
