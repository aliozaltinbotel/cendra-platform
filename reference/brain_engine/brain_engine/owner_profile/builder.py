"""Builders that materialise :class:`OwnerFlexibilityProfile` snapshots.

Two flows feed the store:

1. :func:`baseline_from_property_profile` — the GraphQL harvester
   takes a :class:`PropertyProfile` (already shaped from
   onboarding-api) and projects an initial baseline.  Every field
   group filled this way is tagged ``"graphql"`` in
   :attr:`OwnerFlexibilityProfile.source_of_truth`.

2. :func:`overlay_field_groups` — the pm-correction writer
   (sandbox / regenerate-pm-knowledge) and the explicit
   owner-directive endpoint each call this with the field groups
   they want to overwrite.  Untouched groups keep their previous
   provenance, so a single PM correction never silently demotes
   harvested data on adjacent groups.

Both helpers are pure functions: they take a snapshot in and return
a new one — the underlying value objects are ``frozen=True`` so the
caller cannot accidentally mutate the input.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from brain_engine.owner_profile.models import (
    AmenityException,
    ApprovalThresholds,
    CheckInRules,
    FIELD_GROUPS,
    FeeRules,
    Flexibility,
    OccupancyCapacity,
    OwnerFlexibilityProfile,
    SourceOfTruth,
    StayRules,
)

if TYPE_CHECKING:
    from brain_engine.profiles.models import PropertyProfile

__all__ = [
    "baseline_from_property_profile",
    "overlay_field_groups",
]


def baseline_from_property_profile(
    *,
    owner_id: str,
    property_id: str,
    tenant_id: str = "",
    property_profile: PropertyProfile | None = None,
) -> OwnerFlexibilityProfile:
    """Project a harvested :class:`PropertyProfile` into a baseline.

    Only the fields that the unified PropertyProfile actually carries
    are populated — everything else stays ``None`` so the
    orchestrator falls through to "ask" instead of guessing.

    Args:
        owner_id: Cendra ``ownerId`` of the property's owner.
        property_id: Property identifier (typically
            ``propertyChannelId``) used as the join key with
            :class:`PropertyProfile`.
        tenant_id: Cendra workspace / tenant id.  Empty string when
            the profile is global to the owner.
        property_profile: Harvested snapshot to project from.  When
            ``None`` only ``owner_id`` / ``property_id`` /
            ``tenant_id`` are set and every field group stays at its
            default.

    Returns:
        A fresh :class:`OwnerFlexibilityProfile` with provenance
        tags set to ``"graphql"`` for every group derived from
        ``property_profile``.  ``version`` is left at its default
        (``1``) — the store stamps the actual persisted version on
        write.
    """
    occupancy = OccupancyCapacity()
    sources: dict[str, SourceOfTruth] = {}

    if property_profile is not None and property_profile.max_occupancy > 0:
        occupancy = OccupancyCapacity(
            max_guests=int(property_profile.max_occupancy),
        )
        sources["occupancy_capacity"] = "graphql"

    return OwnerFlexibilityProfile(
        owner_id=owner_id,
        property_id=property_id,
        tenant_id=tenant_id,
        occupancy_capacity=occupancy,
        source_of_truth=sources,
    )


def overlay_field_groups(
    base: OwnerFlexibilityProfile,
    *,
    source: SourceOfTruth,
    occupancy_capacity: OccupancyCapacity | None = None,
    fee_rules: FeeRules | None = None,
    stay_rules: StayRules | None = None,
    checkin_rules: CheckInRules | None = None,
    amenity_exceptions: tuple[AmenityException, ...] | None = None,
    flexibility: Flexibility | None = None,
    approval_thresholds: ApprovalThresholds | None = None,
) -> OwnerFlexibilityProfile:
    """Return ``base`` with the supplied groups overwritten by ``source``.

    Untouched groups keep their value *and* their existing
    provenance tag in :attr:`OwnerFlexibilityProfile.source_of_truth`.
    Groups that are overwritten get tagged with ``source``.

    Args:
        base: Snapshot to overlay onto.  Not mutated — every value
            object on it is ``frozen=True`` anyway.
        source: Provenance tag for every group passed in.
        occupancy_capacity: New :class:`OccupancyCapacity` group, or
            ``None`` to leave the group untouched.
        fee_rules: New :class:`FeeRules` group, or ``None``.
        stay_rules: New :class:`StayRules` group, or ``None``.
        checkin_rules: New :class:`CheckInRules` group, or ``None``.
        amenity_exceptions: New tuple of :class:`AmenityException`
            entries, or ``None`` to leave them untouched.  Pass an
            empty tuple to *clear* exceptions while still re-tagging
            the group.
        flexibility: New :class:`Flexibility` group, or ``None``.
        approval_thresholds: New :class:`ApprovalThresholds` group,
            or ``None``.

    Returns:
        A new :class:`OwnerFlexibilityProfile`.  ``version`` is left
        unchanged — the store bumps it on write.
    """
    new_sources: dict[str, SourceOfTruth] = dict(base.source_of_truth)

    next_occupancy = base.occupancy_capacity
    if occupancy_capacity is not None:
        next_occupancy = occupancy_capacity
        new_sources["occupancy_capacity"] = source

    next_fees = base.fee_rules
    if fee_rules is not None:
        next_fees = fee_rules
        new_sources["fee_rules"] = source

    next_stay = base.stay_rules
    if stay_rules is not None:
        next_stay = stay_rules
        new_sources["stay_rules"] = source

    next_checkin = base.checkin_rules
    if checkin_rules is not None:
        next_checkin = checkin_rules
        new_sources["checkin_rules"] = source

    next_amenities = base.amenity_exceptions
    if amenity_exceptions is not None:
        next_amenities = amenity_exceptions
        new_sources["amenity_exceptions"] = source

    next_flex = base.flexibility
    if flexibility is not None:
        next_flex = flexibility
        new_sources["flexibility"] = source

    next_thresholds = base.approval_thresholds
    if approval_thresholds is not None:
        next_thresholds = approval_thresholds
        new_sources["approval_thresholds"] = source

    # Drop any provenance keys that no longer correspond to a known
    # field group — defends the row from schema drift downstream
    # accidentally re-writing the source map with stale entries.
    valid_keys = set(FIELD_GROUPS)
    new_sources = {k: v for k, v in new_sources.items() if k in valid_keys}

    return replace(
        base,
        occupancy_capacity=next_occupancy,
        fee_rules=next_fees,
        stay_rules=next_stay,
        checkin_rules=next_checkin,
        amenity_exceptions=next_amenities,
        flexibility=next_flex,
        approval_thresholds=next_thresholds,
        source_of_truth=new_sources,
    )
