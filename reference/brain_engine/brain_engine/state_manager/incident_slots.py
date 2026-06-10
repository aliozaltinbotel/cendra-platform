"""Incident Slots - Data slot definitions for Airbnb incident tracking.

Defines all slots needed across the incident resolution lifecycle:
late checkout negotiation, cleaner coordination, photo inspection,
and damage claim processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from brain_engine.state_manager.slot_manager import SlotManager, SlotInfo


@dataclass
class IncidentSlots:
    """All slots for the Airbnb incident resolution domain.

    Organized by sub-flow with clear naming conventions.
    Each field maps to a SlotInfo that gets registered with the SlotManager.

    Attributes:
        -- Guest & Booking --
        guest_name: Name of the current guest (e.g., "John").
        guest_phone: Guest's phone number for contact.
        booking_id: Airbnb booking reference ID.
        property_id: Property identifier.

        -- Late Checkout --
        john_checkout_time: Requested late checkout time for John.
        george_checkin_time: Next guest George's scheduled check-in time.
        standard_checkout_time: Standard checkout time (default 11:00 AM).
        late_checkout_fee: Fee agreed for late checkout ($50 for 1-2h, $100 for 2-4h).
        fee_agreed: Whether the guest has agreed to the late checkout fee.
        late_checkout_approved: Whether the late checkout was approved.
        checkout_extension_hours: Number of hours of late checkout granted.

        -- Cleaner Coordination --
        cleaner_name: Name of the assigned cleaner.
        cleaner_phone: Cleaner's contact phone number.
        cleaner_confirmed: Whether the cleaner has confirmed availability.
        cleaner_arrival_time: Confirmed arrival time of the cleaner.
        cleaner_eta_minutes: Estimated minutes until cleaner arrives.
        cleaning_duration_minutes: Expected cleaning duration in minutes.
        cleaning_completed: Whether the cleaning is finished.
        cleaning_notes: Notes from the cleaner about the job.

        -- Photo Inspection --
        photos_received: Whether before/after photos have been received.
        photos_before_count: Number of before photos on file.
        photos_after_count: Number of after photos received.
        damage_detected: Whether damage was detected in photo analysis.
        damage_description: Description of detected damage.
        damage_severity: Severity score (1-5, where 5 is most severe).
        damage_location: Where in the property the damage is located.
        damage_items: List of specific items damaged.
        analysis_confidence: Confidence score of the photo analysis (0.0-1.0).

        -- Damage Claim --
        claim_submitted: Whether the claim has been submitted to Airbnb.
        claim_id: Airbnb claim reference number.
        claim_amount: Dollar amount of the claim.
        claim_status: Current status of the claim (draft, submitted, under_review, approved, denied).
        claim_deadline: Deadline for claim submission (24h from checkout).
        evidence_complete: Whether all required evidence has been collected.
        host_statement: Host's written statement for the claim.
        repair_estimate: Estimated repair cost.
        replacement_cost: Cost to replace damaged items.

        -- Incident Meta --
        incident_id: Unique identifier for this incident.
        incident_status: Overall incident status (open, in_progress, resolved, escalated).
        incident_created_at: When the incident was first created.
        incident_resolved_at: When the incident was resolved.
        escalation_reason: Reason for escalation if applicable.
        resolution_summary: Summary of how the incident was resolved.
    """

    # Guest & Booking
    guest_name: str | None = None
    guest_phone: str | None = None
    booking_id: str | None = None
    property_id: str | None = None

    # Late Checkout
    john_checkout_time: str | None = None
    george_checkin_time: str | None = None
    standard_checkout_time: str = "11:00 AM"
    late_checkout_fee: float | None = None
    fee_agreed: bool | None = None
    late_checkout_approved: bool | None = None
    checkout_extension_hours: int | None = None

    # Cleaner Coordination
    cleaner_name: str | None = None
    cleaner_phone: str | None = None
    cleaner_confirmed: bool | None = None
    cleaner_arrival_time: str | None = None
    cleaner_eta_minutes: int | None = None
    cleaning_duration_minutes: int | None = None
    cleaning_completed: bool | None = None
    cleaning_notes: str | None = None

    # Photo Inspection
    photos_received: bool | None = None
    photos_before_count: int | None = None
    photos_after_count: int | None = None
    damage_detected: bool | None = None
    damage_description: str | None = None
    damage_severity: int | None = None
    damage_location: str | None = None
    damage_items: list[str] = field(default_factory=list)
    analysis_confidence: float | None = None

    # Damage Claim
    claim_submitted: bool | None = None
    claim_id: str | None = None
    claim_amount: float | None = None
    claim_status: str | None = None
    claim_deadline: str | None = None
    evidence_complete: bool | None = None
    host_statement: str | None = None
    repair_estimate: float | None = None
    replacement_cost: float | None = None

    # Incident Meta
    incident_id: str | None = None
    incident_status: str | None = None
    incident_created_at: str | None = None
    incident_resolved_at: str | None = None
    escalation_reason: str | None = None
    resolution_summary: str | None = None


# --------------------------------------------------------------------------- #
# Slot definitions for the SlotManager
# --------------------------------------------------------------------------- #

SLOT_DEFINITIONS: list[SlotInfo] = [
    # Guest & Booking
    SlotInfo(name="guest_name", required=True, description="Name of the current guest"),
    SlotInfo(name="guest_phone", required=False, description="Guest phone number"),
    SlotInfo(name="booking_id", required=True, description="Airbnb booking reference ID"),
    SlotInfo(name="property_id", required=True, description="Property identifier"),

    # Late Checkout
    SlotInfo(name="john_checkout_time", required=False, description="Requested late checkout time"),
    SlotInfo(name="george_checkin_time", required=False, description="Next guest check-in time"),
    SlotInfo(name="standard_checkout_time", value="11:00 AM", required=False, description="Standard checkout time"),
    SlotInfo(name="late_checkout_fee", required=False, description="Fee for late checkout"),
    SlotInfo(name="fee_agreed", required=False, description="Whether guest agreed to the fee"),
    SlotInfo(name="late_checkout_approved", required=False, description="Whether late checkout is approved"),
    SlotInfo(name="checkout_extension_hours", required=False, description="Hours of extension granted"),

    # Cleaner Coordination
    SlotInfo(name="cleaner_name", required=False, description="Assigned cleaner name"),
    SlotInfo(name="cleaner_phone", required=False, description="Cleaner phone number"),
    SlotInfo(name="cleaner_confirmed", required=False, description="Cleaner availability confirmed"),
    SlotInfo(name="cleaner_arrival_time", required=False, description="Cleaner arrival time"),
    SlotInfo(name="cleaner_eta_minutes", required=False, description="Minutes until cleaner arrives"),
    SlotInfo(name="cleaning_duration_minutes", required=False, description="Expected cleaning duration"),
    SlotInfo(name="cleaning_completed", required=False, description="Whether cleaning is done"),
    SlotInfo(name="cleaning_notes", required=False, description="Notes from cleaner"),

    # Photo Inspection
    SlotInfo(name="photos_received", required=False, description="Whether photos have been received"),
    SlotInfo(name="photos_before_count", required=False, description="Number of before photos"),
    SlotInfo(name="photos_after_count", required=False, description="Number of after photos"),
    SlotInfo(name="damage_detected", required=False, description="Whether damage was found"),
    SlotInfo(name="damage_description", required=False, description="Description of damage"),
    SlotInfo(name="damage_severity", required=False, description="Severity score 1-5"),
    SlotInfo(name="damage_location", required=False, description="Location of damage in property"),
    SlotInfo(name="damage_items", required=False, description="List of damaged items"),
    SlotInfo(name="analysis_confidence", required=False, description="Photo analysis confidence 0-1"),

    # Damage Claim
    SlotInfo(name="claim_submitted", required=False, description="Claim submitted to Airbnb"),
    SlotInfo(name="claim_id", required=False, description="Airbnb claim reference number"),
    SlotInfo(name="claim_amount", required=False, description="Claim dollar amount"),
    SlotInfo(name="claim_status", required=False, description="Claim status"),
    SlotInfo(name="claim_deadline", required=False, description="Claim submission deadline"),
    SlotInfo(name="evidence_complete", required=False, description="All evidence collected"),
    SlotInfo(name="host_statement", required=False, description="Host written statement"),
    SlotInfo(name="repair_estimate", required=False, description="Estimated repair cost"),
    SlotInfo(name="replacement_cost", required=False, description="Replacement cost"),

    # Incident Meta
    SlotInfo(name="incident_id", required=False, description="Incident unique identifier"),
    SlotInfo(name="incident_status", required=False, description="Overall incident status"),
    SlotInfo(name="incident_created_at", required=False, description="Incident creation time"),
    SlotInfo(name="incident_resolved_at", required=False, description="Incident resolution time"),
    SlotInfo(name="escalation_reason", required=False, description="Reason for escalation"),
    SlotInfo(name="resolution_summary", required=False, description="Resolution summary"),
]


def build_incident_slot_manager() -> SlotManager:
    """Create a SlotManager pre-loaded with all incident slot definitions.

    Returns:
        A SlotManager instance with all Airbnb incident slots registered.
    """
    manager = SlotManager(slots=SLOT_DEFINITIONS)
    return manager
