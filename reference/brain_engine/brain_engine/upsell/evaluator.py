"""Upsell feasibility evaluator for revenue opportunities.

Wraps CalendarEvaluator to answer the Cendra frontend question:
"Given this reservation, which upsells (Early Check-in, Late Check-out,
Gap Night, Late Check-in) are feasible, and at what price?"

The evaluator is stateless and synchronous — all data is passed in
by the caller.  Pricing is computed from property-level defaults
with calendar-aware adjustments.

Upsell types (matching Cendra UI exactly):
- **Early Check-in**: Guest checks in before standard time.
- **Late Check-out**: Guest stays past standard checkout time.
- **Gap Night**: Fill empty nights between bookings at a discount.
- **Late Check-in**: Guest arrives after standard check-in hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import structlog

from brain_engine.calendar.evaluator import (
    CalendarEvaluator,
    FeasibilityResult,
    GapInfo,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_EARLY_CHECKIN_RATE: Final[float] = 10.0   # $ per hour
_DEFAULT_LATE_CHECKOUT_RATE: Final[float] = 15.0   # $ per hour
_DEFAULT_GAP_NIGHT_DISCOUNT: Final[float] = 0.20   # 20% off ADR
_DEFAULT_CHECKIN_HOUR: Final[int] = 15
_DEFAULT_CHECKOUT_HOUR: Final[int] = 10


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class UpsellType(StrEnum):
    """Types of upsell matching the Cendra frontend."""

    EARLY_CHECKIN = "early_checkin"
    LATE_CHECKOUT = "late_checkout"
    GAP_NIGHT = "gap_night"
    LATE_CHECKIN = "late_checkin"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class UpsellOption:
    """A single upsell opportunity for a reservation.

    Attributes:
        upsell_type: Type of upsell.
        feasible: Whether the upsell is operationally possible.
        reason: Human-readable explanation of feasibility.
        time_range: Available time window (e.g. "12:00 - 15:00").
        amount: Suggested price in property currency.
        alternative_options: Additional time/price options.
        gap_info: Gap details (for GAP_NIGHT type only).
    """

    upsell_type: UpsellType
    feasible: bool
    reason: str
    time_range: str = ""
    amount: float = 0.0
    alternative_options: tuple[dict[str, Any], ...] = ()
    gap_info: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response.

        Returns:
            Dict with all upsell option fields.
        """
        result: dict[str, Any] = {
            "upsell_type": self.upsell_type.value,
            "feasible": self.feasible,
            "reason": self.reason,
            "time_range": self.time_range,
            "amount": self.amount,
        }
        if self.alternative_options:
            result["alternative_options"] = list(self.alternative_options)
        if self.gap_info is not None:
            result["gap_info"] = self.gap_info
        return result


