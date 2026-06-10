"""ConfigValidator — Validates that all required data is present before starting a flow.

Checks for missing cleaners, vendor contacts, property configuration,
and other required data before each flow begins execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class GapSeverity(StrEnum):
    """Severity of a detected configuration gap."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class ConfigGap:
    """A detected gap in configuration or data.

    Attributes:
        field: Name of the missing or invalid field.
        severity: How critical this gap is.
        message: Human-readable description of the gap.
        suggestion: Suggested action to resolve the gap.
    """

    field: str
    severity: GapSeverity
    message: str
    suggestion: str = ""


@dataclass(slots=True)
class ValidationResult:
    """Result of configuration validation.

    Attributes:
        valid: Whether all critical checks passed.
        gaps: List of detected gaps.
    """

    valid: bool = True
    gaps: list[ConfigGap] = field(default_factory=list)

    @property
    def critical_gaps(self) -> list[ConfigGap]:
        """Only critical gaps that block execution."""
        return [g for g in self.gaps if g.severity == GapSeverity.CRITICAL]

    @property
    def warnings(self) -> list[ConfigGap]:
        """Warning-level gaps (non-blocking)."""
        return [g for g in self.gaps if g.severity == GapSeverity.WARNING]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "valid": self.valid,
            "gaps": [
                {
                    "field": g.field,
                    "severity": g.severity.value,
                    "message": g.message,
                    "suggestion": g.suggestion,
                }
                for g in self.gaps
            ],
            "critical_count": len(self.critical_gaps),
            "warning_count": len(self.warnings),
        }


# Required fields per flow type
FLOW_REQUIREMENTS: dict[str, list[dict[str, Any]]] = {
    "cleaner_coordination": [
        {
            "field": "cleaner_name",
            "alt_fields": ["cleaners"],
            "severity": "critical",
            "message": "No cleaner configured for this property.",
            "suggestion": "Add a cleaner in config/cleaners.json or contact the property manager.",
        },
        {
            "field": "cleaner_phone",
            "severity": "critical",
            "message": "No cleaner phone number available.",
            "suggestion": "Ask the property manager for cleaner contact information.",
        },
        {
            "field": "property_address",
            "severity": "warning",
            "message": "Property address not set — cleaner may not find the property.",
            "suggestion": "Set the property address in the property configuration.",
        },
    ],
    "late_checkout": [
        {
            "field": "departing_guest_phone",
            "severity": "critical",
            "message": "No phone number for departing guest.",
            "suggestion": "Check the booking details for guest contact information.",
        },
        {
            "field": "standard_checkout_time",
            "severity": "warning",
            "message": "Standard checkout time not set — using default 11:00 AM.",
            "suggestion": "Set checkout time in property configuration.",
        },
    ],
    "damage_claim": [
        {
            "field": "damage_description",
            "severity": "critical",
            "message": "No damage description available.",
            "suggestion": "Run photo inspection flow first to detect damage.",
        },
        {
            "field": "reservation_id",
            "severity": "critical",
            "message": "No reservation ID — required for Airbnb claim submission.",
            "suggestion": "Retrieve reservation ID from the PMS system.",
        },
    ],
    "incident_resolution": [
        {
            "field": "property_id",
            "severity": "critical",
            "message": "No property ID — cannot determine which property to manage.",
            "suggestion": "Set property_id from the booking or PMS data.",
        },
        {
            "field": "guest_name",
            "severity": "warning",
            "message": "Guest name not set.",
            "suggestion": "Retrieve from booking data.",
        },
    ],
}


class ConfigValidator:
    """Validates configuration completeness before flow execution.

    Checks that all required data (cleaners, phones, addresses, etc.)
    is present in the slot manager before a flow can proceed.
    """

    def validate_flow(
        self,
        flow_type: str,
        slots: dict[str, Any],
    ) -> ValidationResult:
        """Validate configuration for a specific flow.

        Args:
            flow_type: Type of flow being started.
            slots: Current slot values from the SlotManager.

        Returns:
            ValidationResult with any detected gaps.
        """
        result = ValidationResult()
        requirements = FLOW_REQUIREMENTS.get(flow_type, [])

        for req in requirements:
            field_name = req["field"]
            alt_fields = req.get("alt_fields", [])
            severity = GapSeverity(req["severity"])

            # Check primary field and alternatives
            has_value = bool(slots.get(field_name))
            if not has_value:
                has_value = any(bool(slots.get(alt)) for alt in alt_fields)

            if not has_value:
                gap = ConfigGap(
                    field=field_name,
                    severity=severity,
                    message=req["message"],
                    suggestion=req.get("suggestion", ""),
                )
                result.gaps.append(gap)
                if severity == GapSeverity.CRITICAL:
                    result.valid = False

        if result.gaps:
            logger.warning(
                "Config validation for %s: %d gaps (%d critical)",
                flow_type, len(result.gaps), len(result.critical_gaps),
            )
        else:
            logger.info("Config validation for %s: all checks passed", flow_type)

        return result

    def validate_cleaner_availability(
        self,
        cleaners: list[dict[str, Any]],
    ) -> ValidationResult:
        """Validate that at least one cleaner is available.

        Args:
            cleaners: List of cleaner configs.

        Returns:
            ValidationResult indicating cleaner availability status.
        """
        result = ValidationResult()

        if not cleaners:
            result.valid = False
            result.gaps.append(ConfigGap(
                field="cleaners",
                severity=GapSeverity.CRITICAL,
                message="No cleaners configured for this property.",
                suggestion="Add cleaners in config/cleaners.json.",
            ))
            return result

        available = [c for c in cleaners if c.get("available", False)]
        if not available:
            result.valid = False
            result.gaps.append(ConfigGap(
                field="cleaners",
                severity=GapSeverity.CRITICAL,
                message=f"All {len(cleaners)} cleaners are unavailable.",
                suggestion=(
                    "Try calling cleaners to confirm availability, "
                    "contact the property manager, or use a marketplace (Turno)."
                ),
            ))

        return result
