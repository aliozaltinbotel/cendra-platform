"""Upsell Engine — auto-detect and offer revenue opportunities.

Implements 4 upsell types from Cendra platform:
    1. Gap Night    — fill empty nights between bookings with discounted rates
    2. Early Check-in — offer check-in before standard time
    3. Late Check-out — extend stay past checkout time
    4. Late Check-in  — accommodate guests arriving after standard hours

Each upsell is scored by guest loyalty, property availability,
and revenue potential. Brain Engine auto-generates offers for
high-value guests and escalates borderline cases to PM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UpsellOffer:
    """A single upsell offer for a guest.

    Attributes:
        upsell_type: One of gap_night, early_checkin, late_checkout, late_checkin.
        description: Human-readable offer description.
        original_price: Normal price for this service.
        offer_price: Discounted price for this offer.
        discount_pct: Discount percentage applied.
        auto_applicable: Whether Brain Engine can offer automatically.
        revenue_potential: Estimated additional revenue (USD).
        confidence: How confident we are the guest will accept (0-1).
    """

    upsell_type: str
    description: str
    original_price: float = 0.0
    offer_price: float = 0.0
    discount_pct: float = 0.0
    auto_applicable: bool = False
    revenue_potential: float = 0.0
    confidence: float = 0.5


@dataclass(slots=True)
class UpsellResult:
    """Complete upsell analysis for a booking.

    Attributes:
        reservation_id: Booking identifier.
        property_id: Property identifier.
        guest_score: Guest loyalty score (0-100).
        offers: List of applicable upsell offers.
        total_revenue_potential: Sum of all offer revenues.
        message_to_guest: Pre-built offer message.
        actions: MCP actions to execute offers.
    """

    reservation_id: str
    property_id: str
    guest_score: int = 0
    offers: list[UpsellOffer] = field(default_factory=list)
    total_revenue_potential: float = 0.0
    message_to_guest: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        return {
            "reservation_id": self.reservation_id,
            "property_id": self.property_id,
            "guest_score": self.guest_score,
            "offers": [
                {
                    "upsell_type": o.upsell_type,
                    "description": o.description,
                    "original_price": o.original_price,
                    "offer_price": o.offer_price,
                    "discount_pct": o.discount_pct,
                    "auto_applicable": o.auto_applicable,
                    "revenue_potential": o.revenue_potential,
                    "confidence": o.confidence,
                }
                for o in self.offers
            ],
            "total_revenue_potential": self.total_revenue_potential,
        }


@dataclass(frozen=True, slots=True)
class BookingContext:
    """Booking data needed for upsell analysis.

    Attributes:
        reservation_id: Booking identifier.
        property_id: Property identifier.
        checkin_date: Check-in date.
        checkout_date: Check-out date.
        checkin_time: Standard check-in time (e.g., "14:00").
        checkout_time: Standard checkout time (e.g., "10:00").
        nightly_rate: Per-night price.
        guest_score: Guest loyalty score (0-100).
        num_guests: Number of guests.
        next_booking_checkin: Next booking's check-in date (or None).
        prev_booking_checkout: Previous booking's checkout date (or None).
    """

    reservation_id: str = ""
    property_id: str = ""
    checkin_date: str = ""
    checkout_date: str = ""
    checkin_time: str = "14:00"
    checkout_time: str = "10:00"
    nightly_rate: float = 0.0
    guest_score: int = 0
    num_guests: int = 1
    next_booking_checkin: str | None = None
    prev_booking_checkout: str | None = None


class UpsellEngine:
    """Detects and generates upsell opportunities for bookings.

    Analyzes booking context (dates, gaps, guest score) to generate
    targeted upsell offers. High-score guests get auto-applicable
    offers; others require PM approval.

    Args:
        early_checkin_fee: Fee for early check-in (default: 25).
        late_checkout_fee: Fee for late checkout (default: 30).
        late_checkin_threshold: Hour after which late check-in applies.
        gap_night_discount: Discount % for gap night fills.
        auto_offer_min_score: Min guest score for auto-offers.
    """

    def __init__(
        self,
        early_checkin_fee: float = 25.0,
        late_checkout_fee: float = 30.0,
        late_checkin_threshold: int = 20,
        gap_night_discount: float = 0.25,
        auto_offer_min_score: int = 60,
    ) -> None:
        self._early_fee = early_checkin_fee
        self._late_fee = late_checkout_fee
        self._late_checkin_hour = late_checkin_threshold
        self._gap_discount = gap_night_discount
        self._auto_min_score = auto_offer_min_score

    def analyze(self, context: BookingContext) -> UpsellResult:
        """Analyze a booking for all upsell opportunities.

        Args:
            context: Booking data and calendar context.

        Returns:
            UpsellResult with all applicable offers.
        """
        offers: list[UpsellOffer] = []

        gap_offer = self._check_gap_night(context)
        if gap_offer:
            offers.append(gap_offer)

        early_offer = self._check_early_checkin(context)
        if early_offer:
            offers.append(early_offer)

        late_co_offer = self._check_late_checkout(context)
        if late_co_offer:
            offers.append(late_co_offer)

        late_ci_offer = self._check_late_checkin(context)
        if late_ci_offer:
            offers.append(late_ci_offer)

        return self._build_result(context, offers)

    def _check_gap_night(
        self, ctx: BookingContext,
    ) -> UpsellOffer | None:
        """Detect empty nights before this booking.

        If there's a gap between previous checkout and this check-in,
        offer the gap nights at a discounted rate.

        Args:
            ctx: Booking context.

        Returns:
            UpsellOffer for gap nights, or None.
        """
        if not ctx.prev_booking_checkout or not ctx.checkin_date:
            return None

        gap_days = _days_between(ctx.prev_booking_checkout, ctx.checkin_date)
        if gap_days < 1 or gap_days > 3:
            return None

        discounted = ctx.nightly_rate * (1 - self._gap_discount)
        total = discounted * gap_days
        is_auto = ctx.guest_score >= self._auto_min_score

        return UpsellOffer(
            upsell_type="gap_night",
            description=f"Extend your stay! {gap_days} extra night(s) at {int(self._gap_discount * 100)}% off",
            original_price=ctx.nightly_rate * gap_days,
            offer_price=total,
            discount_pct=self._gap_discount * 100,
            auto_applicable=is_auto,
            revenue_potential=total,
            confidence=0.4 + (ctx.guest_score / 200),
        )

    def _check_early_checkin(
        self, ctx: BookingContext,
    ) -> UpsellOffer | None:
        """Offer early check-in if property is available.

        Only offered if no checkout on the same day (prev_booking_checkout
        is before checkin_date).

        Args:
            ctx: Booking context.

        Returns:
            UpsellOffer for early check-in, or None.
        """
        if not ctx.checkin_time or not ctx.checkin_date:
            return None

        has_same_day_checkout = (
            ctx.prev_booking_checkout == ctx.checkin_date
        )
        if has_same_day_checkout:
            return None

        is_auto = ctx.guest_score >= self._auto_min_score

        return UpsellOffer(
            upsell_type="early_checkin",
            description="Early check-in from 11:00 AM",
            original_price=self._early_fee,
            offer_price=self._early_fee,
            discount_pct=0,
            auto_applicable=is_auto,
            revenue_potential=self._early_fee,
            confidence=0.5 + (ctx.guest_score / 250),
        )

    def _check_late_checkout(
        self, ctx: BookingContext,
    ) -> UpsellOffer | None:
        """Offer late checkout if no same-day check-in follows.

        Args:
            ctx: Booking context.

        Returns:
            UpsellOffer for late checkout, or None.
        """
        if not ctx.checkout_date:
            return None

        has_same_day_checkin = (
            ctx.next_booking_checkin == ctx.checkout_date
        )
        if has_same_day_checkin:
            return None

        is_auto = ctx.guest_score >= 80

        return UpsellOffer(
            upsell_type="late_checkout",
            description="Late checkout until 3:00 PM",
            original_price=self._late_fee,
            offer_price=self._late_fee if ctx.guest_score < 80 else 0,
            discount_pct=100 if ctx.guest_score >= 80 else 0,
            auto_applicable=is_auto,
            revenue_potential=self._late_fee if ctx.guest_score < 80 else 0,
            confidence=0.6 + (ctx.guest_score / 300),
        )

    def _check_late_checkin(
        self, ctx: BookingContext,
    ) -> UpsellOffer | None:
        """Accommodate guests arriving after standard hours.

        Generates access code instructions for late arrivals.
        No fee — this is a service, not a revenue opportunity.

        Args:
            ctx: Booking context.

        Returns:
            UpsellOffer for late check-in accommodation, or None.
        """
        if not ctx.checkin_time:
            return None

        try:
            hour = int(ctx.checkin_time.split(":")[0])
        except (ValueError, IndexError):
            return None

        if hour < self._late_checkin_hour:
            return None

        return UpsellOffer(
            upsell_type="late_checkin",
            description="Late check-in with self-access instructions",
            original_price=0,
            offer_price=0,
            discount_pct=0,
            auto_applicable=True,
            revenue_potential=0,
            confidence=0.9,
        )

    def _build_result(
        self,
        ctx: BookingContext,
        offers: list[UpsellOffer],
    ) -> UpsellResult:
        """Build final UpsellResult with message and totals.

        Args:
            ctx: Booking context.
            offers: Detected upsell offers.

        Returns:
            Complete UpsellResult.
        """
        total_revenue = sum(o.revenue_potential for o in offers)
        message = self._build_guest_message(offers)

        actions = _build_upsell_actions(ctx, offers)

        return UpsellResult(
            reservation_id=ctx.reservation_id,
            property_id=ctx.property_id,
            guest_score=ctx.guest_score,
            offers=offers,
            total_revenue_potential=total_revenue,
            message_to_guest=message,
            actions=actions,
        )

    @staticmethod
    def _build_guest_message(offers: list[UpsellOffer]) -> str:
        """Build offer message for the guest.

        Args:
            offers: List of upsell offers.

        Returns:
            Formatted message string.
        """
        if not offers:
            return ""

        revenue_offers = [o for o in offers if o.revenue_potential > 0]
        if not revenue_offers:
            return ""

        lines = ["We have some special options for your stay:"]
        for offer in revenue_offers:
            if offer.offer_price > 0:
                lines.append(f"  - {offer.description} (€{offer.offer_price:.0f})")
            else:
                lines.append(f"  - {offer.description} (complimentary)")

        lines.append("\nWould you like any of these?")
        return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────── #


def _days_between(date_a: str, date_b: str) -> int:
    """Calculate days between two ISO date strings.

    Args:
        date_a: Earlier date (ISO format).
        date_b: Later date (ISO format).

    Returns:
        Number of days between dates (positive if b > a).
    """
    try:
        a = datetime.fromisoformat(date_a)
        b = datetime.fromisoformat(date_b)
        return (b - a).days
    except (ValueError, TypeError):
        return 0


def _build_upsell_actions(
    ctx: BookingContext,
    offers: list[UpsellOffer],
) -> list[dict[str, Any]]:
    """Build MCP actions for auto-applicable upsell offers.

    Args:
        ctx: Booking context.
        offers: Upsell offers.

    Returns:
        List of MCP action dicts.
    """
    actions: list[dict[str, Any]] = []

    for offer in offers:
        if not offer.auto_applicable:
            continue

        if offer.upsell_type == "late_checkin":
            actions.append({
                "tool": "createAccessCode",
                "params": {
                    "propertyId": ctx.property_id,
                    "name": f"Late check-in {ctx.reservation_id}",
                },
            })
        elif offer.upsell_type in ("early_checkin", "late_checkout"):
            actions.append({
                "tool": "sendWhatsApp",
                "params": {
                    "reservationId": ctx.reservation_id,
                    "message": offer.description,
                },
            })

    return actions
