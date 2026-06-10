"""Lookup the customer/owner identifier that scopes a property (Gap #2).

``PropertyOwnershipResolver`` turns a bare ``property_id`` into the
``customer_id`` that CustomerMemory uses to recall historical events.
It consults the :class:`~brain_engine.patterns.store.DecisionCaseStore`
for the most recent case with a non-empty ``owner_id``, and caches the
result so subsequent calls avoid a store round-trip.

The resolver fails open: when the store is missing or raises, it
returns ``None`` and the caller falls back to the original v1
behaviour (ops + incidents only).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

__all__ = [
    "OwnershipLookupStore",
    "PropertyOwnershipResolver",
]


logger = structlog.get_logger(__name__)


@runtime_checkable
class OwnershipLookupStore(Protocol):
    """Subset of :class:`DecisionCaseStore` the resolver needs.

    Declared separately so tests can inject a lightweight fake without
    pulling in the full :class:`DecisionCaseStore` Protocol surface.
    """

    async def search(
        self,
        *,
        scenario: object | None = None,
        property_id: str | None = None,
        owner_id: str | None = None,
        stage: object | None = None,
        limit: int = 100,
    ) -> list[object]:
        """Return cases matching the given filters."""
        ...


class PropertyOwnershipResolver:
    """Resolve ``property_id`` → ``owner_id`` via the DecisionCase store.

    A small positive/negative cache keeps repeated lookups cheap inside
    one process lifetime.  The cache is intentionally unbounded — the
    key space is the number of properties in the portfolio, which is
    always small.
    """

    def __init__(
        self,
        case_store: OwnershipLookupStore,
        *,
        lookup_limit: int = 50,
    ) -> None:
        if lookup_limit < 1:
            raise ValueError("lookup_limit must be >= 1")
        self._store = case_store
        self._lookup_limit = int(lookup_limit)
        self._cache: dict[str, str] = {}

    async def resolve(self, property_id: str | None) -> str | None:
        """Return the cached or freshly-looked-up owner id.

        ``None`` means: either ``property_id`` is empty, or the store
        returned nothing, or the store raised.  In every case the
        caller should proceed without a customer scope.
        """
        if not property_id:
            return None
        cached = self._cache.get(property_id)
        if cached is not None:
            return cached or None

        try:
            cases = await self._store.search(
                property_id=property_id,
                limit=self._lookup_limit,
            )
        except Exception as exc:  # noqa: BLE001 - fail open
            logger.warning(
                "narrative.ownership.lookup_failed",
                property_id=property_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        for case in cases:
            owner = getattr(case, "owner_id", "") or ""
            if owner:
                self._cache[property_id] = owner
                return owner

        self._cache[property_id] = ""
        return None
