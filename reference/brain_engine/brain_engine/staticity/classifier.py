"""Staticity classification — determines whether data fields are safe to cache.

In property management, some data never changes (max occupancy), some
changes rarely (WiFi password, house rules), some changes frequently
(calendar availability), and some is secret and must be fetched live
every time (door access codes after lock resets).

StaticityClassifier answers: "Given this field and this property, should
I use a cached value or fetch live from PMS?"

This prevents the engine from confidently returning a stale access code
that was changed after a lock reset — a real incident from Cendra ops.

Four levels:
- **STATIC_SAFE**: Never changes (max occupancy, address, property type).
- **STATIC_VERIFY_PERIODICALLY**: Rarely changes, verify every N hours.
- **DYNAMIC_FETCH_LIVE**: Changes frequently, always fetch live.
- **SECRET_DYNAMIC_FETCH_ONLY**: Sensitive + dynamic, never cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Staticity levels
# ---------------------------------------------------------------------------

class StaticityLevel(StrEnum):
    """How static a data field is — determines caching strategy.

    Ordered from most stable to most volatile.
    """

    STATIC_SAFE = "static_safe"
    STATIC_VERIFY_PERIODICALLY = "static_verify_periodically"
    DYNAMIC_FETCH_LIVE = "dynamic_fetch_live"
    SECRET_DYNAMIC_FETCH_ONLY = "secret_dynamic_fetch_only"


# ---------------------------------------------------------------------------
# Field staticity metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FieldStaticity:
    """Staticity classification for a specific data field.

    Attributes:
        field_name: Name of the data field (e.g. "access_code", "max_guests").
        level: Staticity classification.
        source_of_truth: Where to fetch the authoritative value
            (e.g. "pms_api", "owner_profile", "lock_system").
        verify_interval_hours: How often to re-verify (for STATIC_VERIFY).
        last_verified: When this field was last verified.
        change_count: Number of times this field has changed for this property.
        high_risk_if_stale: Whether serving stale data carries operational risk.
    """

    field_name: str
    level: StaticityLevel
    source_of_truth: str = ""
    verify_interval_hours: float = 24.0
    last_verified: datetime | None = None
    change_count: int = 0
    high_risk_if_stale: bool = False

    @property
    def needs_verification(self) -> bool:
        """Whether this field should be re-verified now.

        Returns True if:
        - Level is DYNAMIC or SECRET (always verify).
        - Level is STATIC_VERIFY and interval has elapsed.
        - Never been verified.
        """
        if self.level in {
            StaticityLevel.DYNAMIC_FETCH_LIVE,
            StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY,
        }:
            return True
        if self.level == StaticityLevel.STATIC_SAFE:
            return False
        if self.last_verified is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_verified).total_seconds()
        return elapsed > self.verify_interval_hours * 3600

    @property
    def is_cacheable(self) -> bool:
        """Whether the value can be served from cache."""
        return self.level in {
            StaticityLevel.STATIC_SAFE,
            StaticityLevel.STATIC_VERIFY_PERIODICALLY,
        }

    def __repr__(self) -> str:
        return (
            f"FieldStaticity({self.field_name}, "
            f"level={self.level.value}, "
            f"changes={self.change_count})"
        )


# ---------------------------------------------------------------------------
# Default field classifications
# ---------------------------------------------------------------------------

_DEFAULT_CLASSIFICATIONS: Final[dict[str, tuple[StaticityLevel, str, bool]]] = {
    # STATIC_SAFE — never changes
    "max_guests": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "max_occupancy": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "address": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "property_type": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "bedrooms": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "bathrooms": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "latitude": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "longitude": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "owner_id": (StaticityLevel.STATIC_SAFE, "pms_property", False),
    "currency": (StaticityLevel.STATIC_SAFE, "pms_property", False),

    # STATIC_VERIFY_PERIODICALLY — changes rarely
    "wifi_password": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", True),
    "wifi_name": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "house_rules": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "check_in_instructions": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", True),
    "parking_instructions": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "min_stay": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "base_price": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "cleaning_fee": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "amenities": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "pet_policy": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "cancellation_policy": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "pms_property", False),
    "owner_phone": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "owner_profile", False),
    "owner_email": (StaticityLevel.STATIC_VERIFY_PERIODICALLY, "owner_profile", False),

    # DYNAMIC_FETCH_LIVE — changes frequently
    "calendar_availability": (StaticityLevel.DYNAMIC_FETCH_LIVE, "pms_calendar", False),
    "reservation_status": (StaticityLevel.DYNAMIC_FETCH_LIVE, "pms_reservation", False),
    "payment_status": (StaticityLevel.DYNAMIC_FETCH_LIVE, "pms_reservation", True),
    "guest_count": (StaticityLevel.DYNAMIC_FETCH_LIVE, "pms_reservation", False),
    "cleaning_status": (StaticityLevel.DYNAMIC_FETCH_LIVE, "ops_system", True),
    "maintenance_status": (StaticityLevel.DYNAMIC_FETCH_LIVE, "ops_system", False),
    "current_price": (StaticityLevel.DYNAMIC_FETCH_LIVE, "pms_pricing", False),

    # SECRET_DYNAMIC_FETCH_ONLY — sensitive + volatile
    "access_code": (StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY, "lock_system", True),
    "door_code": (StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY, "lock_system", True),
    "gate_code": (StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY, "lock_system", True),
    "alarm_code": (StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY, "lock_system", True),
    "safe_code": (StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY, "lock_system", True),
    "lockbox_code": (StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY, "lock_system", True),
}

# Verification intervals per level (hours).
_VERIFY_INTERVALS: Final[dict[StaticityLevel, float]] = {
    StaticityLevel.STATIC_SAFE: 0.0,
    StaticityLevel.STATIC_VERIFY_PERIODICALLY: 24.0,
    StaticityLevel.DYNAMIC_FETCH_LIVE: 0.0,
    StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY: 0.0,
}


# ---------------------------------------------------------------------------
# StaticityClassifier
# ---------------------------------------------------------------------------

class StaticityClassifier:
    """Classifies data fields by their volatility and caching safety.

    Uses a default classification table, augmented by per-property
    change history.  Fields that change more often than expected are
    automatically promoted to a higher volatility level.

    Attributes:
        _change_history: Per-property, per-field change count.
        _overrides: Per-property classification overrides.
        _log: Bound structured logger.
    """

    _PROMOTION_THRESHOLD: Final[int] = 3

    def __init__(self) -> None:
        self._change_history: dict[str, dict[str, int]] = {}
        self._overrides: dict[str, dict[str, StaticityLevel]] = {}
        self._last_verified: dict[str, dict[str, datetime]] = {}
        self._log = logger.bind(component="staticity_classifier")

    def classify(
        self,
        field_name: str,
        property_id: str,
    ) -> FieldStaticity:
        """Classify a data field for a specific property.

        Checks overrides first, then the default table, then applies
        change-history-based promotion.

        Args:
            field_name: Name of the field to classify.
            property_id: Property identifier.

        Returns:
            FieldStaticity with the classification.
        """
        override = self._overrides.get(property_id, {}).get(field_name)
        if override is not None:
            default = _DEFAULT_CLASSIFICATIONS.get(field_name)
            source = default[1] if default else ""
            high_risk = default[2] if default else False
            return FieldStaticity(
                field_name=field_name,
                level=override,
                source_of_truth=source,
                verify_interval_hours=_VERIFY_INTERVALS.get(override, 24.0),
                last_verified=self._get_last_verified(field_name, property_id),
                change_count=self._get_change_count(field_name, property_id),
                high_risk_if_stale=high_risk,
            )

        default = _DEFAULT_CLASSIFICATIONS.get(field_name)
        if default is None:
            return FieldStaticity(
                field_name=field_name,
                level=StaticityLevel.DYNAMIC_FETCH_LIVE,
                source_of_truth="unknown",
                high_risk_if_stale=True,
            )

        level, source, high_risk = default
        change_count = self._get_change_count(field_name, property_id)

        level = self._apply_promotion(level, change_count)

        return FieldStaticity(
            field_name=field_name,
            level=level,
            source_of_truth=source,
            verify_interval_hours=_VERIFY_INTERVALS.get(level, 24.0),
            last_verified=self._get_last_verified(field_name, property_id),
            change_count=change_count,
            high_risk_if_stale=high_risk,
        )

    def should_fetch_live(
        self,
        field_name: str,
        property_id: str,
    ) -> bool:
        """Quick check: should this field be fetched live from PMS?

        Args:
            field_name: Data field name.
            property_id: Property identifier.

        Returns:
            True if the field should be fetched live.
        """
        classification = self.classify(field_name, property_id)
        return classification.needs_verification

    def record_change(
        self,
        field_name: str,
        property_id: str,
    ) -> None:
        """Record that a field value changed for a property.

        This increments the change counter, which may trigger automatic
        promotion to a higher volatility level.

        Args:
            field_name: Data field name.
            property_id: Property identifier.
        """
        prop_history = self._change_history.setdefault(property_id, {})
        current = prop_history.get(field_name, 0)
        prop_history[field_name] = current + 1

        new_count = current + 1
        if new_count >= self._PROMOTION_THRESHOLD:
            self._auto_promote(field_name, property_id, new_count)

        self._log.debug(
            "field_change_recorded",
            field=field_name,
            property_id=property_id,
            change_count=new_count,
        )

    def mark_verified(
        self,
        field_name: str,
        property_id: str,
    ) -> None:
        """Record that a field was verified against the source of truth.

        Args:
            field_name: Data field name.
            property_id: Property identifier.
        """
        prop_verified = self._last_verified.setdefault(property_id, {})
        prop_verified[field_name] = datetime.now(timezone.utc)

    def set_override(
        self,
        field_name: str,
        property_id: str,
        level: StaticityLevel,
    ) -> None:
        """Set a manual classification override for a specific property.

        Overrides take precedence over defaults and auto-promotion.

        Args:
            field_name: Data field name.
            property_id: Property identifier.
            level: Override staticity level.
        """
        prop_overrides = self._overrides.setdefault(property_id, {})
        prop_overrides[field_name] = level
        self._log.info(
            "staticity_override_set",
            field=field_name,
            property_id=property_id,
            level=level.value,
        )

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    def _get_change_count(
        self,
        field_name: str,
        property_id: str,
    ) -> int:
        """Get the change count for a field at a property.

        Args:
            field_name: Data field name.
            property_id: Property identifier.

        Returns:
            Number of recorded changes.
        """
        return self._change_history.get(property_id, {}).get(field_name, 0)

    def _get_last_verified(
        self,
        field_name: str,
        property_id: str,
    ) -> datetime | None:
        """Get the last verification timestamp for a field.

        Args:
            field_name: Data field name.
            property_id: Property identifier.

        Returns:
            Last verification datetime or None.
        """
        return self._last_verified.get(property_id, {}).get(field_name)

    def _apply_promotion(
        self,
        level: StaticityLevel,
        change_count: int,
    ) -> StaticityLevel:
        """Promote a field to a higher volatility level based on changes.

        If a STATIC_SAFE field has changed 3+ times, it's promoted to
        STATIC_VERIFY.  If STATIC_VERIFY has changed 3+ times beyond
        that, it's promoted to DYNAMIC.

        Args:
            level: Current staticity level.
            change_count: Number of recorded changes.

        Returns:
            Possibly promoted level.
        """
        if change_count < self._PROMOTION_THRESHOLD:
            return level

        if level == StaticityLevel.STATIC_SAFE:
            return StaticityLevel.STATIC_VERIFY_PERIODICALLY
        if level == StaticityLevel.STATIC_VERIFY_PERIODICALLY:
            if change_count >= self._PROMOTION_THRESHOLD * 2:
                return StaticityLevel.DYNAMIC_FETCH_LIVE
        return level

    def _auto_promote(
        self,
        field_name: str,
        property_id: str,
        change_count: int,
    ) -> None:
        """Automatically promote a field based on change frequency.

        Logs a warning when a field is promoted, since this may indicate
        an unexpected data pattern (e.g. access code changed frequently
        after multiple lock resets).

        Args:
            field_name: Data field name.
            property_id: Property identifier.
            change_count: Current change count.
        """
        default = _DEFAULT_CLASSIFICATIONS.get(field_name)
        if default is None:
            return

        original_level = default[0]
        promoted = self._apply_promotion(original_level, change_count)

        if promoted != original_level:
            self._log.warning(
                "field_auto_promoted",
                field=field_name,
                property_id=property_id,
                from_level=original_level.value,
                to_level=promoted.value,
                change_count=change_count,
            )
