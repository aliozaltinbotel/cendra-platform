"""Persistence Protocol for :class:`OwnerFlexibilityProfile` snapshots.

The orchestrator reads owner baselines on every conversation turn,
so the store ships with both a Protocol (for DI) and an in-memory
implementation suitable for tests / dev.  The production
implementation lives in
:mod:`brain_engine.owner_profile.postgres_store`.

Concurrency: :meth:`OwnerProfileStore.put` accepts an optional
``expected_version`` for compare-and-swap writes.  When supplied,
the persisted row's ``version`` must match — otherwise
:class:`VersionConflictError` is raised so the caller can re-read
and retry.  When omitted, the call is a plain upsert and ``version``
is monotonically incremented.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from brain_engine.owner_profile.models import OwnerFlexibilityProfile

__all__ = [
    "InMemoryOwnerProfileStore",
    "OwnerProfileStore",
    "VersionConflictError",
]


class VersionConflictError(RuntimeError):
    """Raised when a CAS write hits a stale ``version``.

    The error message carries the conflicting key plus the expected
    and actual versions so logs make the race obvious without an
    extra round-trip.
    """


@runtime_checkable
class OwnerProfileStore(Protocol):
    """Persistence surface for :class:`OwnerFlexibilityProfile`."""

    async def get(
        self,
        owner_id: str,
        property_id: str,
    ) -> OwnerFlexibilityProfile | None:
        """Return the profile for one (owner, property), or ``None``."""
        ...

    async def put(
        self,
        profile: OwnerFlexibilityProfile,
        *,
        expected_version: int | None = None,
    ) -> OwnerFlexibilityProfile:
        """Upsert ``profile`` keyed by ``(owner_id, property_id)``.

        Args:
            profile: Snapshot to persist.  ``version`` and
                ``updated_at`` on the input are ignored — the store
                computes the next ``version`` itself and stamps
                ``updated_at`` to ``now()``.
            expected_version: When provided, only commit if the
                persisted row's ``version`` equals this value
                (``0`` means "no row yet").

        Returns:
            The persisted profile with its incremented ``version``
            and refreshed ``updated_at``.

        Raises:
            VersionConflictError: When ``expected_version`` is set
                but does not match the persisted row.
        """
        ...

    async def list_for_owner(
        self,
        owner_id: str,
    ) -> list[OwnerFlexibilityProfile]:
        """Return every profile scoped to one owner, sorted by property."""
        ...


class InMemoryOwnerProfileStore:
    """Dev / test :class:`OwnerProfileStore` implementation.

    Writes are serialised through a single ``asyncio.Lock`` so two
    concurrent CAS attempts cannot race past each other inside one
    process.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], OwnerFlexibilityProfile] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        owner_id: str,
        property_id: str,
    ) -> OwnerFlexibilityProfile | None:
        return self._data.get((owner_id, property_id))

    async def put(
        self,
        profile: OwnerFlexibilityProfile,
        *,
        expected_version: int | None = None,
    ) -> OwnerFlexibilityProfile:
        async with self._lock:
            key = (profile.owner_id, profile.property_id)
            existing = self._data.get(key)
            current_version = existing.version if existing is not None else 0
            if (
                expected_version is not None
                and current_version != expected_version
            ):
                raise VersionConflictError(
                    f"version conflict on {key}: "
                    f"expected {expected_version}, got {current_version}",
                )
            persisted = replace(
                profile,
                version=current_version + 1,
                updated_at=datetime.now(timezone.utc),
            )
            self._data[key] = persisted
            return persisted

    async def list_for_owner(
        self,
        owner_id: str,
    ) -> list[OwnerFlexibilityProfile]:
        return sorted(
            (
                profile
                for (key_owner, _), profile in self._data.items()
                if key_owner == owner_id
            ),
            key=lambda p: p.property_id,
        )
