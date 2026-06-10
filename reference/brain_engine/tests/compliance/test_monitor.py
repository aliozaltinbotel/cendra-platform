"""Behaviour of :class:`ComplianceMonitor` aggregation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.compliance.monitor import (
    ComplianceCheck,
    ComplianceContext,
    ComplianceMonitor,
    ComplianceSeverity,
    ComplianceViolation,
    VerdictKind,
)


def _ctx() -> ComplianceContext:
    return ComplianceContext(
        property_id="p",
        owner_id="o",
        action_kind=CardActionKind.SEND_MESSAGE,
    )


def _passing_check(_: ComplianceContext) -> None:
    return None


def _block_check(_: ComplianceContext) -> ComplianceViolation:
    return ComplianceViolation(
        rule_id="test.block",
        severity=ComplianceSeverity.BLOCK,
        reason="hard fail",
    )


def _review_check(_: ComplianceContext) -> ComplianceViolation:
    return ComplianceViolation(
        rule_id="test.review",
        severity=ComplianceSeverity.REVIEW,
        reason="needs review",
    )


def _warn_check(_: ComplianceContext) -> ComplianceViolation:
    return ComplianceViolation(
        rule_id="test.warn",
        severity=ComplianceSeverity.WARN,
        reason="info only",
    )


def test_no_violations_passes() -> None:
    """All checks pass → :attr:`VerdictKind.PASS`."""
    monitor = ComplianceMonitor(checks=(_passing_check,))
    verdict = monitor.evaluate(_ctx())
    assert verdict.kind is VerdictKind.PASS
    assert verdict.violations == ()


def test_block_dominates_over_review() -> None:
    """Any BLOCK row → BLOCKED regardless of REVIEW rows."""
    monitor = ComplianceMonitor(
        checks=(_review_check, _block_check),
    )
    verdict = monitor.evaluate(_ctx())
    assert verdict.kind is VerdictKind.BLOCKED
    assert "test.block" in verdict.rationale


def test_review_without_block_yields_needs_review() -> None:
    """REVIEW row without BLOCK → NEEDS_REVIEW."""
    monitor = ComplianceMonitor(checks=(_review_check,))
    verdict = monitor.evaluate(_ctx())
    assert verdict.kind is VerdictKind.NEEDS_REVIEW


def test_warn_only_passes() -> None:
    """WARN-only rows still produce PASS but record violations."""
    monitor = ComplianceMonitor(checks=(_warn_check,))
    verdict = monitor.evaluate(_ctx())
    assert verdict.kind is VerdictKind.PASS
    assert "warning" in verdict.rationale


def test_violations_preserve_check_order() -> None:
    """Violation tuple matches the order checks ran."""
    monitor = ComplianceMonitor(
        checks=(_warn_check, _review_check, _block_check),
    )
    verdict = monitor.evaluate(_ctx())
    rule_ids = [v.rule_id for v in verdict.violations]
    assert rule_ids == ["test.warn", "test.review", "test.block"]


def test_evaluated_at_is_tz_aware() -> None:
    """Default evaluation instant is tz-aware UTC."""
    monitor = ComplianceMonitor(checks=(_passing_check,))
    verdict = monitor.evaluate(_ctx())
    assert verdict.evaluated_at.tzinfo is not None


def test_explicit_at_used_when_provided() -> None:
    """Caller-supplied moment overrides the default clock."""
    monitor = ComplianceMonitor(checks=(_passing_check,))
    moment = datetime(2026, 5, 10, tzinfo=timezone.utc)
    verdict = monitor.evaluate(_ctx(), at=moment)
    assert verdict.evaluated_at == moment


def test_empty_checks_list_rejected() -> None:
    """Constructor refuses an empty check list."""
    with pytest.raises(ValueError, match="at least one"):
        ComplianceMonitor(checks=())
