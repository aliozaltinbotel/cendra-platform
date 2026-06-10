"""Property Intents - Airbnb property management intent definitions.

Extends the base Intent enum with domain-specific intents for
Airbnb property management, guest communication, and incident handling.
"""

from enum import StrEnum

from brain_engine.intent_controller.intents import Intent


class PropertyIntent(StrEnum):
    """Airbnb property management intents.

    Extends the base Intent categories with domain-specific intents
    for property management operations. Maps back to base Intent
    categories where applicable.

    Usage:
        >>> intent = PropertyIntent.LATE_CHECKOUT_REQUEST
        >>> intent.to_base_intent()
        <Intent.REQUEST: 'request'>
    """

    # Guest communication intents
    GUEST_COMPLAINT = "guest_complaint"
    GUEST_CHECKIN = "guest_checkin"
    GUEST_CHECKOUT = "guest_checkout"
    GUEST_QUESTION = "guest_question"
    GUEST_FEEDBACK = "guest_feedback"

    # Checkout & scheduling
    LATE_CHECKOUT_REQUEST = "late_checkout_request"
    EARLY_CHECKIN_REQUEST = "early_checkin_request"
    CHECKOUT_CONFIRMATION = "checkout_confirmation"

    # Incident & damage
    DAMAGE_REPORTED = "damage_reported"
    DAMAGE_CLAIM_UPDATE = "damage_claim_update"
    INCIDENT_ESCALATION = "incident_escalation"
    INCIDENT_RESOLUTION = "incident_resolution"

    # Photos & inspection
    PHOTO_RECEIVED = "photo_received"
    PHOTO_REQUEST = "photo_request"
    INSPECTION_COMPLETE = "inspection_complete"

    # Cleaner & maintenance
    CLEANER_UPDATE = "cleaner_update"
    CLEANER_DISPATCH = "cleaner_dispatch"
    CLEANER_COMPLETED = "cleaner_completed"
    MAINTENANCE_REQUEST = "maintenance_request"
    MAINTENANCE_COMPLETE = "maintenance_complete"

    # Booking management
    BOOKING_INQUIRY = "booking_inquiry"
    BOOKING_MODIFICATION = "booking_modification"
    BOOKING_CANCELLATION = "booking_cancellation"

    # System / fallback
    UNKNOWN = "unknown"
    GREETING = "greeting"
    FAREWELL = "farewell"

    def to_base_intent(self) -> Intent:
        """Map this property intent to a base Intent category.

        Returns:
            The closest matching base Intent.
        """
        mapping: dict[PropertyIntent, Intent] = {
            PropertyIntent.GUEST_COMPLAINT: Intent.COMPLAINT,
            PropertyIntent.GUEST_CHECKIN: Intent.ACTION,
            PropertyIntent.GUEST_CHECKOUT: Intent.ACTION,
            PropertyIntent.GUEST_QUESTION: Intent.INFO,
            PropertyIntent.GUEST_FEEDBACK: Intent.FEEDBACK,
            PropertyIntent.LATE_CHECKOUT_REQUEST: Intent.REQUEST,
            PropertyIntent.EARLY_CHECKIN_REQUEST: Intent.REQUEST,
            PropertyIntent.CHECKOUT_CONFIRMATION: Intent.CONFIRMATION,
            PropertyIntent.DAMAGE_REPORTED: Intent.COMPLAINT,
            PropertyIntent.DAMAGE_CLAIM_UPDATE: Intent.INFO,
            PropertyIntent.INCIDENT_ESCALATION: Intent.ACTION,
            PropertyIntent.INCIDENT_RESOLUTION: Intent.ACTION,
            PropertyIntent.PHOTO_RECEIVED: Intent.ACTION,
            PropertyIntent.PHOTO_REQUEST: Intent.REQUEST,
            PropertyIntent.INSPECTION_COMPLETE: Intent.ACTION,
            PropertyIntent.CLEANER_UPDATE: Intent.INFO,
            PropertyIntent.CLEANER_DISPATCH: Intent.ACTION,
            PropertyIntent.CLEANER_COMPLETED: Intent.ACTION,
            PropertyIntent.MAINTENANCE_REQUEST: Intent.REQUEST,
            PropertyIntent.MAINTENANCE_COMPLETE: Intent.ACTION,
            PropertyIntent.BOOKING_INQUIRY: Intent.INFO,
            PropertyIntent.BOOKING_MODIFICATION: Intent.REQUEST,
            PropertyIntent.BOOKING_CANCELLATION: Intent.CANCELLATION,
            PropertyIntent.UNKNOWN: Intent.UNKNOWN,
            PropertyIntent.GREETING: Intent.GREETING,
            PropertyIntent.FAREWELL: Intent.FAREWELL,
        }
        return mapping.get(self, Intent.UNKNOWN)

    @classmethod
    def from_string(cls, value: str) -> "PropertyIntent":
        """Parse an intent from a raw string, falling back to UNKNOWN.

        Args:
            value: Raw string to parse (case-insensitive).

        Returns:
            The matched PropertyIntent, or UNKNOWN if no match.
        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.UNKNOWN

    @classmethod
    def all_values(cls) -> list[str]:
        """Return all intent values as a list of strings."""
        return [member.value for member in cls]
