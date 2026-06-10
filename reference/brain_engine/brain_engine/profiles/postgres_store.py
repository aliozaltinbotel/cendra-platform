"""Postgres-backed persistence for :class:`PropertyProfile` snapshots.

Production implementation of the :class:`PropertyProfileStore` Protocol
declared in :mod:`brain_engine.profiles.store`.  Uses ``asyncpg`` with a
JSONB codec registered on every connection so that the serialised
profile payload round-trips natively into the ``JSONB`` column.

Schema contract — table ``property_profiles`` as declared in
``deploy/postgres-migrations.yaml`` (migration ``012_property_profiles.sql``).

The store is stateless apart from the injected connection pool; all
queries are parameterised (no string interpolation of user data) and use
``ON CONFLICT (property_channel_id) DO UPDATE`` so :meth:`put` doubles
as a "create-or-replace" primitive — useful when the harvester re-emits
a refreshed :class:`PropertyProfile` snapshot for the same property.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.profiles.models import (
    KnowledgeSection,
    PropertyProfile,
    ReviewAggregate,
)

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_UPSERT_SQL: Final[str] = """
INSERT INTO property_profiles (
    property_channel_id,
    payload,
    built_at,
    updated_at
)
VALUES (
    $1, $2, $3, now()
)
ON CONFLICT (property_channel_id) DO UPDATE SET
    payload    = EXCLUDED.payload,
    built_at   = EXCLUDED.built_at,
    updated_at = now()
"""

_SELECT_BY_ID_SQL: Final[str] = (
    "SELECT property_channel_id, payload, built_at "  # noqa: S608
    "FROM property_profiles WHERE property_channel_id = $1"
)

_SELECT_ALL_SQL: Final[str] = (
    "SELECT property_channel_id, payload, built_at "  # noqa: S608
    "FROM property_profiles ORDER BY built_at ASC"
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register a JSON codec for the ``JSONB`` ``payload`` column."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_property_profiles_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create an ``asyncpg`` pool wired with the JSONB codec.

    Args:
        database_url: Postgres URI (``postgresql://…``).
        min_size: Minimum pool size.
        max_size: Maximum pool size.

    Returns:
        A live asyncpg connection pool.

    Raises:
        ImportError: When ``asyncpg`` is not installed.
    """
    import asyncpg  # local import — optional dependency

    return await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        init=_register_jsonb_codec,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _section_to_dict(section: KnowledgeSection) -> dict[str, Any]:
    return {
        "name": section.name,
        "item_count": section.item_count,
        "last_ingested_at": (
            section.last_ingested_at.isoformat()
            if section.last_ingested_at is not None
            else None
        ),
        "notes": section.notes,
    }


def _section_from_dict(payload: dict[str, Any]) -> KnowledgeSection:
    return KnowledgeSection(
        name=str(payload.get("name") or ""),
        item_count=int(payload.get("item_count") or 0),
        last_ingested_at=_parse_optional_datetime(payload.get("last_ingested_at")),
        notes=str(payload.get("notes") or ""),
    )


def _review_aggregate_to_dict(agg: ReviewAggregate) -> dict[str, Any]:
    return {
        "total": agg.total,
        "with_rating": agg.with_rating,
        "average_rating": agg.average_rating,
        "latest_review_at": (
            agg.latest_review_at.isoformat()
            if agg.latest_review_at is not None
            else None
        ),
    }


def _review_aggregate_from_dict(payload: dict[str, Any]) -> ReviewAggregate:
    avg = payload.get("average_rating")
    return ReviewAggregate(
        total=int(payload.get("total") or 0),
        with_rating=int(payload.get("with_rating") or 0),
        average_rating=(float(avg) if avg is not None else None),
        latest_review_at=_parse_optional_datetime(payload.get("latest_review_at")),
    )


def _profile_to_payload(profile: PropertyProfile) -> dict[str, Any]:
    """Serialise a :class:`PropertyProfile` into a JSONB-friendly dict.

    ``built_at`` lands in the dedicated column, so it is omitted from
    the JSONB payload to avoid double-storing the same value.
    """
    return {
        "property_channel_id": profile.property_channel_id,
        "pms_id": profile.pms_id,
        "customer_id": profile.customer_id,
        "org_id": profile.org_id,
        "provider_type": profile.provider_type,
        "title": profile.title,
        "is_active": profile.is_active,
        "city": profile.city,
        "country": profile.country,
        "property_type": profile.property_type,
        "max_occupancy": profile.max_occupancy,
        "bedrooms": profile.bedrooms,
        "bathrooms": profile.bathrooms,
        "base_currency": profile.base_currency,
        "base_price": profile.base_price,
        "knowledge_percentage": profile.knowledge_percentage,
        "amenity_codes": list(profile.amenity_codes),
        "image_count": profile.image_count,
        "room_count": profile.room_count,
        "description_languages": list(profile.description_languages),
        "reservations": _section_to_dict(profile.reservations),
        "conversations": _section_to_dict(profile.conversations),
        "rate_plans": _section_to_dict(profile.rate_plans),
        "reviews": _section_to_dict(profile.reviews),
        "review_aggregate": _review_aggregate_to_dict(profile.review_aggregate),
        "static_payload": dict(profile.static_payload or {}),
    }


