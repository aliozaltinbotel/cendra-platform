"""Postgres-backed persistence for :class:`OwnerFlexibilityProfile`.

Uses ``asyncpg`` with a JSONB codec registered on every connection
so each field-group dict / list round-trips natively into its JSONB
column.  The schema is declared in
``deploy/postgres-migrations.yaml`` (migration
``014_owner_flexibility_profiles.sql``).

Concurrency: :meth:`PgOwnerProfileStore.put` uses ``SELECT … FOR
UPDATE`` inside a single transaction to compare-and-swap on
``version``.  When ``expected_version`` is supplied and the
persisted row has moved on, :class:`VersionConflictError` is raised
so the caller can re-read and retry.  When omitted the call is a
plain upsert and ``version`` is monotonically incremented.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.owner_profile.models import (
    AmenityException,
    ApprovalThresholds,
    CheckInRules,
    FeeRules,
    Flexibility,
    LocalRecommendation,
    OccupancyCapacity,
    OwnerFlexibilityProfile,
    SOURCES_OF_TRUTH,
    SourceOfTruth,
    StayRules,
)
from brain_engine.owner_profile.store import VersionConflictError

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------


_SELECT_COLUMNS: Final[str] = (
    "owner_id, property_id, tenant_id, "
    "occupancy_capacity, fee_rules, stay_rules, checkin_rules, "
    "amenity_exceptions, flexibility, approval_thresholds, "
    "local_recommendations, "
    "source_of_truth, version, updated_at"
)

_SELECT_BY_KEY_SQL: Final[str] = (  # noqa: S608
    f"SELECT {_SELECT_COLUMNS} "
    "FROM owner_flexibility_profiles "
    "WHERE owner_id = $1 AND property_id = $2"
)

_SELECT_FOR_UPDATE_SQL: Final[str] = (  # noqa: S608
    "SELECT version FROM owner_flexibility_profiles "
    "WHERE owner_id = $1 AND property_id = $2 FOR UPDATE"
)

_LIST_FOR_OWNER_SQL: Final[str] = (  # noqa: S608
    f"SELECT {_SELECT_COLUMNS} "
    "FROM owner_flexibility_profiles "
    "WHERE owner_id = $1 "
    "ORDER BY property_id ASC"
)

_UPSERT_SQL: Final[str] = """
INSERT INTO owner_flexibility_profiles (
    owner_id, property_id, tenant_id,
    occupancy_capacity, fee_rules, stay_rules, checkin_rules,
    amenity_exceptions, flexibility, approval_thresholds,
    local_recommendations,
    source_of_truth, version, updated_at
)
VALUES (
    $1, $2, $3,
    $4, $5, $6, $7,
    $8, $9, $10,
    $11,
    $12, $13, now()
)
ON CONFLICT (owner_id, property_id) DO UPDATE SET
    tenant_id             = EXCLUDED.tenant_id,
    occupancy_capacity    = EXCLUDED.occupancy_capacity,
    fee_rules             = EXCLUDED.fee_rules,
    stay_rules            = EXCLUDED.stay_rules,
    checkin_rules         = EXCLUDED.checkin_rules,
    amenity_exceptions    = EXCLUDED.amenity_exceptions,
    flexibility           = EXCLUDED.flexibility,
    approval_thresholds   = EXCLUDED.approval_thresholds,
    local_recommendations = EXCLUDED.local_recommendations,
    source_of_truth       = EXCLUDED.source_of_truth,
    version               = EXCLUDED.version,
    updated_at            = now()
