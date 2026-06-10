"""Booking feature computation for the decision-pattern subsystem.

Transforms raw PMS reservation and calendar data into a structured
``BookingFeatures`` object that pattern rules can evaluate against.

All features are deterministic — they are computed from data, never
learned.  This ensures that rule conditions reference stable, verifiable
values rather than fuzzy estimates.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Final

import structlog

from brain_engine.patterns.models import Scenario

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WEEKEND_DAYS: Final[frozenset[int]] = frozenset({4, 5})  # Fri, Sat (isoweekday-based)
_DEFAULT_SEASON: Final[str] = "standard"
_OCCUPANCY_WINDOW_7D: Final[int] = 7
_OCCUPANCY_WINDOW_30D: Final[int] = 30


# ---------------------------------------------------------------------------
# WeekdayMix — sub-component
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WeekdayMix:
    """Distribution of weekdays vs. weekends in a stay.

    Attributes:
        weekday_count: Number of weekday nights.
        weekend_count: Number of weekend nights (Friday + Saturday).
    """

    weekday_count: int = 0
    weekend_count: int = 0

    @property
    def total(self) -> int:
        """Total nights."""
        return self.weekday_count + self.weekend_count

    @property
    def weekend_ratio(self) -> float:
        """Fraction of nights that fall on weekends."""
        if self.total == 0:
            return 0.0
        return self.weekend_count / self.total


# ---------------------------------------------------------------------------
# BookingFeatures — deterministic booking attributes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BookingFeatures:
    """Deterministic features computed from a single reservation + calendar.

    Used as the evaluation surface for PatternRule conditions.  Every
    field must be reproducible from the same PMS data at any point in
    time (no randomness, no learning).

    Attributes:
        nights: Total nights in the reservation.
        adults: Number of adults.
        children: Number of children.
        infants: Number of infants.
        pets: Number of pets (0 if not applicable).
        booking_value: Total booking value in property currency.
        adr: Average daily rate (booking_value / nights).
        fees_total: Sum of additional fees (cleaning, extras, …).
        lead_time_hours: Hours between booking creation and check-in.
        booking_source: Channel the booking came from (airbnb, booking.com, …).
        payment_status: Current payment state (paid, partial, pending, …).
        id_verified: Whether the guest has verified identity.
        gap_before_nights: Vacant nights before check-in.
        gap_after_nights: Vacant nights after check-out.
        occupancy_7d: Property occupancy rate within a 7-day window.
        occupancy_30d: Property occupancy rate within a 30-day window.
        season: Season classification (high, low, standard, event, …).
        same_day_turnover: Whether check-out and next check-in are same day.
        next_booking_same_day: Whether the next booking starts on check-out.
        weekday_mix: Breakdown of weekday vs. weekend nights.
        hours_before_checkin: Signed hours from ``now`` to check-in.
            Positive = check-in is in the future, negative = check-in
            already happened.  ``None`` when ``now`` or check-in is
            unknown.  Drives stage classification at the 24 h / 4 h
            boundaries (advisory §5.3, AI Pattern doc §5 Stage 3-4).
        hours_before_checkout: Signed hours from ``now`` to check-out.
            Same sign convention as ``hours_before_checkin``.
        is_within_24h_window: True iff ``0 < hours_before_checkin <= 24``.
            Captures the "PRE_ARRIVAL within one day" cohort that the
            feedback example separates from earlier pre-booking traffic.
        is_within_4h_window: True iff ``0 < hours_before_checkin <= 4``.
            Captures the "imminent check-in" cohort where PMs typically
            release access codes / wifi without further gating.
        reservation_status: Raw reservation status from PMS
            (``confirmed``, ``cancelled``, ``in_house``, …) when known.
    """

    nights: int = 0
    adults: int = 0
    children: int = 0
    infants: int = 0
    pets: int = 0
    booking_value: float = 0.0
    adr: float = 0.0
    fees_total: float = 0.0
    lead_time_hours: float = 0.0
    booking_source: str = ""
    payment_status: str = ""
    id_verified: bool = False
    gap_before_nights: int = 0
    gap_after_nights: int = 0
    occupancy_7d: float = 0.0
    occupancy_30d: float = 0.0
    season: str = _DEFAULT_SEASON
    same_day_turnover: bool = False
    next_booking_same_day: bool = False
    weekday_mix: WeekdayMix = field(default_factory=WeekdayMix)
    hours_before_checkin: float | None = None
    hours_before_checkout: float | None = None
    is_within_24h_window: bool = False
    is_within_4h_window: bool = False
    reservation_status: str = ""

    @property
    def total_guests(self) -> int:
        """Total human guest count (adults + children + infants)."""
        return self.adults + self.children + self.infants

    @property
    def has_children(self) -> bool:
        """Whether children or infants are present."""
        return self.children > 0 or self.infants > 0

    @property
    def is_long_stay(self) -> bool:
        """Whether the stay qualifies as long (7+ nights)."""
        return self.nights >= 7

    @property
    def is_high_value(self) -> bool:
        """Whether the booking value exceeds a high-value threshold.

        Uses ADR > $200 as a heuristic; property-specific thresholds
        should be defined in PatternRule conditions instead.
        """
        return self.adr > 200.0

    def to_dict(self) -> dict[str, Any]:
        """Flatten features to a dict for condition evaluation.

        Returns:
            Flat dictionary with all feature names as keys.
        """
        return {
            "nights": self.nights,
            "adults": self.adults,
            "children": self.children,
            "infants": self.infants,
            "pets": self.pets,
            "total_guests": self.total_guests,
            "booking_value": self.booking_value,
            "adr": self.adr,
            "fees_total": self.fees_total,
            "lead_time_hours": self.lead_time_hours,
            "booking_source": self.booking_source,
            "payment_status": self.payment_status,
            "id_verified": self.id_verified,
            "gap_before_nights": self.gap_before_nights,
            "gap_after_nights": self.gap_after_nights,
            "occupancy_7d": self.occupancy_7d,
            "occupancy_30d": self.occupancy_30d,
            "season": self.season,
            "same_day_turnover": self.same_day_turnover,
            "next_booking_same_day": self.next_booking_same_day,
            "has_children": self.has_children,
            "is_long_stay": self.is_long_stay,
            "is_high_value": self.is_high_value,
            "weekday_count": self.weekday_mix.weekday_count,
            "weekend_count": self.weekday_mix.weekend_count,
            "weekend_ratio": self.weekday_mix.weekend_ratio,
            "hours_before_checkin": self.hours_before_checkin,
            "hours_before_checkout": self.hours_before_checkout,
            "is_within_24h_window": self.is_within_24h_window,
            "is_within_4h_window": self.is_within_4h_window,
            "reservation_status": self.reservation_status,
        }


# ---------------------------------------------------------------------------
# FeatureBuilder — constructs BookingFeatures from raw PMS data
# ---------------------------------------------------------------------------

class FeatureBuilder:
    """Computes deterministic booking features from raw PMS data.

    Pure computation — no I/O, no side effects.  Every method is
    synchronous because features are derived from data already fetched.

    Example:
        >>> builder = FeatureBuilder()
        >>> features = builder.build(reservation_data, calendar_data)
        >>> features.nights
        5
    """

    def build(
        self,
        reservation_data: dict[str, Any],
        calendar_data: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> BookingFeatures:
        """Build features from reservation + calendar data.

        Args:
            reservation_data: PMS reservation object (check_in, check_out,
                adults, children, price, fees, source, …).
            calendar_data: Calendar availability around the reservation
                period (list of date→status entries, or structured dict).
            now: Optional anchor timestamp.  When supplied, the builder
                computes :attr:`BookingFeatures.hours_before_checkin` and
                :attr:`BookingFeatures.hours_before_checkout` and the
                derived 24 h / 4 h boolean flags.  Without it, those
                fields stay at their defaults so callers that have not
                migrated keep working.

        Returns:
            Fully populated BookingFeatures.
        """
        checkin_str = reservation_data.get("check_in", "")
        checkout_str = reservation_data.get("check_out", "")
        checkin = _parse_date(checkin_str)
        checkout = _parse_date(checkout_str)

        nights = (checkout - checkin).days if checkin and checkout else 0
        nights = max(nights, 0)

        booking_value = float(reservation_data.get("total_price", 0) or 0)
        adr = booking_value / nights if nights > 0 else 0.0
        fees = self._sum_fees(reservation_data)

        lead_time = self._compute_lead_time(reservation_data, checkin)
        gap_before, gap_after = self._compute_gaps(
            calendar_data, checkin, checkout,
        )
        weekday_mix = self._compute_weekday_mix(checkin, checkout)
        occupancy_7d = self._compute_occupancy(
            calendar_data, checkin_str, _OCCUPANCY_WINDOW_7D,
        )
        occupancy_30d = self._compute_occupancy(
            calendar_data, checkin_str, _OCCUPANCY_WINDOW_30D,
        )
        season = self._detect_season(
            checkin, reservation_data.get("property_id", ""),
        )
        same_day = self._check_same_day_turnover(calendar_data, checkout_str)
        hours_before_checkin, hours_before_checkout = (
            self._compute_time_to_checkin(
                reservation_data, checkin, checkout, now,
            )
        )
        within_24h = (
            hours_before_checkin is not None
            and 0.0 < hours_before_checkin <= 24.0
        )
        within_4h = (
            hours_before_checkin is not None
            and 0.0 < hours_before_checkin <= 4.0
        )

        return BookingFeatures(
            nights=nights,
            adults=int(reservation_data.get("adults", 0) or 0),
            children=int(reservation_data.get("children", 0) or 0),
            infants=int(reservation_data.get("infants", 0) or 0),
            pets=int(reservation_data.get("pets", 0) or 0),
            booking_value=booking_value,
            adr=round(adr, 2),
            fees_total=fees,
            lead_time_hours=lead_time,
            booking_source=str(reservation_data.get("source", "")),
            payment_status=str(reservation_data.get("payment_status", "")),
            id_verified=bool(reservation_data.get("id_verified", False)),
            gap_before_nights=gap_before,
            gap_after_nights=gap_after,
            occupancy_7d=round(occupancy_7d, 3),
            occupancy_30d=round(occupancy_30d, 3),
            season=season,
            same_day_turnover=same_day,
            next_booking_same_day=same_day,
            weekday_mix=weekday_mix,
            hours_before_checkin=hours_before_checkin,
            hours_before_checkout=hours_before_checkout,
            is_within_24h_window=within_24h,
            is_within_4h_window=within_4h,
            reservation_status=str(
                reservation_data.get("status", "")
                or reservation_data.get("reservation_status", "")
            ),
        )

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    def _sum_fees(self, reservation_data: dict[str, Any]) -> float:
        """Sum all fee fields in the reservation.

        Looks for ``fees``, ``cleaning_fee``, ``extra_fees``.

        Args:
            reservation_data: Raw reservation dict.

        Returns:
            Total fee amount.
        """
        total = 0.0
        for key in ("fees", "cleaning_fee", "extra_fees"):
            val = reservation_data.get(key)
            if val is not None:
                if isinstance(val, (int, float)):
                    total += float(val)
                elif isinstance(val, list):
                    total += sum(float(f.get("amount", 0)) for f in val)
        return round(total, 2)

    def _compute_time_to_checkin(
        self,
        reservation_data: dict[str, Any],
        checkin: date | None,
        checkout: date | None,
        now: datetime | None,
    ) -> tuple[float | None, float | None]:
        """Signed hour-deltas from ``now`` to check-in / check-out.

        Positive values mean the boundary lies in the future, negative
        values mean it has already passed.  ``None`` is returned when
        we lack either the anchor or the boundary.

        The reservation may carry richer ``check_in_time`` /
        ``check_out_time`` ISO strings.  We prefer those over the
        bare date so 14:00 vs 11:00 boundaries do not collapse to
        midnight when the deltas matter.

        Args:
            reservation_data: Raw reservation dict.
            checkin: Parsed check-in date (without time).
            checkout: Parsed check-out date (without time).
            now: Anchor timestamp.

        Returns:
            Tuple ``(hours_before_checkin, hours_before_checkout)``.
        """
        if now is None:
            return None, None
        anchor = (
            now.astimezone(timezone.utc)
            if now.tzinfo is not None
            else now.replace(tzinfo=timezone.utc)
        )
        check_in_dt = self._resolve_boundary_dt(
            reservation_data, "check_in_time", checkin,
        )
        check_out_dt = self._resolve_boundary_dt(
            reservation_data, "check_out_time", checkout,
        )
        before_in: float | None = None
        before_out: float | None = None
        if check_in_dt is not None:
            before_in = round(
                (check_in_dt - anchor).total_seconds() / 3600.0, 2,
            )
        if check_out_dt is not None:
            before_out = round(
                (check_out_dt - anchor).total_seconds() / 3600.0, 2,
            )
        return before_in, before_out

    @staticmethod
    def _resolve_boundary_dt(
        reservation_data: dict[str, Any],
        time_key: str,
        fallback: date | None,
    ) -> datetime | None:
        """Return the most precise UTC datetime for a date boundary.

        Tries ``reservation_data[time_key]`` (full ISO with HH:MM)
        first; falls back to ``fallback`` at 14:00 UTC, the
        industry-standard short-stay check-in time, when only a date
        is known.  Returns ``None`` when neither is available.
        """
        raw = reservation_data.get(time_key)
        if raw:
            parsed = _parse_datetime(str(raw))
            if parsed is not None:
                return (
                    parsed.astimezone(timezone.utc)
                    if parsed.tzinfo is not None
                    else parsed.replace(tzinfo=timezone.utc)
                )
        if fallback is None:
            return None
        return datetime(
            fallback.year,
            fallback.month,
            fallback.day,
            14, 0, 0,
            tzinfo=timezone.utc,
        )

    def _compute_lead_time(
        self,
        reservation_data: dict[str, Any],
        checkin: date | None,
    ) -> float:
        """Hours between booking creation and check-in.

        Args:
            reservation_data: Raw reservation with ``created_at``.
            checkin: Parsed check-in date.

        Returns:
            Lead time in hours, or 0.0 if data is missing.
        """
        if checkin is None:
            return 0.0
        created_str = reservation_data.get("created_at", "")
        if not created_str:
            return 0.0
        created = _parse_datetime(str(created_str))
        if created is None:
            return 0.0
        checkin_dt = datetime(
            checkin.year, checkin.month, checkin.day,
            tzinfo=timezone.utc,
        )
        delta = checkin_dt - created
        return max(round(delta.total_seconds() / 3600, 1), 0.0)

    def _compute_gaps(
        self,
        calendar_data: dict[str, Any],
        checkin: date | None,
        checkout: date | None,
    ) -> tuple[int, int]:
        """Compute vacant-night gaps before check-in and after check-out.

        Scans calendar entries for the nearest occupied dates surrounding
        the reservation.

        Args:
            calendar_data: Calendar availability dict with ``dates`` list.
            checkin: Check-in date.
            checkout: Check-out date.

        Returns:
            Tuple of (gap_before_nights, gap_after_nights).
        """
        if checkin is None or checkout is None:
            return 0, 0

        occupied_dates = _extract_occupied_dates(calendar_data)
        if not occupied_dates:
            return 0, 0

        gap_before = 0
        probe = checkin - timedelta(days=1)
        while probe not in occupied_dates and gap_before < 30:
            gap_before += 1
            probe -= timedelta(days=1)

        gap_after = 0
        probe = checkout
        while probe not in occupied_dates and gap_after < 30:
            gap_after += 1
            probe += timedelta(days=1)

        return gap_before, gap_after

    def _compute_weekday_mix(
        self,
        checkin: date | None,
        checkout: date | None,
    ) -> WeekdayMix:
        """Count weekday vs. weekend nights in the stay.

        Friday and Saturday nights are counted as weekend.

        Args:
            checkin: Check-in date.
            checkout: Check-out date.

        Returns:
            WeekdayMix with counts.
        """
        if checkin is None or checkout is None:
            return WeekdayMix()
        weekday = 0
        weekend = 0
        current = checkin
        while current < checkout:
            if current.isoweekday() in _WEEKEND_DAYS:
                weekend += 1
            else:
                weekday += 1
            current += timedelta(days=1)
        return WeekdayMix(weekday_count=weekday, weekend_count=weekend)

    def _compute_occupancy(
        self,
        calendar_data: dict[str, Any],
        reference_date_str: str,
        window: int,
    ) -> float:
        """Compute occupancy rate around a reference date.

        Args:
            calendar_data: Calendar availability dict.
            reference_date_str: ISO date string as center of the window.
            window: Number of days in the window.

        Returns:
            Occupancy rate (0.0–1.0).
        """
        ref = _parse_date(reference_date_str)
        if ref is None:
            return 0.0

        occupied = _extract_occupied_dates(calendar_data)
        start = ref - timedelta(days=window // 2)
        count = 0
        for offset in range(window):
            if (start + timedelta(days=offset)) in occupied:
                count += 1
        return count / window if window > 0 else 0.0

    def _detect_season(
        self,
        checkin: date | None,
        property_id: str,
    ) -> str:
        """Classify the season for a given check-in date.

        Uses a simple month-based heuristic.  Property-specific season
        maps should be loaded from SemanticMemory in production.

        Args:
            checkin: Check-in date.
            property_id: Property identifier (for future per-property maps).

        Returns:
            Season label (high, low, standard, holiday).
        """
        if checkin is None:
            return _DEFAULT_SEASON
        month = checkin.month
        if month in {6, 7, 8}:
            return "high"
        if month in {12, 1}:
            return "holiday"
        if month in {11, 2, 3}:
            return "low"
        return _DEFAULT_SEASON

    def _check_same_day_turnover(
        self,
        calendar_data: dict[str, Any],
        checkout_str: str,
    ) -> bool:
        """Check whether another booking starts on the check-out date.

        Args:
            calendar_data: Calendar availability dict.
            checkout_str: ISO date of check-out.

        Returns:
            True if a new booking starts on the same day.
        """
        checkout = _parse_date(checkout_str)
        if checkout is None:
            return False
        occupied = _extract_occupied_dates(calendar_data)
        return checkout in occupied

    # -------------------------------------------------------------------
    # P5 per-scenario feature branches (ali.md §6)
    # -------------------------------------------------------------------

    def build_for_scenario(
        self,
        scenario: Scenario,
        reservation_data: dict[str, Any],
        calendar_data: dict[str, Any],
        *,
        case_context: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return base features merged with per-scenario branch features.

        Base features come from :meth:`build` (always emitted).  Per-
        scenario branch features cover the additional inputs that
        ali.md §6 calls for: booked vs. stated guest count for
        ``guest_count_mismatch``, requested amenity / vendor / inventory
        for ``amenity_exception``, ``fills_orphan_gap`` and friends
        for ``orphan_night_exception``.  Scenarios without a
        registered branch return the base feature set unchanged, so
        adding new branches is purely additive.

        Branch outputs are flat ``dict[str, Any]`` slices that ride
        on top of :meth:`BookingFeatures.to_dict`.  Where ali.md uses
        a name that aliases a base feature (e.g. ``requested_nights``
        ≡ ``nights``, ``lead_time_days`` ≡ ``lead_time_hours / 24``)
        the branch emits the alias verbatim so downstream rules can
        consume the ali.md vocabulary without re-reading the base
        dict.

        Args:
            scenario: Scenario whose branch should be applied.
            reservation_data: PMS reservation object — same shape as
                :meth:`build` consumes.
            calendar_data: Calendar availability — same shape as
                :meth:`build` consumes.
            case_context: Free-form per-case context dictionary the
                branch readers extract from (typically populated by
                ``case_builder._extract_entities``).  Recognised
                keys per scenario are documented on each
                ``_features_for_*`` method.
            now: Optional anchor passed through to :meth:`build`.

        Returns:
            Flattened feature dict ready for ``PatternRule``
            condition evaluation.
        """
        base = self.build(reservation_data, calendar_data, now=now)
        flat = base.to_dict()
        branch = self._scenario_features(
            scenario,
            base,
            case_context or {},
        )
        flat.update(branch)
        return flat

    def _scenario_features(
        self,
        scenario: Scenario,
        base: BookingFeatures,
        case_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Dispatch to the per-scenario branch builder, if any."""
        if scenario is Scenario.GUEST_COUNT_MISMATCH:
            return self._features_for_guest_count_mismatch(
                base, case_context,
            )
        if scenario is Scenario.AMENITY_EXCEPTION:
            return self._features_for_amenity_exception(
                base, case_context,
            )
        if scenario is Scenario.ORPHAN_NIGHT_EXCEPTION:
            return self._features_for_orphan_night(base, case_context)
        return {}

    @staticmethod
    def _features_for_guest_count_mismatch(
        base: BookingFeatures,
        case_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the ali.md §6 guest_count_mismatch feature slice.

        Recognised ``case_context`` keys (all optional; unknowns
        degrade the branch to the safest baseline rather than
        fabricating values):

        * ``stated_guest_count``   — guest's claimed count (int)
        * ``max_occupancy``        — property cap (int)
        * ``extra_guest_fee_after``— guest count above which the
          extra-guest fee applies (int; 0 disables the fee)
        * ``extra_guest_fee_amount`` — per-guest, per-night fee
          (float)
        * ``access_code_already_sent`` — whether the access code
          has already been released (bool)
        * ``previous_guest_count_updates`` — count of prior
          updates on this reservation (int)

        ``booked_guest_count`` reuses :attr:`BookingFeatures.total_guests`.
        ``fee_applicable`` is ``True`` only when the stated count
        exceeds the fee threshold.  ``computed_extra_guest_fee``
        multiplies the billable surplus by the per-night fee and
        the stay length — so a 6-night stay with two extra guests
        and a $25/night fee yields $300, matching ali.md §6's
        worked example.
        """
        stated = case_context.get("stated_guest_count")
        stated_int = int(stated) if stated is not None else None
        threshold = int(case_context.get("extra_guest_fee_after", 0) or 0)
        per_unit = float(
            case_context.get("extra_guest_fee_amount", 0.0) or 0.0,
        )
        nights = max(int(base.nights), 0)
        billable = (
            max(0, stated_int - threshold)
            if (stated_int is not None and threshold > 0)
            else 0
        )
        fee_applicable = (
            stated_int is not None
            and threshold > 0
            and stated_int > threshold
        )
        computed_fee = round(billable * per_unit * nights, 2)
        return {
            "booked_guest_count": base.total_guests,
            "stated_guest_count": stated_int,
            "max_occupancy": int(case_context.get("max_occupancy", 0) or 0),
            "extra_guest_fee_after": threshold,
            "extra_guest_fee_amount": per_unit,
            "fee_applicable": fee_applicable,
            "computed_extra_guest_fee": computed_fee,
            "access_code_already_sent": bool(
                case_context.get("access_code_already_sent", False),
            ),
            "previous_guest_count_updates": int(
                case_context.get("previous_guest_count_updates", 0) or 0,
            ),
        }

    @staticmethod
    def _features_for_amenity_exception(
        base: BookingFeatures,
        case_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the ali.md §6 amenity_exception feature slice.

        Recognised ``case_context`` keys:

        * ``requested_amenity``    — canonical amenity slug (str)
        * ``listed_amenities``     — sequence of amenity slugs the
          property declares (list / tuple / set)
        * ``property_has_inventory`` — whether the property already
          owns the requested item (bool)
        * ``vendor_available``     — whether a same-day vendor can
          deliver (bool)
        * ``guest_type``           — coarse segmentation tag
          (``"family"``, ``"business"``, …)

        ``amenity_listed`` is derived from membership in
        ``listed_amenities``; ``lead_time_days`` aliases the base
        feature in days for direct ali.md vocabulary parity.
        """
        requested = str(case_context.get("requested_amenity", "") or "")
        listed_raw = case_context.get("listed_amenities", ())
        listed: tuple[str, ...]
        if isinstance(listed_raw, (list, tuple, set, frozenset)):
            listed = tuple(str(x) for x in listed_raw)
        else:
            listed = ()
        amenity_listed = bool(requested) and requested in listed
        lead_time_days = round(base.lead_time_hours / 24.0, 2)
        return {
            "requested_amenity": requested,
            "amenity_listed": amenity_listed,
            "property_has_inventory": bool(
                case_context.get("property_has_inventory", False),
            ),
            "vendor_available": bool(
                case_context.get("vendor_available", False),
            ),
            "lead_time_days": lead_time_days,
            "guest_type": str(case_context.get("guest_type", "") or ""),
        }

    @staticmethod
    def _features_for_orphan_night(
        base: BookingFeatures,
        case_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the ali.md §6 orphan_night feature slice.

        Recognised ``case_context`` keys:

        * ``min_stay``                — property's min-stay rule (int)
        * ``requested_discount_pct``  — discount the guest is
          asking for as a fraction of ADR (float; ``0.0`` when the
          guest did not bring up price)

        ``requested_nights`` aliases :attr:`BookingFeatures.nights`
        for ali.md vocabulary parity.  ``fills_orphan_gap`` is
        ``True`` when the request slots into a tight calendar
        pocket (gap_before == gap_after == 0) and is shorter than
        the property's min-stay — exactly the condition ali.md §6
        names as the orphan-fill signal.  ``can_release_at_discount``
        is a simple convenience flag combining
        ``fills_orphan_gap`` with ``requested_discount_pct == 0`` so
        rules can prefer the no-discount path when the gap is
        already filled.
        """
        min_stay = int(case_context.get("min_stay", 0) or 0)
        nights = max(int(base.nights), 0)
        fills_gap = (
            nights > 0
            and base.gap_before_nights == 0
            and base.gap_after_nights == 0
            and min_stay > 0
            and nights < min_stay
        )
        requested_discount_pct = float(
            case_context.get("requested_discount_pct", 0.0) or 0.0,
        )
        return {
            "requested_nights": nights,
            "min_stay": min_stay,
            "fills_orphan_gap": fills_gap,
            "requested_discount_pct": requested_discount_pct,
            "can_release_at_discount": (
                fills_gap and requested_discount_pct == 0.0
            ),
        }


# ---------------------------------------------------------------------------
# Module-level parsing helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> date | None:
    """Parse an ISO date string, returning None on failure.

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
        logger.debug("unparseable_date", value=value)
        return None


def _parse_datetime(value: str) -> datetime | None:
    """Parse an ISO datetime string, returning None on failure.

    Args:
        value: Datetime string.

    Returns:
        Parsed UTC datetime or None.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        logger.debug("unparseable_datetime", value=value)
        return None


def _extract_occupied_dates(calendar_data: dict[str, Any]) -> set[date]:
    """Extract set of occupied dates from calendar data.

    Supports two formats:
    - ``{"dates": [{"date": "2026-06-01", "status": "occupied"}, …]}``
    - ``{"2026-06-01": "occupied", …}`` (flat dict)

    Args:
        calendar_data: Calendar availability dict.

    Returns:
        Set of occupied dates.
    """
    result: set[date] = set()
    dates_list = calendar_data.get("dates")
    if isinstance(dates_list, list):
        for entry in dates_list:
            if isinstance(entry, dict):
                status = str(entry.get("status", "")).lower()
                if status in {"occupied", "booked", "reserved"}:
                    d = _parse_date(str(entry.get("date", "")))
                    if d is not None:
                        result.add(d)
        return result

    for key, value in calendar_data.items():
        if key == "dates":
            continue
        status = str(value).lower() if isinstance(value, str) else ""
        if status in {"occupied", "booked", "reserved"}:
            d = _parse_date(key)
            if d is not None:
                result.add(d)
    return result
