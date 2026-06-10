"""Value objects for the owner flexibility baseline.

An :class:`OwnerFlexibilityProfile` is the per-(owner, property)
snapshot the :class:`ExecutionOrchestrator` consults as the
"preference" tier in the §10 priority chain — manual → blocker →
safety → learned → preference → ask.  It captures *how flexible* the
owner is on price, length-of-stay, fees, check-in windows and
amenity exceptions, plus the approval thresholds that gate
auto-bookings.

The profile is partitioned into seven JSONB-friendly field groups so
the harvester (GraphQL-derived defaults) and the pm-correction
writer (sandbox / regenerate) can update one cluster without
rewriting unrelated ones.  Provenance is tracked per field group in
:attr:`OwnerFlexibilityProfile.source_of_truth` so callers can tell
whether a value came from harvested PMS data, a PM correction, or an
explicit owner directive.

Concurrent writers compare-and-swap on :attr:`version`: every commit
bumps it, conflicting writers retry on stale CAS.

Every dataclass here is ``frozen=True, slots=True`` so a profile can
be cached at startup and shared across coroutines without anyone
mutating it underneath the orchestrator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Literal

__all__ = [
    "FIELD_GROUPS",
    "SOURCES_OF_TRUTH",
    "AmenityException",
    "ApprovalThresholds",
    "CheckInRules",
    "FeeRules",
    "Flexibility",
    "LocalRecommendation",
    "OccupancyCapacity",
    "OwnerFlexibilityProfile",
    "SourceOfTruth",
    "StayRules",
]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------


SourceOfTruth = Literal["graphql", "pm_correction", "owner_directive"]
"""Provenance tag: which writer most recently authored a field group."""


SOURCES_OF_TRUTH: Final[tuple[SourceOfTruth, ...]] = (
    "graphql",
    "pm_correction",
    "owner_directive",
)
"""Runtime-iterable view of the :data:`SourceOfTruth` literal."""


FIELD_GROUPS: Final[tuple[str, ...]] = (
    "occupancy_capacity",
    "fee_rules",
    "stay_rules",
    "checkin_rules",
    "amenity_exceptions",
    "flexibility",
    "approval_thresholds",
    "local_recommendations",
)
"""Stable list of JSONB field-group names for source-of-truth keys."""


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Field groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OccupancyCapacity:
    """Hard-cap occupancy rules for the property.

    Attributes:
        max_guests: Maximum total guests the owner allows.
        max_adults: Maximum adults; ``None`` when not separately
            constrained from ``max_guests``.
        max_children: Maximum children; ``None`` when unconstrained.
        infants_count_as_guests: Whether infants count toward
            ``max_guests``.  ``None`` when the owner has not stated.
        pets_allowed: Whether pets are accepted at all.  ``None``
            means "case by case" — the orchestrator escalates to
            "ask" instead of guessing.
    """

    max_guests: int | None = None
    max_adults: int | None = None
    max_children: int | None = None
    infants_count_as_guests: bool | None = None
    pets_allowed: bool | None = None


@dataclass(frozen=True, slots=True)
class FeeRules:
    """Owner-defined surcharges layered on top of the base nightly rate.

    Amounts are stored in :attr:`OwnerFlexibilityProfile`'s base
    currency; currency conversion is the booking pipeline's job, not
    this snapshot's.

    Attributes:
        extra_guest_fee: Per-extra-guest-per-night surcharge above
            the standard occupancy.
        child_fee: Per-child surcharge (when the owner discounts or
            premiums children separately).
        infant_fee: Per-infant surcharge.
        pet_fee: Flat per-stay pet surcharge.
        cleaning_fee: One-off cleaning fee per booking.
    """

    extra_guest_fee: float | None = None
    child_fee: float | None = None
    infant_fee: float | None = None
    pet_fee: float | None = None
    cleaning_fee: float | None = None


@dataclass(frozen=True, slots=True)
class StayRules:
    """Length-of-stay and lead-time constraints.

    Attributes:
        default_min_stay: Soft minimum-stay nights the orchestrator
            should propose by default.
        hard_min_stay_floor: Absolute minimum-stay floor — bookings
            below it are auto-rejected even when the rate plan
            allows.
        max_stay: Hard upper bound on stay length, when the owner
            sets one.
        advance_booking_window: Maximum number of days in the future
            the property accepts bookings.
    """

    default_min_stay: int | None = None
    hard_min_stay_floor: int | None = None
    max_stay: int | None = None
    advance_booking_window: int | None = None


@dataclass(frozen=True, slots=True)
class CheckInRules:
    """Check-in / check-out flexibility windows.

    Time fields are stored as ``HH:MM`` strings — JSONB keeps them
    portable and the orchestrator does no timezone arithmetic on
    them at the preference tier.

    Attributes:
        early_checkin_policy: Free-text policy ("ok within 1h",
            "paid", "case-by-case" …) — the orchestrator parses it
            for keywords.
        late_checkout_policy: Mirror of ``early_checkin_policy``.
        std_checkin_time: Standard check-in time as ``HH:MM``.
        std_checkout_time: Standard check-out time as ``HH:MM``.
    """

    early_checkin_policy: str = ""
    late_checkout_policy: str = ""
    std_checkin_time: str = ""
    std_checkout_time: str = ""


@dataclass(frozen=True, slots=True)
class AmenityException:
    """A single amenity carve-out (positive or negative).

    Attributes:
        amenity_code: Stable amenity code (matches
            :class:`PropertyProfile.amenity_codes`).
        available: ``True`` when the owner explicitly allows it,
            ``False`` when explicitly denied.
        notes: Free-form qualifier ("only on weekends", "fee
            applies") for the orchestrator's prompt builder.
    """

    amenity_code: str
    available: bool
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Flexibility:
    """Numeric flexibility bands the orchestrator may bend within.

    Attributes:
        discount_ceiling_pct: Maximum discount (0.0–1.0) the
            orchestrator may quote without owner approval.
        extension_default: Default number of free / negotiable nights
            offered when guests ask to extend.
        gap_night_threshold: Gap-fill threshold — the maximum
            unbooked-night window the orchestrator may aggressively
            fill at a discount.
    """

    discount_ceiling_pct: float | None = None
    extension_default: int | None = None
    gap_night_threshold: int | None = None


@dataclass(frozen=True, slots=True)
class LocalRecommendation:
    """One owner-curated local place near the property.

    Surfaced into the LLM system prompt by ``_format_owner_flexibility``
    so the agent can answer "nearest restaurant", "best café",
    "pharmacy" guest questions from owner-vetted data instead of
    deferring to PM or hallucinating from training data.

    Attributes:
        category: Short slug — ``"restaurant"``, ``"cafe"``,
            ``"supermarket"``, ``"pharmacy"``, ``"transport"``,
            ``"attraction"``, free-form when the owner introduces
            a new category.  Lower-case to keep prompt rendering
            deterministic.
        name: Display name the owner has approved (e.g. ``"La Casa"``).
        distance: Free-text proximity hint (``"500m"``, ``"5 min walk"``,
            ``"3 stops on metro"``).  Empty when the owner did not
            specify.
        notes: Free-form qualifier the owner wants the agent to
            quote — opening hours, cuisine type, why it's recommended
            ("kid-friendly", "open late", "best espresso in town").
    """

    category: str
    name: str
    distance: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalThresholds:
    """Money-amount thresholds gating auto-approval.

    Attributes:
        auto_approve_below_eur: Bookings whose total revenue is
            strictly less than this value (in EUR-equivalent) may be
            auto-approved by the orchestrator.
        escalate_above_eur: Bookings strictly greater than this
            value are always escalated to the owner.
    """

    auto_approve_below_eur: float | None = None
    escalate_above_eur: float | None = None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerFlexibilityProfile:
    """Per-(owner, property) baseline consulted by the orchestrator.

    Attributes:
        owner_id: Stable owner identifier (Cendra ``ownerId``).
        property_id: Property identifier — typically
            ``propertyChannelId`` so the join with
            :class:`PropertyProfile` is direct.
        tenant_id: Cendra workspace / tenant id; ``""`` when the
            profile is global to the owner.
        occupancy_capacity: :class:`OccupancyCapacity` field group.
        fee_rules: :class:`FeeRules` field group.
        stay_rules: :class:`StayRules` field group.
        checkin_rules: :class:`CheckInRules` field group.
        amenity_exceptions: Tuple of :class:`AmenityException`
            entries.  Order is stable (sorted by amenity code).
        flexibility: :class:`Flexibility` field group.
        approval_thresholds: :class:`ApprovalThresholds` field
            group.
        local_recommendations: Tuple of :class:`LocalRecommendation`
            entries — owner-curated nearby places the LLM may quote
            to answer "nearest restaurant", "best café", etc.
            without deferring to PM or hallucinating.
        source_of_truth: ``field_group_name → SourceOfTruth`` map
            recording who most recently wrote each group.
        version: Monotonically-increasing CAS counter.  Writers bump
            it on every put; conflicting writers retry on stale CAS.
        updated_at: When this snapshot was persisted.
    """

    owner_id: str
    property_id: str
    tenant_id: str = ""
    occupancy_capacity: OccupancyCapacity = field(
        default_factory=OccupancyCapacity,
    )
    fee_rules: FeeRules = field(default_factory=FeeRules)
    stay_rules: StayRules = field(default_factory=StayRules)
    checkin_rules: CheckInRules = field(default_factory=CheckInRules)
    amenity_exceptions: tuple[AmenityException, ...] = ()
    flexibility: Flexibility = field(default_factory=Flexibility)
    approval_thresholds: ApprovalThresholds = field(
        default_factory=ApprovalThresholds,
    )
    local_recommendations: tuple[LocalRecommendation, ...] = ()
    source_of_truth: Mapping[str, SourceOfTruth] = field(default_factory=dict)
    version: int = 1
    updated_at: datetime = field(default_factory=_utc_now)

    def source_for(self, field_group: str) -> SourceOfTruth | None:
        """Return the writer that last touched ``field_group``."""
        return self.source_of_truth.get(field_group)