@dataclass(frozen=True, slots=True)
class UpsellEvaluation:
    """Complete upsell evaluation for a reservation.

    Attributes:
        property_id: Property identifier.
        reservation_id: Reservation identifier.
        options: List of evaluated upsell options.
        total_potential_revenue: Sum of all feasible upsell amounts.
    """

    property_id: str
    reservation_id: str
    options: tuple[UpsellOption, ...] = ()
    total_potential_revenue: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response.

        Returns:
            Dict with evaluation summary and all options.
        """
        return {
            "property_id": self.property_id,
            "reservation_id": self.reservation_id,
            "options": [o.to_dict() for o in self.options],
            "total_potential_revenue": self.total_potential_revenue,
            "feasible_count": sum(1 for o in self.options if o.feasible),
            "total_count": len(self.options),
        }


# ---------------------------------------------------------------------------
# UpsellEvaluator
# ---------------------------------------------------------------------------

class UpsellEvaluator:
    """Evaluates upsell feasibility for a specific reservation.

    Delegates calendar checks to CalendarEvaluator and adds pricing
    logic on top.  All methods are synchronous.

    Attributes:
        _calendar: Calendar intelligence engine.
        _early_rate: Per-hour rate for early check-in.
        _late_rate: Per-hour rate for late check-out.
        _gap_discount: Discount fraction for gap night pricing.
        _log: Bound structured logger.
    """

    def __init__(
        self,
        calendar_evaluator: CalendarEvaluator | None = None,
        *,
        early_checkin_rate: float = _DEFAULT_EARLY_CHECKIN_RATE,
        late_checkout_rate: float = _DEFAULT_LATE_CHECKOUT_RATE,
        gap_night_discount: float = _DEFAULT_GAP_NIGHT_DISCOUNT,
    ) -> None:
        self._calendar = calendar_evaluator or CalendarEvaluator()
        self._early_rate = early_checkin_rate
        self._late_rate = late_checkout_rate
        self._gap_discount = gap_night_discount
        self._log = logger.bind(component="upsell_evaluator")

    def evaluate(
        self,
        *,
        property_id: str,
        reservation_id: str,
        checkin_date: str,
        checkout_date: str,
        calendar_data: dict[str, Any],
        adr: float = 0.0,
        standard_checkin_hour: int = _DEFAULT_CHECKIN_HOUR,
        standard_checkout_hour: int = _DEFAULT_CHECKOUT_HOUR,
        property_config: dict[str, Any] | None = None,
    ) -> UpsellEvaluation:
        """Evaluate all upsell types for a reservation.

        Args:
            property_id: Property identifier.
            reservation_id: Reservation identifier.
            checkin_date: ISO check-in date (YYYY-MM-DD).
            checkout_date: ISO check-out date (YYYY-MM-DD).
            calendar_data: Calendar availability dict.
            adr: Average daily rate for pricing gap nights.
            standard_checkin_hour: Standard check-in hour (24h).
            standard_checkout_hour: Standard check-out hour (24h).
            property_config: Optional per-property pricing overrides.

        Returns:
            UpsellEvaluation with all options.
        """
        config = property_config or {}
        early_rate = config.get("early_checkin_rate", self._early_rate)
        late_rate = config.get("late_checkout_rate", self._late_rate)

        options: list[UpsellOption] = [
            self._evaluate_early_checkin(
                calendar_data, property_id, checkin_date,
                standard_checkin_hour, early_rate,
            ),
            self._evaluate_late_checkout(
                calendar_data, property_id, checkout_date,
                standard_checkout_hour, late_rate,
            ),
            self._evaluate_gap_night(
                calendar_data, property_id, adr,
            ),
            self._evaluate_late_checkin(
                standard_checkin_hour,
            ),
        ]

        total_revenue = sum(o.amount for o in options if o.feasible)

        self._log.info(
            "upsell_evaluated",
            property_id=property_id,
            reservation_id=reservation_id,
            feasible=[o.upsell_type.value for o in options if o.feasible],
            total_revenue=round(total_revenue, 2),
        )

        return UpsellEvaluation(
            property_id=property_id,
            reservation_id=reservation_id,
            options=tuple(options),
            total_potential_revenue=round(total_revenue, 2),
        )

    # -------------------------------------------------------------------
    # Individual upsell evaluations
    # -------------------------------------------------------------------

    def _evaluate_early_checkin(
        self,
        calendar_data: dict[str, Any],
        property_id: str,
        checkin_date: str,
        standard_hour: int,
        rate_per_hour: float,
    ) -> UpsellOption:
        """Evaluate early check-in feasibility and pricing.

        Offers time slots from the earliest feasible hour to the
        standard check-in hour, each priced per hour.

        Args:
            calendar_data: Calendar availability.
            property_id: Property identifier.
            checkin_date: ISO check-in date.
            standard_hour: Standard check-in hour.
            rate_per_hour: Price per hour of early access.

        Returns:
            UpsellOption for early check-in.
        """
        result = self._calendar.check_early_checkin_feasibility(
            calendar_data, property_id, checkin_date,
            requested_hour=standard_hour - 3,
        )

        if not result.feasible:
            return UpsellOption(
                upsell_type=UpsellType.EARLY_CHECKIN,
                feasible=False,
                reason=result.reason,
            )

        earliest_hour = max(standard_hour - 3, 8)
        hours_available = standard_hour - earliest_hour
        primary_amount = round(hours_available * rate_per_hour, 2)
        time_range = f"{earliest_hour:02d}:00 - {standard_hour:02d}:00"

        alternatives: list[dict[str, Any]] = []
        for offset in range(1, hours_available):
            alt_hour = standard_hour - offset
            alt_amount = round(offset * rate_per_hour, 2)
            alternatives.append({
                "time_range": f"{alt_hour:02d}:00 - {standard_hour:02d}:00",
                "amount": alt_amount,
                "description": f"{offset}h early ({alt_hour}:00)",
            })

        return UpsellOption(
            upsell_type=UpsellType.EARLY_CHECKIN,
            feasible=True,
            reason=result.reason,
            time_range=time_range,
            amount=primary_amount,
            alternative_options=tuple(alternatives),
        )

    def _evaluate_late_checkout(
        self,
        calendar_data: dict[str, Any],
        property_id: str,
        checkout_date: str,
        standard_hour: int,
        rate_per_hour: float,
    ) -> UpsellOption:
        """Evaluate late check-out feasibility and pricing.

        Offers time slots from the standard checkout hour to the
        latest feasible hour, each priced per hour.

        Args:
            calendar_data: Calendar availability.
            property_id: Property identifier.
            checkout_date: ISO check-out date.
            standard_hour: Standard check-out hour.
            rate_per_hour: Price per hour of late departure.

        Returns:
            UpsellOption for late check-out.
        """
        latest_hour = min(standard_hour + 4, 18)
        result = self._calendar.check_late_checkout_feasibility(
            calendar_data, property_id, checkout_date,
            requested_hour=latest_hour,
        )

        if not result.feasible:
            return UpsellOption(
                upsell_type=UpsellType.LATE_CHECKOUT,
                feasible=False,
                reason=result.reason,
            )

        hours_available = latest_hour - standard_hour
        primary_amount = round(hours_available * rate_per_hour, 2)
        time_range = f"{standard_hour:02d}:00 - {latest_hour:02d}:00"

        alternatives: list[dict[str, Any]] = []
        for offset in range(1, hours_available):
            alt_hour = standard_hour + offset
            alt_amount = round(offset * rate_per_hour, 2)
            alternatives.append({
                "time_range": f"{standard_hour:02d}:00 - {alt_hour:02d}:00",
                "amount": alt_amount,
                "description": f"{offset}h late (until {alt_hour}:00)",
            })

        return UpsellOption(
            upsell_type=UpsellType.LATE_CHECKOUT,
            feasible=True,
            reason=result.reason,
            time_range=time_range,
            amount=primary_amount,
            alternative_options=tuple(alternatives),
        )

    def _evaluate_gap_night(
        self,
        calendar_data: dict[str, Any],
        property_id: str,
        adr: float,
    ) -> UpsellOption:
        """Evaluate gap night fill opportunity.

        Finds orphan gaps adjacent to the reservation and prices them
        at a discount from ADR.

        Args:
            calendar_data: Calendar availability.
            property_id: Property identifier.
            adr: Average daily rate for pricing.

        Returns:
            UpsellOption for gap night fill.
        """
        gaps = self._calendar.analyze_gaps(calendar_data, property_id)
        orphan_gaps = [g for g in gaps if g.is_orphan]

        if not orphan_gaps:
            return UpsellOption(
                upsell_type=UpsellType.GAP_NIGHT,
                feasible=False,
                reason="No orphan gaps found adjacent to this reservation.",
            )

        best_gap = max(orphan_gaps, key=lambda g: g.value_if_filled)
        discounted_adr = round(adr * (1 - self._gap_discount), 2)
        total_amount = round(discounted_adr * best_gap.gap_nights, 2)
        value = self._calendar.compute_orphan_night_value(best_gap, adr)

        return UpsellOption(
            upsell_type=UpsellType.GAP_NIGHT,
            feasible=True,
            reason=(
                f"Orphan gap of {best_gap.gap_nights} night(s) "
                f"({best_gap.gap_start} to {best_gap.gap_end}), "
                f"sellability {best_gap.sellability_score:.0%}. "
                f"Discounted at {self._gap_discount:.0%} off ADR."
            ),
            time_range=f"{best_gap.gap_start} - {best_gap.gap_end}",
            amount=total_amount,
            gap_info={
                "gap_nights": best_gap.gap_nights,
                "gap_start": str(best_gap.gap_start),
                "gap_end": str(best_gap.gap_end),
                "sellability": best_gap.sellability_score,
                "incremental_value": round(value, 2),
                "discounted_adr": discounted_adr,
            },
        )

    def _evaluate_late_checkin(
        self,
        standard_hour: int,
    ) -> UpsellOption:
        """Evaluate late check-in accommodation.

        Late check-in is always feasible (no calendar conflict) but
        may require coordination with the property for key handoff.

        Args:
            standard_hour: Standard check-in hour.

        Returns:
            UpsellOption for late check-in (always feasible, no charge).
        """
        return UpsellOption(
            upsell_type=UpsellType.LATE_CHECKIN,
            feasible=True,
            reason=(
                "Late check-in is available. Guest will receive "
                "access instructions for self check-in after hours."
            ),
            time_range=f"{standard_hour:02d}:00 - 23:00",
            amount=0.0,
        )
