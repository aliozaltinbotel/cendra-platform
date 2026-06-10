"""Reg (EU) 2024/1028 STR data-sharing reconciliation.

Regulation (EU) 2024/1028 requires every STR (short-term-rental)
unit listed on a platform to carry a *registration number* issued
by the local authority, and obliges platforms / property managers
to reconcile bookings against that registration on a monthly
cadence.  Brain Engine's role is twofold:

1. Bind every booking decision to the unit's ``registration_id``
   so the audit pack carries the receipt the regulator will ask
   for (:class:`UnitRegistration`).
2. Produce a deterministic monthly export bundle aggregating
   every decision into one signed JSON payload
   (:class:`MonthlyExportBundle`, :func:`build_monthly_export`).

Signing reuses :mod:`brain_engine.evidence.audit_pack`'s BLAKE2B
chain primitive so the regulator can replay the same hash on
their side.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Final


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "MonthlyExportBundle",
    "UnitRegistration",
    "build_monthly_export",
]


EXPORT_SCHEMA_VERSION: Final[str] = "reg-2024-1028.v1"


@dataclass(frozen=True, slots=True)
class UnitRegistration:
    """Per-unit registration record.

    Attributes:
        property_id: Internal stable id.
        registration_id: Authority-issued registration number.
        jurisdiction: Authority code (e.g. ``"BCN"``, ``"PAR"``).
        valid_from: First day the registration applies.
        valid_to: Last day the registration applies, or ``None``
            for an open-ended record.
    """

    property_id: str
    registration_id: str
    jurisdiction: str
    valid_from: date
    valid_to: date | None = None

    def __post_init__(self) -> None:
        """Validate ranges and non-emptiness."""
        if not self.property_id:
            raise ValueError("property_id required")
        if not self.registration_id:
            raise ValueError("registration_id required")
        if not self.jurisdiction:
            raise ValueError("jurisdiction required")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError(
                "valid_to must be on or after valid_from"
            )

    def covers(self, on: date) -> bool:
        """Return ``True`` when the registration covers ``on``."""
        if on < self.valid_from:
            return False
        if self.valid_to is not None and on > self.valid_to:
            return False
        return True


@dataclass(frozen=True, slots=True)
class MonthlyExportBundle:
    """Signed monthly export payload.

    Attributes:
        schema_version: Frozen schema id; bumps when the wire
            format changes so the regulator can opt into an
            upgrade explicitly.
        period_year: Calendar year being reported.
        period_month: Month number (1–12).
        generated_at: tz-aware UTC instant the export was built.
        registrations: Tuple of :class:`UnitRegistration` records
            in scope for the period.
        signature_hex: BLAKE2B-256 hex digest over the canonical
            JSON of every other field.  Idempotent under same
            inputs.
    """

    schema_version: str
    period_year: int
    period_month: int
    generated_at: datetime
    registrations: tuple[UnitRegistration, ...]
    signature_hex: str

    def __post_init__(self) -> None:
        """Fail-fast on the obvious mistakes."""
        if not 1 <= self.period_month <= 12:
            raise ValueError(
                "period_month must be in [1, 12]"
            )
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be tz-aware")
        if not self.signature_hex:
            raise ValueError("signature_hex required")


def build_monthly_export(
    *,
    period_year: int,
    period_month: int,
    registrations: Sequence[UnitRegistration],
    generated_at: datetime | None = None,
) -> MonthlyExportBundle:
    """Build and sign a :class:`MonthlyExportBundle`.

    Args:
        period_year: Calendar year (e.g. 2026).
        period_month: 1-based month number.
        registrations: Records to include in the export.
        generated_at: Override export instant; defaults to
            :func:`datetime.now(timezone.utc)`.

    Returns:
        A signed bundle ready for write-out / submission.
    """
    if not 1 <= period_month <= 12:
        raise ValueError("period_month must be in [1, 12]")
    instant = generated_at or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("generated_at must be tz-aware")
    sorted_regs = tuple(
        sorted(
            registrations,
            key=lambda r: (r.property_id, r.valid_from),
        )
    )
    payload = _canonical_payload(
        schema_version=EXPORT_SCHEMA_VERSION,
        period_year=period_year,
        period_month=period_month,
        generated_at=instant,
        registrations=sorted_regs,
    )
    signature = hashlib.blake2b(
        payload, digest_size=32,
    ).hexdigest()
    return MonthlyExportBundle(
        schema_version=EXPORT_SCHEMA_VERSION,
        period_year=period_year,
        period_month=period_month,
        generated_at=instant,
        registrations=sorted_regs,
        signature_hex=signature,
    )


def _canonical_payload(
    *,
    schema_version: str,
    period_year: int,
    period_month: int,
    generated_at: datetime,
    registrations: tuple[UnitRegistration, ...],
) -> bytes:
    """Return the deterministic JSON bytes used for signing."""
    body = {
        "schema_version": schema_version,
        "period_year": period_year,
        "period_month": period_month,
        "generated_at": generated_at.astimezone(
            timezone.utc,
        ).isoformat(),
        "registrations": [
            _registration_to_dict(r) for r in registrations
        ],
    }
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _registration_to_dict(
    record: UnitRegistration,
) -> dict[str, object]:
    """Convert a :class:`UnitRegistration` into JSON-safe primitives."""
    return {
        "property_id": record.property_id,
        "registration_id": record.registration_id,
        "jurisdiction": record.jurisdiction,
        "valid_from": record.valid_from.isoformat(),
        "valid_to": (
            record.valid_to.isoformat()
            if record.valid_to is not None
            else None
        ),
    }