def _payload_to_profile(
    payload: dict[str, Any],
    built_at: datetime,
) -> PropertyProfile:
    """Hydrate a :class:`PropertyProfile` from JSONB + the ``built_at`` column."""
    return PropertyProfile(
        property_channel_id=str(payload.get("property_channel_id") or ""),
        pms_id=str(payload.get("pms_id") or ""),
        customer_id=str(payload.get("customer_id") or ""),
        org_id=str(payload.get("org_id") or ""),
        provider_type=str(payload.get("provider_type") or ""),
        title=str(payload.get("title") or ""),
        is_active=bool(payload.get("is_active")),
        city=str(payload.get("city") or ""),
        country=str(payload.get("country") or ""),
        property_type=str(payload.get("property_type") or ""),
        max_occupancy=int(payload.get("max_occupancy") or 0),
        bedrooms=int(payload.get("bedrooms") or 0),
        bathrooms=float(payload.get("bathrooms") or 0.0),
        base_currency=str(payload.get("base_currency") or ""),
        base_price=float(payload.get("base_price") or 0.0),
        knowledge_percentage=float(payload.get("knowledge_percentage") or 0.0),
        amenity_codes=tuple(payload.get("amenity_codes") or ()),
        image_count=int(payload.get("image_count") or 0),
        room_count=int(payload.get("room_count") or 0),
        description_languages=tuple(payload.get("description_languages") or ()),
        reservations=_section_from_dict(payload.get("reservations") or {}),
        conversations=_section_from_dict(payload.get("conversations") or {}),
        rate_plans=_section_from_dict(payload.get("rate_plans") or {}),
        reviews=_section_from_dict(payload.get("reviews") or {}),
        review_aggregate=_review_aggregate_from_dict(
            payload.get("review_aggregate") or {},
        ),
        static_payload=dict(payload.get("static_payload") or {}),
        built_at=built_at,
    )


def _parse_optional_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 string back to :class:`datetime`, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PgPropertyProfileStore:
    """Postgres-backed :class:`PropertyProfileStore` implementation.

    Satisfies the Protocol defined in :mod:`brain_engine.profiles.store`
    without inheritance — structural typing keeps the production store
    decoupled from the in-memory reference implementation shipped for
    dev / tests.

    By default the store does *not* own the pool's lifecycle.  When
    constructed via :meth:`from_url`, the store does own the pool and
    :meth:`close` releases it.

    Attributes:
        _pool: Injected asyncpg pool.
        _log: Structured logger bound to this component.
        _owns_pool: Whether :meth:`close` should close the pool.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        owns_pool: bool = False,
    ) -> None:
        self._pool = pool
        self._owns_pool = owns_pool
        self._log = logger.bind(component="pg_property_profile_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PgPropertyProfileStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_property_profiles_pool(
            database_url,
            min_size=min_size,
            max_size=max_size,
        )
        return cls(pool, owns_pool=True)

    async def close(self) -> None:
        """Close the underlying pool if this store owns it."""
        if self._owns_pool:
            await self._pool.close()
            self._log.info("pool_closed")

    # ── PropertyProfileStore Protocol ──────────────────────────── #

    async def get(
        self,
        property_channel_id: str,
    ) -> PropertyProfile | None:
        """Return the profile for one property, or ``None`` when absent."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_BY_ID_SQL,
                property_channel_id,
            )
        if row is None:
            return None
        return _payload_to_profile(dict(row["payload"]), row["built_at"])

    async def put(self, profile: PropertyProfile) -> None:
        """Upsert a profile keyed by ``property_channel_id``."""
        payload = _profile_to_payload(profile)
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                profile.property_channel_id,
                payload,
                profile.built_at,
            )
        self._log.debug(
            "profile_saved",
            property_channel_id=profile.property_channel_id,
            static_payload_size=len(profile.static_payload or {}),
        )

    async def list_all(self) -> list[PropertyProfile]:
        """Return every stored profile (ordered by ``built_at`` asc)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_ALL_SQL)
        return [
            _payload_to_profile(dict(row["payload"]), row["built_at"])
            for row in rows
        ]
