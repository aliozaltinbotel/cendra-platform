"""Behaviour of Reg 2024/1028 reconciliation primitives."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from brain_engine.compliance.reg_2024_1028 import (
    EXPORT_SCHEMA_VERSION,
    UnitRegistration,
    build_monthly_export,
)


def _reg(**overrides: object) -> UnitRegistration:
    base: dict[str, object] = {
        "property_id": "p1",
        "registration_id": "HUTB-1234",
        "jurisdiction": "BCN",
        "valid_from": date(2026, 1, 1),
        "valid_to": date(2026, 12, 31),
    }
    base.update(overrides)
    return UnitRegistration(**base)  # type: ignore[arg-type]


def test_registration_immutable() -> None:
    rec = _reg()
    with pytest.raises((AttributeError, TypeError)):
        rec.property_id = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"property_id": ""}, "property_id"),
        ({"registration_id": ""}, "registration_id"),
        ({"jurisdiction": ""}, "jurisdiction"),
        (
            {
                "valid_from": date(2026, 6, 1),
                "valid_to": date(2026, 5, 1),
            },
            "valid_to",
        ),
    ],
    ids=[
        "empty_property",
        "empty_reg",
        "empty_juris",
        "valid_to_before_from",
    ],
)
def test_validation_failures(
    override: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _reg(**override)


def test_open_ended_registration_covers_future() -> None:
    """``valid_to=None`` means the registration never expires."""
    rec = _reg(valid_to=None)
    assert rec.covers(date(2099, 1, 1))


def test_covers_outside_range_false() -> None:
    """Dates outside the range are not covered."""
    rec = _reg()
    assert not rec.covers(date(2025, 12, 31))
    assert not rec.covers(date(2027, 1, 1))


def test_build_monthly_export_signs_payload() -> None:
    """Export signature is 64-hex BLAKE2B."""
    bundle = build_monthly_export(
        period_year=2026,
        period_month=5,
        registrations=[_reg()],
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    assert bundle.schema_version == EXPORT_SCHEMA_VERSION
    assert bundle.period_month == 5
    assert len(bundle.signature_hex) == 64
    int(bundle.signature_hex, 16)


def test_export_signature_is_deterministic() -> None:
    """Same inputs reproduce the same signature."""
    fixed_instant = datetime(2026, 5, 31, tzinfo=timezone.utc)
    a = build_monthly_export(
        period_year=2026,
        period_month=5,
        registrations=[_reg(property_id="p1"), _reg(property_id="p2")],
        generated_at=fixed_instant,
    )
    b = build_monthly_export(
        period_year=2026,
        period_month=5,
        registrations=[_reg(property_id="p2"), _reg(property_id="p1")],
        generated_at=fixed_instant,
    )
    assert a.signature_hex == b.signature_hex


def test_invalid_period_month_rejected() -> None:
    """Out-of-range month is rejected at build time."""
    with pytest.raises(ValueError, match="period_month"):
        build_monthly_export(
            period_year=2026,
            period_month=13,
            registrations=[],
            generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        )


def test_naive_generated_at_rejected() -> None:
    """tz-naive ``generated_at`` is rejected."""
    with pytest.raises(ValueError, match="generated_at"):
        build_monthly_export(
            period_year=2026,
            period_month=5,
            registrations=[],
            generated_at=datetime(2026, 5, 31),
        )
