"""Behaviour of built-in compliance checks."""

from __future__ import annotations

from datetime import date

import pytest

from core.brain.compliance.checks import (
    DEFAULT_BUILTIN_CHECKS,
    REG_2024_1028_BOOKING_KINDS,
    gdpr_art22_consent,
    hitl_required_for_high_risk,
    jurisdiction_min_nights,
    never_ai_action,
    registration_id_required,
)
from core.brain.compliance.monitor import (
    ComplianceContext,
    ComplianceSeverity,
)


def _ctx(**overrides: object) -> ComplianceContext:
    base: dict[str, object] = {
        "property_id": "p",
        "owner_id": "o",
        "action_kind": "send_message",
    }
    base.update(overrides)
    return ComplianceContext(**base)  # type: ignore[arg-type]


def test_default_builtin_checks_count() -> None:
    """Five checks ship by default."""
    assert len(DEFAULT_BUILTIN_CHECKS) == 5


@pytest.mark.parametrize(
    "kind",
    list(REG_2024_1028_BOOKING_KINDS),
    ids=lambda k: str(k),
)
def test_registration_id_blocks_booking_action_without_reg(
    kind: str,
) -> None:
    """Booking actions need a registration_id."""
    violation = registration_id_required(_ctx(action_kind=kind))
    assert violation is not None
    assert violation.severity is ComplianceSeverity.BLOCK
    assert "reg_2024_1028" in violation.rule_id


def test_registration_id_passes_when_present() -> None:
    """Passing registration_id satisfies the check."""
    assert (
        registration_id_required(
            _ctx(
                action_kind="confirm_booking",
                registration_id="HUTB-1234",
            )
        )
        is None
    )


def test_registration_id_skips_non_booking() -> None:
    """Non-booking actions do not trigger the check."""
    assert registration_id_required(_ctx(action_kind="send_message")) is None


def test_hitl_required_review_severity() -> None:
    """High-risk actions without consent return REVIEW."""
    violation = hitl_required_for_high_risk(_ctx(action_kind="issue_refund"))
    assert violation is not None
    assert violation.severity is ComplianceSeverity.REVIEW


def test_hitl_passes_with_consent() -> None:
    """Recorded consent satisfies the HITL gate."""
    assert (
        hitl_required_for_high_risk(
            _ctx(
                action_kind="issue_refund",
                has_human_consent=True,
            )
        )
        is None
    )


def test_gdpr_art22_blocks_natural_person_decision() -> None:
    """Adverse decisions on natural persons need consent."""
    violation = gdpr_art22_consent(_ctx(is_natural_person_decision=True))
    assert violation is not None
    assert violation.severity is ComplianceSeverity.BLOCK


def test_gdpr_art22_passes_with_consent() -> None:
    """Recorded consent satisfies GDPR Art. 22."""
    assert (
        gdpr_art22_consent(
            _ctx(
                is_natural_person_decision=True,
                has_human_consent=True,
            )
        )
        is None
    )


def test_jurisdiction_min_nights_block_bcn_short() -> None:
    """BCN 2-night booking is blocked."""
    violation = jurisdiction_min_nights(
        _ctx(
            action_kind="confirm_booking",
            jurisdiction="BCN",
            booking_dates=(date(2026, 6, 1), date(2026, 6, 2)),
        )
    )
    assert violation is not None
    assert "31 nights" in violation.reason


def test_jurisdiction_min_nights_pass_when_unregulated() -> None:
    """City without min-night cap returns ``None``."""
    assert (
        jurisdiction_min_nights(
            _ctx(
                action_kind="confirm_booking",
                jurisdiction="LON",
                booking_dates=(date(2026, 6, 1),),
            )
        )
        is None
    )


def test_never_ai_action_blocks_protected_class() -> None:
    """Structurally banned category triggers BLOCK violation."""
    violation = never_ai_action(_ctx(extra={"never_ai_category": "screen_by_protected_class"}))
    assert violation is not None
    assert violation.severity is ComplianceSeverity.BLOCK


def test_never_ai_action_skips_unknown_category() -> None:
    """Unknown extra value does not trigger a violation."""
    assert never_ai_action(_ctx(extra={"never_ai_category": "x"})) is None


def test_never_ai_action_skips_when_no_category_extra() -> None:
    """Extra without the key skips the check."""
    assert never_ai_action(_ctx()) is None