RETURNING version, updated_at
"""


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register a JSON codec for the JSONB columns."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_owner_profile_pool(
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


def _occupancy_to_dict(value: OccupancyCapacity) -> dict[str, Any]:
    return {
        "max_guests": value.max_guests,
        "max_adults": value.max_adults,
        "max_children": value.max_children,
        "infants_count_as_guests": value.infants_count_as_guests,
        "pets_allowed": value.pets_allowed,
    }


def _occupancy_from_dict(payload: dict[str, Any]) -> OccupancyCapacity:
    return OccupancyCapacity(
        max_guests=_optional_int(payload.get("max_guests")),
        max_adults=_optional_int(payload.get("max_adults")),
        max_children=_optional_int(payload.get("max_children")),
        infants_count_as_guests=_optional_bool(
            payload.get("infants_count_as_guests"),
        ),
        pets_allowed=_optional_bool(payload.get("pets_allowed")),
    )


def _fees_to_dict(value: FeeRules) -> dict[str, Any]:
    return {
        "extra_guest_fee": value.extra_guest_fee,
        "child_fee": value.child_fee,
        "infant_fee": value.infant_fee,
        "pet_fee": value.pet_fee,
        "cleaning_fee": value.cleaning_fee,
    }


def _fees_from_dict(payload: dict[str, Any]) -> FeeRules:
    return FeeRules(
        extra_guest_fee=_optional_float(payload.get("extra_guest_fee")),
        child_fee=_optional_float(payload.get("child_fee")),
        infant_fee=_optional_float(payload.get("infant_fee")),
        pet_fee=_optional_float(payload.get("pet_fee")),
        cleaning_fee=_optional_float(payload.get("cleaning_fee")),
    )


def _stay_to_dict(value: StayRules) -> dict[str, Any]:
    return {
        "default_min_stay": value.default_min_stay,
        "hard_min_stay_floor": value.hard_min_stay_floor,
        "max_stay": value.max_stay,
        "advance_booking_window": value.advance_booking_window,
    }


def _stay_from_dict(payload: dict[str, Any]) -> StayRules:
    return StayRules(
        default_min_stay=_optional_int(payload.get("default_min_stay")),
        hard_min_stay_floor=_optional_int(payload.get("hard_min_stay_floor")),
        max_stay=_optional_int(payload.get("max_stay")),
        advance_booking_window=_optional_int(
            payload.get("advance_booking_window"),
        ),
    )


def _checkin_to_dict(value: CheckInRules) -> dict[str, Any]:
    return {
        "early_checkin_policy": value.early_checkin_policy,
        "late_checkout_policy": value.late_checkout_policy,
        "std_checkin_time": value.std_checkin_time,
        "std_checkout_time": value.std_checkout_time,
    }


def _checkin_from_dict(payload: dict[str, Any]) -> CheckInRules:
    return CheckInRules(
        early_checkin_policy=str(payload.get("early_checkin_policy") or ""),
        late_checkout_policy=str(payload.get("late_checkout_policy") or ""),
        std_checkin_time=str(payload.get("std_checkin_time") or ""),
        std_checkout_time=str(payload.get("std_checkout_time") or ""),
    )


def _amenity_to_dict(value: AmenityException) -> dict[str, Any]:
    return {
        "amenity_code": value.amenity_code,
        "available": value.available,
        "notes": value.notes,
    }


def _amenity_from_dict(payload: dict[str, Any]) -> AmenityException:
    return AmenityException(
        amenity_code=str(payload.get("amenity_code") or ""),
        available=bool(payload.get("available")),
        notes=str(payload.get("notes") or ""),
    )


def _local_rec_to_dict(value: LocalRecommendation) -> dict[str, Any]:
    return {
        "category": value.category,
        "name": value.name,
        "distance": value.distance,
        "notes": value.notes,
    }


def _local_rec_from_dict(payload: dict[str, Any]) -> LocalRecommendation:
    return LocalRecommendation(
        category=str(payload.get("category") or ""),
        name=str(payload.get("name") or ""),
        distance=str(payload.get("distance") or ""),
        notes=str(payload.get("notes") or ""),
    )


def _flexibility_to_dict(value: Flexibility) -> dict[str, Any]:
    return {
        "discount_ceiling_pct": value.discount_ceiling_pct,
        "extension_default": value.extension_default,
        "gap_night_threshold": value.gap_night_threshold,
    }


def _flexibility_from_dict(payload: dict[str, Any]) -> Flexibility:
    return Flexibility(
        discount_ceiling_pct=_optional_float(payload.get("discount_ceiling_pct")),
        extension_default=_optional_int(payload.get("extension_default")),
        gap_night_threshold=_optional_int(payload.get("gap_night_threshold")),
    )


def _thresholds_to_dict(value: ApprovalThresholds) -> dict[str, Any]:
    return {
        "auto_approve_below_eur": value.auto_approve_below_eur,
        "escalate_above_eur": value.escalate_above_eur,
    }


def _thresholds_from_dict(payload: dict[str, Any]) -> ApprovalThresholds:
    return ApprovalThresholds(
        auto_approve_below_eur=_optional_float(
            payload.get("auto_approve_below_eur"),
        ),
        escalate_above_eur=_optional_float(payload.get("escalate_above_eur")),
    )


def _source_of_truth_from_dict(
    payload: dict[str, Any],
) -> dict[str, SourceOfTruth]:
    """Filter unknown source tags so a corrupt row cannot poison reads."""
    valid = set(SOURCES_OF_TRUTH)
    out: dict[str, SourceOfTruth] = {}
    for key, raw in payload.items():
        if not isinstance(key, str) or not isinstance(raw, str):
            continue
        if raw in valid:
            out[key] = raw  # type: ignore[assignment]
    return out


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _row_to_profile(row: Any) -> OwnerFlexibilityProfile:
    """Hydrate a profile from an ``asyncpg.Record`` row."""
    return OwnerFlexibilityProfile(
        owner_id=str(row["owner_id"]),
        property_id=str(row["property_id"]),
        tenant_id=str(row["tenant_id"] or ""),
        occupancy_capacity=_occupancy_from_dict(
            dict(row["occupancy_capacity"] or {}),
        ),
        fee_rules=_fees_from_dict(dict(row["fee_rules"] or {})),
        stay_rules=_stay_from_dict(dict(row["stay_rules"] or {})),
        checkin_rules=_checkin_from_dict(dict(row["checkin_rules"] or {})),
        amenity_exceptions=tuple(
            _amenity_from_dict(item)
            for item in (row["amenity_exceptions"] or [])
            if isinstance(item, dict)
        ),
        flexibility=_flexibility_from_dict(dict(row["flexibility"] or {})),
        approval_thresholds=_thresholds_from_dict(
            dict(row["approval_thresholds"] or {}),
        ),
        local_recommendations=tuple(
            _local_rec_from_dict(item)
            for item in (row["local_recommendations"] or [])
            if isinstance(item, dict)
        ),
        source_of_truth=_source_of_truth_from_dict(
            dict(row["source_of_truth"] or {}),
        ),
        version=int(row["version"]),
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class PgOwnerProfileStore:
    """Postgres-backed :class:`OwnerProfileStore` implementation.

    Satisfies the :class:`OwnerProfileStore` Protocol structurally —
    no inheritance — so the production store stays decoupled from
    the in-memory reference store.

    By default the store does not own the pool's lifecycle.  When
    constructed via :meth:`from_url` the store owns the pool and
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
        self._log = logger.bind(component="pg_owner_profile_store")

    @classmethod
    async def from_url(
        cls,
        database_url: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> PgOwnerProfileStore:
        """Build a store that owns a freshly-created pool."""
        pool = await create_owner_profile_pool(
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

    # ── OwnerProfileStore Protocol ─────────────────────────────── #

    async def get(
        self,
        owner_id: str,
        property_id: str,
    ) -> OwnerFlexibilityProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_BY_KEY_SQL,
                owner_id,
                property_id,
            )
        if row is None:
            return None
        return _row_to_profile(row)

    async def put(
        self,
        profile: OwnerFlexibilityProfile,
        *,
        expected_version: int | None = None,
    ) -> OwnerFlexibilityProfile:
        amenities_payload = [
            _amenity_to_dict(item) for item in profile.amenity_exceptions
        ]
        local_recs_payload = [
            _local_rec_to_dict(item)
            for item in profile.local_recommendations
        ]
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                _SELECT_FOR_UPDATE_SQL,
                profile.owner_id,
                profile.property_id,
            )
            current_version = int(row["version"]) if row is not None else 0
            if (
                expected_version is not None
                and current_version != expected_version
            ):
                raise VersionConflictError(
                    f"version conflict on "
                    f"({profile.owner_id}, {profile.property_id}): "
                    f"expected {expected_version}, got {current_version}",
                )
            new_version = current_version + 1
            updated_row = await conn.fetchrow(
                _UPSERT_SQL,
                profile.owner_id,
                profile.property_id,
                profile.tenant_id,
                _occupancy_to_dict(profile.occupancy_capacity),
                _fees_to_dict(profile.fee_rules),
                _stay_to_dict(profile.stay_rules),
                _checkin_to_dict(profile.checkin_rules),
                amenities_payload,
                _flexibility_to_dict(profile.flexibility),
                _thresholds_to_dict(profile.approval_thresholds),
                local_recs_payload,
                dict(profile.source_of_truth),
                new_version,
            )
        persisted_at: datetime = updated_row["updated_at"]
        self._log.debug(
            "owner_profile_saved",
            owner_id=profile.owner_id,
            property_id=profile.property_id,
            version=new_version,
            had_cas=expected_version is not None,
        )
        return replace(profile, version=new_version, updated_at=persisted_at)

    async def list_for_owner(
        self,
        owner_id: str,
    ) -> list[OwnerFlexibilityProfile]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_LIST_FOR_OWNER_SQL, owner_id)
        return [_row_to_profile(row) for row in rows]
