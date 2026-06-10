"""Calendar intelligence — gap analysis and scheduling feasibility.

CalendarEvaluator answers operational questions that require calendar
awareness:

- **Orphan night detection**: Short gaps between bookings that are
  unlikely to sell, creating opportunities for extensions or min-stay
  exceptions.
- **Min-stay exception feasibility**: Whether a shorter-than-minimum
  stay fills an otherwise-unsellable gap.
- **Early check-in / late check-out feasibility**: Whether the calendar
  allows flexible timing without conflicting with adjacent bookings.
- **Gap valuation**: Revenue impact of filling or leaving a gap empty.

All methods are synchronous and pure — they operate on calendar data
already fetched from PMS.  No I/O is performed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ORPHAN_THRESHOLD_NIGHTS: Final[int] = 2
_SELLABILITY_HIGH: Final[float] = 0.8
_SELLABILITY_MEDIUM: Final[float] = 0.5
_SELLABILITY_LOW: Final[float] = 0.2
_SAME_DAY_TURNOVER_HOURS: Final[int] = 4
_DEFAULT_CHECKOUT_HOUR: Final[int] = 11
_DEFAULT_CHECKIN_HOUR: Final[int] = 15


# ---------------------------------------------------------------------------
# GapInfo — value object
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GapInfo:
    """A vacant gap between two bookings.

    Attributes:
        gap_start: First vacant date.
        gap_end: Last vacant date (inclusive).
        gap_nights: Number of vacant nights.
        is_orphan: Whether the gap is too short to sell normally.
        sellability_score: Estimated probability the gap will sell
            (0.0 = unsellable, 1.0 = highly likely to sell).
        prev_checkout: Check-out date of the preceding booking.
        next_checkin: Check-in date of the following booking.
    """

    gap_start: date
    gap_end: date
    gap_nights: int
    is_orphan: bool = False
    sellability_score: float = 0.0
    prev_checkout: date | None = None
    next_checkin: date | None = None

    @property
    def value_if_filled(self) -> float:
        """Relative value of filling this gap (0.0–1.0 scale).

        Orphan nights that are unlikely to sell have high fill-value
        because filling them captures otherwise-lost revenue.
        """
        if self.is_orphan:
            return 1.0 - self.sellability_score
        return 0.5 * (1.0 - self.sellability_score)

    def __repr__(self) -> str:
        orphan_tag = " ORPHAN" if self.is_orphan else ""
        return (
            f"GapInfo({self.gap_start}..{self.gap_end}, "
            f"{self.gap_nights}n, sell={self.sellability_score:.2f}"
            f"{orphan_tag})"
        )


# ---------------------------------------------------------------------------
# FeasibilityResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FeasibilityResult:
    """Result of a scheduling feasibility check.

    Attributes:
        feasible: Whether the requested action is feasible.
        reason: Human-readable explanation.
        conflict_reservation_id: ID of the conflicting reservation (if any).
        buffer_hours: Available buffer time (for early/late checks).
    """

    feasible: bool
    reason: str
    conflict_reservation_id: str | None = None
    buffer_hours: float = 0.0


# ---------------------------------------------------------------------------
# CalendarEvaluator
# ---------------------------------------------------------------------------

class CalendarEvaluator:
    """Calendar intelligence engine for gap analysis and scheduling.

    All methods accept raw calendar data and return structured results.
    No I/O — pure computation.

    Attributes:
        _orphan_threshold: Maximum gap nights to classify as orphan.
        _log: Bound structured logger.
    """

    def __init__(
        self,
        *,
        orphan_threshold: int = _ORPHAN_THRESHOLD_NIGHTS,
    ) -> None:
        self._orphan_threshold = orphan_threshold
        self._log = logger.bind(component="calendar_evaluator")

    def analyze_gaps(
        self,
        calendar_data: dict[str, Any],
        property_id: str,
        min_stay: int = 1,
    ) -> list[GapInfo]:
        """Identify all gaps in the calendar and classify them.

        Scans the calendar for consecutive vacant dates, computes gap
        length, and classifies each as orphan or sellable.

        Args:
            calendar_data: Calendar availability dict.
            property_id: Property identifier (for logging).
            min_stay: Property minimum-stay requirement (nights).

        Returns:
            List of GapInfo objects, sorted by gap_start.
        """
        bookings = self._extract_booking_periods(calendar_data)
        if not bookings:
            return []

        bookings.sort(key=lambda b: b[0])
        gaps: list[GapInfo] = []

        for i in range(len(bookings) - 1):
            _, prev_checkout = bookings[i]
            next_checkin, _ = bookings[i + 1]

            gap_nights = (next_checkin - prev_checkout).days
            if gap_nights <= 0:
                continue

            gap_start = prev_checkout
            gap_end = next_checkin - timedelta(days=1)
            is_orphan = gap_nights <= self._orphan_threshold
            sellability = self._compute_sellability(
                gap_nights, min_stay, prev_checkout, next_checkin,
            )

            gaps.append(GapInfo(
                gap_start=gap_start,
                gap_end=gap_end,
                gap_nights=gap_nights,
                is_orphan=is_orphan,
                sellability_score=round(sellability, 3),
                prev_checkout=prev_checkout,
                next_checkin=next_checkin,
            ))

        self._log.debug(
            "gaps_analyzed",
            property_id=property_id,
            total_gaps=len(gaps),
            orphan_gaps=sum(1 for g in gaps if g.is_orphan),
        )
        return gaps

    def check_min_stay_exception(
        self,
        requested_nights: int,
        min_stay: int,
        gap_info: GapInfo | None,
    ) -> FeasibilityResult:
        """Evaluate whether a min-stay exception is justified.

        A min-stay exception is justified when:
        1. The requested stay fills an orphan gap exactly.
        2. The gap is unlikely to sell at the normal min-stay.
        3. Leaving the gap empty loses more revenue than accepting
           the shorter stay.

        Args:
            requested_nights: Guest's requested stay length.
            min_stay: Property minimum-stay requirement.
            gap_info: Gap analysis for the relevant period.

        Returns:
            FeasibilityResult with justification.
        """
        if requested_nights >= min_stay:
            return FeasibilityResult(
                feasible=True,
                reason="Requested nights meet minimum stay requirement.",
            )

        if gap_info is None:
            return FeasibilityResult(
                feasible=False,
                reason="No gap context available — cannot evaluate exception.",
            )

        if not gap_info.is_orphan:
            return FeasibilityResult(
                feasible=False,
                reason=(
                    f"Gap of {gap_info.gap_nights} nights is not an orphan "
                    f"(threshold: {self._orphan_threshold}). Standard min-stay "
                    "applies."
                ),
            )

        if requested_nights > gap_info.gap_nights:
            return FeasibilityResult(
                feasible=False,
                reason=(
                    f"Requested {requested_nights} nights exceeds gap of "
                    f"{gap_info.gap_nights} nights."
                ),
            )

        if gap_info.sellability_score >= _SELLABILITY_HIGH:
            return FeasibilityResult(
                feasible=False,
                reason=(
                    f"Gap sellability is high ({gap_info.sellability_score:.2f}) — "
                    "likely to sell at full min-stay. Exception not recommended."
                ),
            )

        fills_gap = requested_nights == gap_info.gap_nights
        reason_parts = [
            f"Orphan gap of {gap_info.gap_nights} nights",
            f"with low sellability ({gap_info.sellability_score:.2f}).",
        ]
        if fills_gap:
            reason_parts.append("Requested stay fills the gap exactly.")
        else:
            reason_parts.append(
                f"Requested {requested_nights} of {gap_info.gap_nights} nights.",
            )

        return FeasibilityResult(
            feasible=True,
            reason=" ".join(reason_parts),
        )

    def check_early_checkin_feasibility(
        self,
        calendar_data: dict[str, Any],
        property_id: str,
        checkin_date_str: str,
        requested_hour: int = 12,
    ) -> FeasibilityResult:
        """Check whether early check-in is feasible.

        Early check-in requires no previous booking checking out on the
        same day, or enough buffer time for turnover.

        Args:
            calendar_data: Calendar availability dict.
            property_id: Property identifier.
            checkin_date_str: ISO date of requested check-in.
            requested_hour: Requested check-in hour (24h format).

        Returns:
            FeasibilityResult.
        """
        checkin_date = _parse_date(checkin_date_str)
        if checkin_date is None:
            return FeasibilityResult(
                feasible=False,
                reason="Invalid check-in date.",
            )

        bookings = self._extract_booking_periods(calendar_data)
        prev_checkout_same_day = None
        prev_reservation_id = None

        for booking in bookings:
            checkout = booking[1]
            if checkout == checkin_date:
                prev_checkout_same_day = checkout
                prev_reservation_id = booking[2] if len(booking) > 2 else None
                break

        if prev_checkout_same_day is None:
            buffer = float(_DEFAULT_CHECKIN_HOUR - requested_hour)
            return FeasibilityResult(
                feasible=True,
                reason="No same-day checkout — early check-in available.",
                buffer_hours=max(buffer, 0.0),
            )

        buffer_hours = float(requested_hour - _DEFAULT_CHECKOUT_HOUR)
        if buffer_hours < _SAME_DAY_TURNOVER_HOURS:
            return FeasibilityResult(
                feasible=False,
                reason=(
                    f"Same-day turnover: only {buffer_hours:.0f}h buffer "
                    f"(need {_SAME_DAY_TURNOVER_HOURS}h for cleaning)."
                ),
                conflict_reservation_id=prev_reservation_id,
                buffer_hours=buffer_hours,
            )

        return FeasibilityResult(
            feasible=True,
            reason=f"Same-day turnover with {buffer_hours:.0f}h buffer — sufficient.",
            buffer_hours=buffer_hours,
        )

    def check_late_checkout_feasibility(
        self,
        calendar_data: dict[str, Any],
        property_id: str,
        checkout_date_str: str,
        requested_hour: int = 14,
    ) -> FeasibilityResult:
        """Check whether late check-out is feasible.

        Late check-out requires no next booking checking in on the same
        day, or enough buffer time for turnover.

        Args:
            calendar_data: Calendar availability dict.
            property_id: Property identifier.
            checkout_date_str: ISO date of requested check-out.
            requested_hour: Requested check-out hour (24h format).

        Returns:
            FeasibilityResult.
        """
        checkout_date = _parse_date(checkout_date_str)
        if checkout_date is None:
            return FeasibilityResult(
                feasible=False,
                reason="Invalid check-out date.",
            )

        bookings = self._extract_booking_periods(calendar_data)
        next_checkin_same_day = None
        next_reservation_id = None

        for booking in bookings:
            checkin = booking[0]
            if checkin == checkout_date:
                next_checkin_same_day = checkin
                next_reservation_id = booking[2] if len(booking) > 2 else None
                break

        if next_checkin_same_day is None:
            return FeasibilityResult(
                feasible=True,
                reason="No same-day check-in — late check-out available.",
                buffer_hours=float(24 - requested_hour),
            )

        buffer_hours = float(_DEFAULT_CHECKIN_HOUR - requested_hour)
        if buffer_hours < _SAME_DAY_TURNOVER_HOURS:
            return FeasibilityResult(
                feasible=False,
                reason=(
                    f"Next guest checks in same day: only {buffer_hours:.0f}h "
                    f"buffer (need {_SAME_DAY_TURNOVER_HOURS}h for cleaning)."
                ),
                conflict_reservation_id=next_reservation_id,
                buffer_hours=buffer_hours,
            )

        return FeasibilityResult(
            feasible=True,
            reason=f"Same-day check-in but {buffer_hours:.0f}h buffer — sufficient.",
            buffer_hours=buffer_hours,
        )

    def compute_orphan_night_value(
        self,
        gap_info: GapInfo,
        adr: float,
    ) -> float:
        """Estimate the revenue value of filling an orphan gap.

        Value = ADR * nights * (1 - sellability).  If the gap would
        sell anyway, the incremental value of a min-stay exception
        is lower.

        Args:
            gap_info: Gap analysis for the relevant period.
            adr: Average daily rate for the property.

        Returns:
            Estimated revenue capture in property currency.
        """
        if adr <= 0 or gap_info.gap_nights <= 0:
            return 0.0
        incremental_factor = 1.0 - gap_info.sellability_score
        return round(adr * gap_info.gap_nights * incremental_factor, 2)

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    def _extract_booking_periods(
        self,
        calendar_data: dict[str, Any],
    ) -> list[tuple[date, date, str]]:
        """Extract booking periods from calendar data.

        Returns a list of (checkin, checkout, reservation_id) tuples.
        Supports two formats:
        - ``{"bookings": [{"check_in": "...", "check_out": "...", "id": "..."}, …]}``
        - Date-status dict with consecutive occupied dates grouped.

        Args:
            calendar_data: Raw calendar data.

        Returns:
            List of (checkin, checkout, reservation_id) tuples.
        """
        bookings_list = calendar_data.get("bookings")
        if isinstance(bookings_list, list):
            return self._parse_bookings_list(bookings_list)
        return self._infer_bookings_from_dates(calendar_data)

    def _parse_bookings_list(
        self,
        bookings: list[dict[str, Any]],
    ) -> list[tuple[date, date, str]]:
        """Parse an explicit bookings list.

        Args:
            bookings: List of booking dicts with check_in/check_out.

        Returns:
            List of (checkin, checkout, reservation_id) tuples.
        """
        results: list[tuple[date, date, str]] = []
        for booking in bookings:
            checkin = _parse_date(str(booking.get("check_in", "")))
            checkout = _parse_date(str(booking.get("check_out", "")))
            res_id = str(booking.get("id", booking.get("reservation_id", "")))
            if checkin and checkout and checkout > checkin:
                results.append((checkin, checkout, res_id))
        return results

    def _infer_bookings_from_dates(
        self,
        calendar_data: dict[str, Any],
    ) -> list[tuple[date, date, str]]:
        """Infer booking periods from consecutive occupied dates.

        Groups consecutive occupied dates into booking-like periods.

        Args:
            calendar_data: Date-status calendar dict.

        Returns:
            List of inferred (start, end, "") tuples.
        """
        dates_list = calendar_data.get("dates")
        occupied: list[date] = []

        if isinstance(dates_list, list):
            for entry in dates_list:
                if isinstance(entry, dict):
                    status = str(entry.get("status", "")).lower()
                    if status in {"occupied", "booked", "reserved"}:
                        d = _parse_date(str(entry.get("date", "")))
                        if d is not None:
                            occupied.append(d)
        else:
            for key, value in calendar_data.items():
                if key in {"bookings", "dates"}:
                    continue
                if str(value).lower() in {"occupied", "booked", "reserved"}:
                    d = _parse_date(key)
                    if d is not None:
                        occupied.append(d)

        if not occupied:
            return []

        occupied.sort()
        periods: list[tuple[date, date, str]] = []
        start = occupied[0]
        prev = occupied[0]

        for d in occupied[1:]:
            if (d - prev).days > 1:
                periods.append((start, prev + timedelta(days=1), ""))
                start = d
            prev = d
        periods.append((start, prev + timedelta(days=1), ""))

        return periods

    def _compute_sellability(
        self,
        gap_nights: int,
        min_stay: int,
        prev_checkout: date,
        next_checkin: date,
    ) -> float:
        """Estimate how likely a gap is to sell.

        Factors:
        - Gaps shorter than min-stay are hard to sell.
        - Weekday-only gaps sell worse than weekend gaps.
        - Very short gaps (1 night) almost never sell.

        Args:
            gap_nights: Number of vacant nights.
            min_stay: Property minimum-stay requirement.
            prev_checkout: Previous booking check-out date.
            next_checkin: Next booking check-in date.

        Returns:
            Sellability score (0.0–1.0).
        """
        if gap_nights < 1:
            return 0.0

        if gap_nights < min_stay:
            base = _SELLABILITY_LOW
        elif gap_nights == min_stay:
            base = _SELLABILITY_MEDIUM
        else:
            base = _SELLABILITY_HIGH

        weekend_days = 0
        current = prev_checkout
        while current < next_checkin:
            if current.isoweekday() in {5, 6}:
                weekend_days += 1
            current += timedelta(days=1)

        weekend_ratio = weekend_days / gap_nights if gap_nights > 0 else 0
        weekend_bonus = weekend_ratio * 0.2

        if gap_nights == 1:
            return round(min(base * 0.5 + weekend_bonus, 1.0), 3)

        return round(min(base + weekend_bonus, 1.0), 3)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> date | None:
    """Parse an ISO date string.

    Args:
        value: Date string (YYYY-MM-DD or ISO datetime).

    Returns:
        Parsed date or None.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None
