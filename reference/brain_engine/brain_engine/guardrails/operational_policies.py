# ruff: noqa: RUF001
# RUF001 (ambiguous unicode) is suppressed file-wide because the
# policy strings are reproduced verbatim from the xlsx workbook.
# Quoting characters such as the right-single-quote codepoint are
# intentional source fidelity,
# not stylistic drift, and must not be silently rewritten.
"""47 botel operational policy registry — distinct from output guardrails.

The Botel Guest Journey workbook (sheet
``botel_guardrails_with_status``) catalogues 46 operational policies
that govern how the property manager handles concrete guest scenarios:
ECI/LCO promises, deposit pre-auths, invoice/VAT obligations, refund
scopes and so on.  These policies are **operational** — they shape
*what the AI is allowed to commit to* — and are categorically distinct
from :mod:`brain_engine.guardrails.customer_guardrails`, which holds
the eight *output* guardrails (no fabrication, no bold formatting,
match guest language, …) used by the output-validation cascade.

The audit reference (`.context/coverage_audit.md`) documented "47
policies" by counting workbook rows including the header; the actual
data-row count is 46, captured by :data:`EXPECTED_POLICY_COUNT`.

Each policy is a frozen :class:`OperationalPolicy` row mirroring the
xlsx columns:

* ``title``                — display name (also the registry key)
* ``stage``                — raw lifecycle stage string from the
  workbook; intentionally kept as a free-form label rather than
  collapsed onto :class:`~brain_engine.patterns.models.BookingStage`,
  because some policies tag themselves "Global (all stages)" or
  "Pre-checkout (48h)" which do not map cleanly onto the 9-stage enum
* ``statuses``             — booking statuses where the policy fires
  (e.g. ``"inquiry"``, ``"in_house"``, ``"checkout"``)
* ``trigger_keywords``     — example phrases that activate the policy
* ``text``                 — the policy text itself; this is the
  prompt-injection-safe rule the AI must follow
* ``variables_needed``     — placeholders / data hooks the policy
  references (free-form names, e.g. ``"{check_in_time}"``,
  ``"deposit_amount"``)
* ``review_notes``         — workbook reviewer comments, if any
* ``conflict_resolution``  — how to behave when this policy conflicts
  with another rule, if specified

Drift guard: the import fails fast when xlsx row count drifts from
:data:`EXPECTED_POLICY_COUNT`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    "EXPECTED_POLICY_COUNT",
    "POLICIES",
    "POLICIES_BY_TITLE",
    "OperationalPolicy",
    "format_policies_for_prompt",
    "lookup",
    "policies_for_stage",
    "policies_for_status",
]


@dataclass(frozen=True, slots=True)
class OperationalPolicy:
    """One row of the xlsx ``botel_guardrails_with_status`` sheet."""

    title: str
    stage: str
    statuses: tuple[str, ...]
    trigger_keywords: tuple[str, ...]
    text: str
    variables_needed: tuple[str, ...]
    review_notes: str | None
    conflict_resolution: str | None


POLICIES: Final[tuple[OperationalPolicy, ...]] = (
    OperationalPolicy(
        title="Early Check-in / Late Check-out Enquiry",
        stage="Pre-booking",
        statuses=(
            "inquiry",
            "follow_up",
            "inquiryPreapproved",
            "inquirypreapproved",
            "inquirynotpossible",
            "confirmed",
        ),
        trigger_keywords=(
            "early check-in",
            "early checkin",
            "check in at 12",
            "late checkout",
            "late check-out",
            "check out at 2",
        ),
        text="If the guest asks about early check-in or late check-out, reply that standard check-in is {check_in_time} and checkout is {check_out_time}. Do not promise early check-in or late check-out. Offer to check availability after booking and explain that approval depends on cleaning schedule and a fee where applicable.",
        variables_needed=(
            "{check_in_time}",
            "{check_out_time}",
            "cleaning schedule reference",
            "early check-in fee policy",
            "late check-out fee policy",
        ),
        review_notes="Potential conflict when Status=inquiry* forbids sharing specific details but guest asks about timing.",
        conflict_resolution="Share only standard times and policy; avoid links/codes/addresses for inquiry statuses.",
    ),
    OperationalPolicy(
        title="Discount / Deal Eligibility",
        stage="Pre-booking",
        statuses=(
            "inquiry",
            "follow_up",
            "inquiryPreapproved",
            "inquirypreapproved",
            "inquirynotpossible",
            "confirmed",
            "unknown",
        ),
        trigger_keywords=(
            "discount",
            "promo code",
            "best price",
            "long-stay discount",
            "returning guest rate",
            "corporate rate",
        ),
        text="If the guest asks for a discount, promo, or best price, never create custom prices. State that discounts apply only per published rules (e.g., LOS, lead time, loyalty tier). If asked pre-booking, present the eligible, published options only; otherwise say no discounts apply.",
        variables_needed=(
            "LOS discount rules",
            "lead-time rules",
            "loyalty tier rules",
            "coupon engine config",
            "blackout dates",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Invoice/VAT Promise (B2B)",
        stage="Pre-booking",
        statuses=(
            "inquiry",
            "follow_up",
            "inquiryPreapproved",
            "inquirypreapproved",
            "inquirynotpossible",
            "confirmed",
        ),
        trigger_keywords=(
            "VAT invoice",
            "tax invoice",
            "GST invoice",
            "company invoice",
            "business invoice",
        ),
        text="If asked whether tax invoices are provided, state we can issue an invoice only after payment is received and only with complete company details. Do not commit to special tax treatments; route unusual requests to human support.",
        variables_needed=(
            "invoice capability",
            "legal entity fields required",
            "payment status",
            "supported tax schemes",
            "finance escalation contact",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Payment Method Fit / Deposit Existence",
        stage="Pre-booking",
        statuses=(
            "inquiry",
            "follow_up",
            "inquiryPreapproved",
            "inquirypreapproved",
            "inquirynotpossible",
            "confirmed",
        ),
        trigger_keywords=(
            "pay with Amex",
            "PayPal accepted",
            "bank transfer",
            "cash payment",
            "deposit required",
            "security deposit amount",
        ),
        text="If asked whether a payment method or security deposit is accepted, answer only using the allowed list for this property. Do not offer offline or unlisted methods. If a deposit is required, state amount and that it’s pre-auth/held via PSP; do not guarantee waivers.",
        variables_needed=(
            "allowed payment methods by property",
            "{deposit_amount}",
            "deposit type (pre-auth/hold)",
            "PSP policy link",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Guest Detail Edit (Names/Headcount)",
        stage="Booking confirmation",
        statuses=("confirmed", "modified"),
        trigger_keywords=(
            "change guest name",
            "add guest",
            "update headcount",
            "extra person",
        ),
        text="If a guest asks to change names or guest count, accept only if within occupancy and policy window. If change increases occupancy beyond base, state the additional fee and do not confirm until guest accepts the fee.",
        variables_needed=(
            "max occupancy",
            "base occupancy",
            "extra guest fee table",
            "change window policy",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Invoice Issuance (Post-Payment)",
        stage="Booking confirmation",
        statuses=("confirmed", "modified"),
        trigger_keywords=("send invoice", "need receipt", "tax invoice now"),
        text="If asked to send an invoice/receipt, issue only for paid bookings. If payment is pending, instruct guest to complete payment first. For B2B details, collect full legal information; do not alter tax class after invoice issuance.",
        variables_needed=(
            "payment status",
            "invoice generation link",
            "required company fields",
            "tax class rules",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Check-in Guide Timing / Access Info",
        stage="Booking confirmation",
        statuses=("confirmed", "modified"),
        trigger_keywords=(
            "door code",
            "keys",
            "check-in instructions",
            "access details",
        ),
        text="If the guest requests check-in instructions, door codes, or exact keys before release time, do not share codes. State that access details are released after ID verification + balance paid + deposit set and within the standard release window.",
        variables_needed=(
            "release window timing",
            "ID verification status/link",
            "balance status",
            "deposit status",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Reservation Modification (Dates/Unit)",
        stage="Pre-arrival",
        statuses=("confirmed", "modified"),
        trigger_keywords=(
            "change dates",
            "move apartment",
            "switch unit",
            "modify reservation",
        ),
        text="If asked to change dates or move units, follow the modify policy: only if availability allows and per repricing rules. Never confirm a lower price than the recalculated rate. If non-refundable/late window, explain fees first.",
        variables_needed=(
            "availability calendar",
            "repricing rules",
            "cancellation & change policy",
            "NRF/late window definition",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Add-ons (Transfers, Crib, High-chair, Groceries)",
        stage="Pre-arrival",
        statuses=("confirmed", "arriving_in_two_days", "check_in_tomorrow"),
        trigger_keywords=(
            "airport transfer",
            "need a crib",
            "high chair",
            "grocery pack",
            "add-on service",
        ),
        text="If the guest requests an add-on, offer only items on the approved list and only within cutoff/stock limits. Quote the fee and do not confirm until guest explicitly agrees to the price.",
        variables_needed=(
            "approved add-ons list",
            "pricing per item",
            "cutoff windows",
            "stock/capacity",
            "booking link",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Balance Payment / Pre-auth",
        stage="Pre-arrival",
        statuses=("confirmed", "arriving_in_two_days", "check_in_tomorrow"),
        trigger_keywords=(
            "pay balance now",
            "payment link",
            "deposit hold",
            "card failed",
        ),
        text="If asked to pay remaining balance now or to confirm deposit, provide a secure payment link only. Do not accept card details via chat. If payment fails, provide the retry flow and warn that access cannot be released without successful payment.",
        variables_needed=(
            "secure payment link",
            "PSP retry flow",
            "balance due amount",
            "payment status",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="ID / KYC Verification",
        stage="Pre-arrival",
        statuses=("confirmed", "arriving_in_two_days", "check_in_tomorrow"),
        trigger_keywords=(
            "ID upload",
            "passport photo okay?",
            "verification link",
            "KYC",
        ),
        text="If asked about ID requirements, state that all adult guests must complete ID verification via the secure link. Do not accept photos or attachments in chat as verification.",
        variables_needed=(
            "ID/KYC provider link",
            "list of accepted IDs",
            "verification status",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Parcel Acceptance Before Arrival",
        stage="Pre-arrival",
        statuses=("confirmed", "arriving_in_two_days"),
        trigger_keywords=(
            "deliver package before arrival",
            "receive parcel",
            "Amazon delivery to property",
        ),
        text="If the guest asks to deliver a parcel before arrival, allow only non-perishable, non-valuable items and only within storage capacity and staff hours. Otherwise decline politely and suggest third-party lockers.",
        variables_needed=(
            "storage capacity",
            "staff hours",
            "allowed items policy",
            "nearby locker partners",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Early Check-in Decision (Operational)",
        stage="Pre-check-in (48h)",
        statuses=("arriving_in_two_days", "check_in_tomorrow"),
        trigger_keywords=(
            "arrive early tomorrow",
            "early check-in today",
            "room ready early",
        ),
        text="If the guest asks for early check-in within 48h, do not approve automatically. Offer to check readiness; communicate that approval depends on cleaning completion and that a fee may apply. Never approve if there’s a back-to-back turnover.",
        variables_needed=(
            "turnover calendar",
            "cleaner schedule",
            "cleaning status",
            "early check-in fee table",
            "back-to-back detection",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Access Release (Codes/Keys)",
        stage="Pre-check-in (48h)",
        statuses=(
            "arriving_in_two_days",
            "check_in_tomorrow",
            "check_in_today",
        ),
        trigger_keywords=(
            "send code now",
            "key safe code",
            "digital key activation",
        ),
        text="If the guest requests the code/keys and it’s before the release window or missing a prerequisite (ID, payment, deposit), withhold access details and list the exact outstanding steps. When all checks pass and it’s within release time, share time-bound access (valid from {access_start} to {access_end}).",
        variables_needed=(
            "{access_start}",
            "{access_end}",
            "release window timing",
            "ID/payment/deposit status",
            "code provisioning method",
        ),
        review_notes="May conflict with status guidance that encourages sharing info at T-48h or T-24h.",
        conflict_resolution="At T-48/T-24 share checklist and arrival overview; share codes only within release window and after prerequisites are complete.",
    ),
    OperationalPolicy(
        title="Luggage Storage (Pre-arrival)",
        stage="Pre-check-in (48h)",
        statuses=("arriving_in_two_days", "check_in_tomorrow"),
        trigger_keywords=(
            "store bags before check-in",
            "drop luggage early",
            "bag storage options",
        ),
        text="If asked to store bags before check-in, offer only approved partner/storage options and only within operating hours. Do not promise onsite storage where none exists.",
        variables_needed=(
            "approved storage partners",
            "operating hours",
            "storage capacity",
            "pricing if any",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Access Failure Triage",
        stage="Check-in day",
        statuses=("check_in_today", "currently_hosting"),
        trigger_keywords=(
            "code not working",
            "can’t enter",
            "door won’t open",
            "lock error",
        ),
        text="If the guest says they can’t enter or code doesn’t work, first verify booking identity (name + last 4 digits of phone/email on res). Provide step-by-step retry and then a backup access method if available. Do not share master codes or permanent keys. If still failing, escalate to on-site support.",
        variables_needed=(
            "verification fields",
            "retry steps script",
            "backup access method",
            "on-site support contact",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Readiness Issue (Cleaner Inside / Not Ready)",
        stage="Check-in day",
        statuses=("check_in_today",),
        trigger_keywords=(
            "cleaner still inside",
            "apartment not ready",
            "still being cleaned",
        ),
        text="If the unit is not ready at check-in, apologize, give ETA, and offer the standard goodwill (e.g., luggage drop and a preset comp such as {comp_option}) within budget limits. Do not exceed comp caps; escalate if readiness exceeds {delay_threshold} minutes.",
        variables_needed=(
            "{comp_option}",
            "comp caps",
            "readiness ETA source",
            "{delay_threshold}",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Wayfinding Help",
        stage="Check-in day",
        statuses=("check_in_today",),
        trigger_keywords=(
            "can’t find entrance",
            "where is the door",
            "which building",
            "directions please",
        ),
        text="If the guest can’t find the entrance/unit, send the approved wayfinding pack (map/photos/video). Do not share unverified directions.",
        variables_needed=(
            "wayfinding pack link",
            "verified directions",
            "building/entrance notes",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Amenity Gap / Not as Described",
        stage="Post check-in (24h)",
        statuses=("currently_hosting",),
        trigger_keywords=(
            "missing amenity",
            "no kettle",
            "not as described",
            "where is the crib",
        ),
        text="If the guest reports a missing/incorrect amenity, check against the official amenity list. If listed, arrange replacement/fix; if not listed, clarify it’s not included. Offer preset goodwill only per policy; no open-ended promises.",
        variables_needed=(
            "official amenity list",
            "replacement workflow",
            "goodwill policy & caps",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Noise / Disturbance",
        stage="Post check-in (24h)",
        statuses=("currently_hosting",),
        trigger_keywords=(
            "noisy neighbors",
            "construction noise",
            "loud music",
            "disturbance",
        ),
        text="If the guest reports noise from neighbors/works, provide quiet-hours policy and mitigation steps. Offer relocation only if noise is severe and within relocation rules. Do not promise refunds beyond the policy caps.",
        variables_needed=(
            "quiet-hours policy",
            "mitigation steps",
            "relocation rules",
            "refund caps",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="HVAC / Critical Maintenance",
        stage="Post check-in (24h)",
        statuses=("currently_hosting",),
        trigger_keywords=(
            "AC not working",
            "heating broken",
            "no hot water",
            "boiler issue",
        ),
        text="If AC/heating fails, follow the triage script (reset steps). If unresolved, book technician per SLA and offer temporary equipment per policy. Never instruct guests to access restricted panels or hazardous equipment.",
        variables_needed=(
            "HVAC triage script",
            "technician SLA",
            "temporary equipment policy",
            "emergency escalation",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Mid-stay Services (Linen/Towels/Cleaning)",
        stage="In-stay",
        statuses=("currently_hosting",),
        trigger_keywords=(
            "extra towels",
            "mid-stay clean",
            "linen change",
            "housekeeping",
        ),
        text="If the guest requests extra linen/towels/cleaning, offer available slots and standard fees only. Confirm booking after guest accepts the fee. Cap same-day requests by capacity.",
        variables_needed=(
            "service menu",
            "price list",
            "scheduling slots",
            "same-day capacity limits",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Occupancy Change (Add Guest/Pet)",
        stage="In-stay",
        statuses=("currently_hosting",),
        trigger_keywords=(
            "friend joining",
            "add one more person",
            "bringing a pet",
            "dog allowed?",
        ),
        text="If the guest asks to add a person or pet, permit only within max occupancy and pet policy. Quote the additional fee if applicable and update house rules. Decline if property is pet-free or occupancy would be exceeded.",
        variables_needed=(
            "max occupancy",
            "pet policy",
            "extra guest/pet fee table",
            "house rules link",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Slow Internet / Connectivity Issue",
        stage="In-stay",
        statuses=("currently_hosting",),
        trigger_keywords=(
            "slow Wi-Fi",
            "internet down",
            "buffering",
            "poor connection",
        ),
        text="If the guest reports slow Wi-Fi, provide the approved troubleshooting steps and a self-test. Do not instruct factory resets. If speed remains below {min_speed_mbps} for {duration}, escalate to ISP/tech.",
        variables_needed=(
            "Wi-Fi troubleshooting script",
            "self-test link",
            "{min_speed_mbps}",
            "{duration}",
            "ISP/tech escalation contact",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Extend Stay",
        stage="In-stay",
        statuses=("currently_hosting",),
        trigger_keywords=(
            "extend stay",
            "add nights",
            "stay longer",
            "extra night",
        ),
        text="If the guest asks to extend, offer only available nights at current dynamic rates. Do not hold dates without payment. If extension risks disrupting the next turnover, decline or propose an alternative unit.",
        variables_needed=(
            "availability calendar",
            "current dynamic rates",
            "turnover calendar",
            "alternative unit list",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Emergency (Leak/Smoke/Medical)",
        stage="In-stay",
        statuses=("currently_hosting", "check_in_today", "check_out_today"),
        trigger_keywords=(
            "smell gas",
            "water leak",
            "fire",
            "smoke",
            "break-in",
            "medical emergency",
        ),
        text="If the guest mentions an emergency, immediately present emergency numbers and the on-call contact. Suspend any sales offers. Always escalate to human support.",
        variables_needed=(
            "local emergency numbers",
            "on-call contact",
            "escalation protocol",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Late Checkout Decision",
        stage="Pre-checkout (48h)",
        statuses=("currently_hosting", "check_out_tomorrow"),
        trigger_keywords=(
            "late checkout request",
            "check out later",
            "extend checkout",
        ),
        text="If the guest asks for late checkout, approve only if there is no same-day back-to-back booking and cleaners can accommodate. Quote the fee and limit maximum extension to {max_lco_hours}. If not possible, offer luggage storage options.",
        variables_needed=(
            "turnover calendar",
            "cleaner schedule",
            "late checkout fee table",
            "{max_lco_hours}",
            "storage partners",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Checkout Instructions",
        stage="Pre-checkout (48h)",
        statuses=("check_out_tomorrow",),
        trigger_keywords=(
            "checkout steps",
            "how to check out",
            "departure checklist",
        ),
        text="If the guest asks for checkout steps, send the standard departure checklist. Do not add ad-hoc requirements not listed in policy.",
        variables_needed=("departure checklist", "house rules link"),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Post-checkout Luggage Storage",
        stage="Pre-checkout (48h)",
        statuses=("check_out_tomorrow", "check_out_today"),
        trigger_keywords=(
            "store bags after checkout",
            "luggage after 11",
            "hold my bags",
        ),
        text="If the guest asks to store bags after checkout, only offer approved partners and hours; avoid implying you can hold items onsite if you can’t.",
        variables_needed=(
            "approved storage partners",
            "operating hours",
            "pricing if any",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Airport Transfer Booking",
        stage="Pre-checkout (48h)",
        statuses=("currently_hosting", "check_out_tomorrow", "post_stay"),
        trigger_keywords=(
            "airport pickup",
            "taxi to airport",
            "transfer booking",
        ),
        text="If the guest requests an airport transfer, present partner options, pickup window, and price, and book only after explicit confirmation. Decline if within partner cutoff.",
        variables_needed=(
            "transfer partners",
            "pricing",
            "pickup windows",
            "partner cutoff times",
            "booking link",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Key/Code Return & Lock-Up",
        stage="Checkout day",
        statuses=("check_out_today",),
        trigger_keywords=(
            "where to leave keys",
            "lock the door",
            "key drop",
            "code deactivation",
        ),
        text="If asked where to leave keys/how to lock, provide the standard key return steps and confirm that access will auto-revoke after checkout plus a {grace_period} buffer.",
        variables_needed=(
            "key return steps",
            "smart lock auto-revoke timing",
            "{grace_period}",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Lost & Found",
        stage="Checkout day",
        statuses=("check_out_today", "post_stay"),
        trigger_keywords=(
            "left my charger",
            "forgot item",
            "lost passport",
            "missing bag",
        ),
        text="If the guest reports a lost item, collect a precise description and search via the L&F log. Offer shipping at guest cost and state storage is limited to {storage_days}. Escalate for passports/IDs.",
        variables_needed=(
            "L&F log",
            "shipping process & rates",
            "{storage_days}",
            "escalation rules for IDs",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Micro-Extension (1–2h same day)",
        stage="Checkout day",
        statuses=("check_out_today",),
        trigger_keywords=(
            "need 1 more hour",
            "short extension",
            "2 hours late checkout",
        ),
        text="If the guest requests a short extension on checkout day, approve only if cleaners and calendar allow; apply micro-extension fee; never exceed {max_micro_hours}.",
        variables_needed=(
            "turnover calendar",
            "cleaner schedule",
            "micro-extension fee table",
            "{max_micro_hours}",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Deposit Refund Timing",
        stage="Post-checkout",
        statuses=("post_stay",),
        trigger_keywords=(
            "when deposit back",
            "refund deposit",
            "hold release timing",
        ),
        text="If asked when the deposit is returned, state that deposits are automatically released within {refund_sla_days} business days if no incident is recorded. Do not make instant-refund promises.",
        variables_needed=(
            "{refund_sla_days}",
            "incident log status",
            "PSP release policy",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Billing Dispute / Unknown Charge",
        stage="Post-checkout",
        statuses=("post_stay",),
        trigger_keywords=(
            "charged extra",
            "dispute charge",
            "incorrect fee",
            "billing issue",
        ),
        text="If the guest disputes a charge, share the folio breakdown and the policy basis (e.g., extra cleaning, late checkout). Offer goodwill only within preset caps. For chargeback-risk language (e.g., “I’ll dispute with bank”), escalate.",
        variables_needed=(
            "folio breakdown",
            "policy mapping for charges",
            "goodwill caps",
            "chargeback escalation process",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Loyalty / Return Discount",
        stage="Post-checkout",
        statuses=("post_stay",),
        trigger_keywords=(
            "returning guest discount",
            "loyalty code",
            "next stay offer",
        ),
        text="If asked for a future discount, issue only approved codes tied to blackout dates and min LOS. Do not stack with other offers.",
        variables_needed=(
            "approved loyalty codes",
            "blackout dates",
            "minimum LOS rules",
            "coupon engine config",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="GDPR / Data Deletion",
        stage="Post-checkout",
        statuses=("post_stay",),
        trigger_keywords=(
            "delete my data",
            "GDPR request",
            "data portability",
            "remove my info",
        ),
        text="If the guest requests data deletion or portability, acknowledge and initiate the DSR workflow. Do not promise immediate deletion in chat; verify identity first.",
        variables_needed=(
            "DSR workflow link",
            "verification steps",
            "privacy team contact",
            "SLA",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Identity & Privacy",
        stage="Global (all stages)",
        statuses=(
            "unknown",
            "inquiry",
            "follow_up",
            "inquiryPreapproved",
            "inquirypreapproved",
            "inquirynotpossible",
            "confirmed",
            "modified",
            "arriving_in_two_days",
            "check_in_tomorrow",
            "check_in_today",
            "currently_hosting",
            "check_out_tomorrow",
            "check_out_today",
            "post_stay",
            "cancelled",
            "no_show",
            "expired",
        ),
        trigger_keywords=(
            "send code to my friend",
            "change booking for me",
            "share my invoice to new email",
            "confirm my phone",
        ),
        text="If a message requests access, booking changes, payment actions, invoices, or personal data, first verify name + booking email/phone against the reservation. Do not share access codes or payment links with unverified contacts.",
        variables_needed=(
            "verification fields (name",
            "email/phone)",
            "reservation CRM lookup",
            "verification script",
        ),
        review_notes="May conflict with 'confirmed' status guidance to share detailed info.",
        conflict_resolution="Always require identity verification before any sensitive details, regardless of status.",
    ),
    OperationalPolicy(
        title="Payments & Links",
        stage="Global (all stages)",
        statuses=("all statuses",),
        trigger_keywords=(
            "here is my card number",
            "can I wire to you directly",
            "send bank details",
            "pay in cash",
        ),
        text="Never accept card numbers, photos of cards, or bank details in chat. Share only secure payment links or PSP flows.",
        variables_needed=(
            "secure payment link generator",
            "PSP hosted flow URLs",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Operational Capacity",
        stage="Global (all stages)",
        statuses=("all statuses",),
        trigger_keywords=(
            "same-day deep clean",
            "daily housekeeping every day",
            "10pm cleaning",
            "multiple cribs now",
        ),
        text="Never promise services beyond cleaning/partner capacity caps for the date.",
        variables_needed=(
            "capacity caps per service/date",
            "partner availability",
            "scheduler access",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Policy Boundaries",
        stage="Global (all stages)",
        statuses=("all statuses",),
        trigger_keywords=(
            "12 people can stay?",
            "bring my cat?",
            "smoke on balcony?",
            "throw party?",
        ),
        text="Never exceed max occupancy, pet restrictions, or house rules in any response.",
        variables_needed=("max occupancy", "pet policy", "house rules link"),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Safety First",
        stage="Global (all stages)",
        statuses=("all statuses",),
        trigger_keywords=(
            "fire",
            "smoke",
            "gas",
            "leak",
            "injury",
            "break-in",
            "emergency",
        ),
        text="If a message contains safety keywords, immediately present emergency contacts and the on-call contact. Always escalate and stop non-essential conversation.",
        variables_needed=(
            "local emergency numbers",
            "on-call contact",
            "safety escalation protocol",
        ),
        review_notes=None,
        conflict_resolution=None,
    ),
    OperationalPolicy(
        title="Status: inquiry preapproved (channel exceptions)",
        stage="Pre-booking",
        # The SECURITY clause covers every initial-contact status,
        # not only the preapproved variants — plain "inquiry" gets
        # the same no-sensitive-details rule.
        statuses=(
            "inquiry",
            "inquiryPreapproved",
            "inquirypreapproved",
            "inquirynotpossible",
        ),
        trigger_keywords=("N/A (status-driven)",),
        text="Initial contact phase, no confirmed booking. SECURITY: Never share sensitive property details (Wi-Fi passwords, lock codes, GPS, map links, exact address). Do not share links/booking URLs EXCEPT Airbnb links when channel=Airbnb. Provide only general area descriptions and high-level amenities/availability; avoid exact check-in details and exact location.",
        variables_needed=(
            "channel detection (Airbnb)",
            "link sanitizer",
            "sensitive-field masker",
        ),
        review_notes="Consolidates previous variants that disagreed on link sharing.",
        conflict_resolution="Implement channel-based allowlist so only Airbnb links pass when source channel is Airbnb; all other links removed.",
    ),
    OperationalPolicy(
        title="Neighbourhood Safety Enquiry",
        stage="Pre-booking",
        statuses=("inquiry", "follow_up", "confirmed"),
        trigger_keywords=(
            "safe at night",
            "safety of area",
            "walk back at night",
            "neighbourhood safe",
            "area safe",
        ),
        text="Share only factual, up-to-date information about the neighbourhood (lighting, typical activity, distance from main roads). Avoid absolute guarantees of safety. Encourage guests to follow standard personal safety practices and use local emergency numbers if needed.",
        variables_needed=(
            "neighbourhood_safety_notes",
            "local_emergency_numbers",
        ),
        review_notes=None,
        conflict_resolution="If neighbourhood_safety_notes conflict with marketing copy, defer to the most conservative wording and flag to ops/marketing.",
    ),
    OperationalPolicy(
        title="Camera / CCTV Transparency",
        stage="Pre-booking",
        statuses=("inquiry", "follow_up", "confirmed"),
        trigger_keywords=(
            "camera",
            "cctv",
            "surveillance",
            "recording",
            "security camera",
        ),
        text="Be transparent about the presence and location of any cameras, and confirm that there are no cameras in private spaces such as bedrooms or bathrooms. Reference camera_policy and house_rules. Do not share live feeds or sensitive security details. Emphasize that cameras, where present, are for safety and compliance only.",
        variables_needed=(
            "camera_locations",
            "camera_policy",
            "house_rules",
            "privacy_policy",
        ),
        review_notes=None,
        conflict_resolution="If camera information differs across channels, default to the most conservative disclosure and flag to ops/marketing.",
    ),
    OperationalPolicy(
        title="House Care / Cleanliness & Pests",
        stage="In-stay (anytime)",
        statuses=("in_house", "follow_up", "checkout"),
        trigger_keywords=(
            "dirty",
            "cleanliness",
            "bugs",
            "cockroach",
            "ant",
            "pest",
            "spill",
            "stain",
            "sofa",
            "smell",
        ),
        text="Always apologize and acknowledge the issue. Offer re-clean or treatment within capacity and according to cleaning_follow_up_SLA and pest_treatment_policy. Avoid promising full refunds by default; follow coverage_policy and any photo/inspection requirements before offering compensation.",
        variables_needed=(
            "house_care_policy",
            "pest_treatment_policy",
            "cleaning_follow_up_SLA",
            "coverage_policy",
        ),
        review_notes=None,
        conflict_resolution="If coverage_policy for cleanliness/pests is unclear, choose the lowest-risk partial comp option and flag for manual review.",
    ),
)


POLICIES_BY_TITLE: Final[Mapping[str, OperationalPolicy]] = {
    p.title: p for p in POLICIES
}


# Workbook drift guard.  Source: xlsx ``botel_guardrails_with_status``
# sheet had 46 data rows on 2026-05-04.
EXPECTED_POLICY_COUNT: Final = 46

if len(POLICIES) != EXPECTED_POLICY_COUNT:
    raise RuntimeError(
        f"operational_policies: expected {EXPECTED_POLICY_COUNT} "
        f"entries, got {len(POLICIES)}; xlsx drift detected"
    )


def lookup(title: str) -> OperationalPolicy | None:
    """Return the policy with the given ``title`` or ``None`` if absent."""
    return POLICIES_BY_TITLE.get(title)


def policies_for_stage(stage: str) -> tuple[OperationalPolicy, ...]:
    """Return policies whose ``stage`` field matches exactly.

    The match is case-sensitive and exact — workbook stage labels are
    stable enough that fuzzy matching would hide drift instead of
    surfacing it.  Callers that need cross-stage scoping should
    intersect with :data:`POLICIES` directly.
    """
    return tuple(p for p in POLICIES if p.stage == stage)


def policies_for_status(status: str) -> tuple[OperationalPolicy, ...]:
    """Return policies whose ``statuses`` tuple contains ``status``.

    Status values come from the booking PMS (``inquiry``, ``confirmed``,
    ``in_house``, …) — see the xlsx ``Status`` column for the full set.
    Matching is case-insensitive so callers can forward the raw PMS
    label ("Inquiry", "InquiryPreapproved") without first lowercasing
    it; empty or blank inputs return an empty tuple.
    """
    needle = (status or "").strip().lower()
    if not needle:
        return ()
    return tuple(
        p
        for p in POLICIES
        if any(known.lower() == needle for known in p.statuses)
    )


def format_policies_for_prompt(
    policies: tuple[OperationalPolicy, ...],
) -> str:
    """Render selected policies as an LLM-readable instructions block.

    The block is added near the other guardrail text so the model
    treats the operational rules as hard constraints, not soft
    advice.  Each policy is rendered as a bullet ``- **<title>**:
    <text>`` — the title acts as a stable retrieval anchor for
    downstream telemetry / debugging.

    Returns an empty string when ``policies`` is empty so the
    assembled prompt stays byte-identical for callers that hit no
    matching rule (status-less requests, unknown statuses).
    """
    if not policies:
        return ""
    lines = [
        "## Operational Policies (active for this reservation status)",
    ]
    for p in policies:
        lines.append(f"- **{p.title}**: {p.text}")
    return "\n".join(lines)
