"""Owner flexibility baseline — the §10 "preference" tier.

Public surface for the owner-profile package.  Downstream callers
should only need these names and never reach into the helpers in
:mod:`brain_engine.owner_profile.postgres_store`.
"""

from __future__ import annotations

from brain_engine.owner_profile.builder import (
    baseline_from_property_profile,
    overlay_field_groups,
)
from brain_engine.owner_profile.models import (
    FIELD_GROUPS,
    SOURCES_OF_TRUTH,
    AmenityException,
    ApprovalThresholds,
    CheckInRules,
    FeeRules,
    Flexibility,
    OccupancyCapacity,
    OwnerFlexibilityProfile,
    SourceOfTruth,
    StayRules,
)
from brain_engine.owner_profile.postgres_store import (
    PgOwnerProfileStore,
    create_owner_profile_pool,
)
from brain_engine.owner_profile.store import (
    InMemoryOwnerProfileStore,
    OwnerProfileStore,
    VersionConflictError,
)

__all__ = [
    "FIELD_GROUPS",
    "SOURCES_OF_TRUTH",
    "AmenityException",
    "ApprovalThresholds",
    "CheckInRules",
    "FeeRules",
    "Flexibility",
    "InMemoryOwnerProfileStore",
    "OccupancyCapacity",
    "OwnerFlexibilityProfile",
    "OwnerProfileStore",
    "PgOwnerProfileStore",
    "SourceOfTruth",
    "StayRules",
    "VersionConflictError",
    "baseline_from_property_profile",
    "create_owner_profile_pool",
    "overlay_field_groups",
]
