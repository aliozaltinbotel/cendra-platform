"""Property knowledge profiles — the "what Brain knows" surface.

Downstream consumers (knowledge endpoint, onboarding UI, sandbox
interview) should only need these public objects and never reach
into the harvester internals.
"""

from __future__ import annotations

from brain_engine.profiles.harvester import (
    HarvestCounts,
    HarvestResult,
    PropertyProfileHarvester,
)
from brain_engine.profiles.models import (
    KnowledgeSection,
    PropertyProfile,
    ReviewAggregate,
)
from brain_engine.profiles.postgres_store import (
    PgPropertyProfileStore,
    create_property_profiles_pool,
)
from brain_engine.profiles.store import (
    InMemoryPropertyProfileStore,
    PropertyProfileStore,
)

__all__ = [
    "HarvestCounts",
    "HarvestResult",
    "InMemoryPropertyProfileStore",
    "KnowledgeSection",
    "PgPropertyProfileStore",
    "PropertyProfile",
    "PropertyProfileHarvester",
    "PropertyProfileStore",
    "ReviewAggregate",
    "create_property_profiles_pool",
]
