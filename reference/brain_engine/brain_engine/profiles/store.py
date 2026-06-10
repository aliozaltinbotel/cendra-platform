"""Persistence Protocol for :class:`PropertyProfile` snapshots.

The profile is a derived artefact — the authoritative records live
in cendra-pg and ES — so an in-memory store is sufficient for the
knowledge endpoint and the onboarding UI header.  A Postgres-backed
implementation can land later if the snapshot needs to survive pod
restarts or be shared across replicas.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from brain_engine.profiles.models import PropertyProfile

__all__ = [
    "InMemoryPropertyProfileStore",
    "PropertyProfileStore",
]


@runtime_checkable
class PropertyProfileStore(Protocol):
    """Persistence surface for :class:`PropertyProfile` snapshots."""

    async def get(self, property_channel_id: str) -> PropertyProfile | None:
        """Return the profile for one property, or ``None`` when absent."""
        ...

    async def put(self, profile: PropertyProfile) -> None:
        """Upsert a profile keyed by ``property_channel_id``."""
        ...

    async def list_all(self) -> list[PropertyProfile]:
        """Return every stored profile (ordered by ``built_at`` asc)."""
        ...


class InMemoryPropertyProfileStore:
    """Dev / test implementation of :class:`PropertyProfileStore`.

    Writes are serialised through a single ``asyncio.Lock`` so a
    concurrent harvester / read cannot observe a torn state.
    """

    def __init__(self) -> None:
        self._data: dict[str, PropertyProfile] = {}
        self._lock = asyncio.Lock()

    async def get(self, property_channel_id: str) -> PropertyProfile | None:
        return self._data.get(property_channel_id)

    async def put(self, profile: PropertyProfile) -> None:
        async with self._lock:
            self._data[profile.property_channel_id] = profile

    async def list_all(self) -> list[PropertyProfile]:
        return sorted(self._data.values(), key=lambda p: p.built_at)
