""":class:`TierPolicy` behaviour with pack-supplied mappings.

Adapted at port time: the reference tested a kernel-resident
``DEFAULT_TIER_POLICY`` keyed by ``CardActionKind``; the genericised
kernel ships no vocabulary, so these tests exercise the same
semantics through an explicit pack-style mapping (mirroring
``packs/hospitality/tier_defaults.yaml``).
"""

from __future__ import annotations

import pytest

from core.brain.certificates.policy import TierPolicy
from core.brain.certificates.tier import AutonomyTier, tier_rank

_PACK_CEILINGS: dict[str, AutonomyTier] = {
    # Communication / bookkeeping — low risk, high autonomy ok
    "send_message": AutonomyTier.COLLABORATOR,
    "request_document": AutonomyTier.COLLABORATOR,
    "mark_resolved": AutonomyTier.OPERATOR,
    "log_decision": AutonomyTier.OPERATOR,
    "handoff_to_teammate": AutonomyTier.OPERATOR,
    # Booking lifecycle — material consequences
    "hold_for_review": AutonomyTier.COLLABORATOR,
    "block_date": AutonomyTier.COLLABORATOR,
    "confirm_booking": AutonomyTier.APPROVER,
    "cancel_booking": AutonomyTier.APPROVER,
    # Pricing / negotiation
    "apply_discount": AutonomyTier.CONSULTANT,
    "counter_offer": AutonomyTier.CONSULTANT,
    # Operations / dispatch
    "dispatch_vendor": AutonomyTier.CONSULTANT,
    # Financial / security-sensitive — must be approved
    "charge_fee": AutonomyTier.APPROVER,
    "issue_refund": AutonomyTier.APPROVER,
    "release_code": AutonomyTier.APPROVER,
    "escalate": AutonomyTier.APPROVER,
}


@pytest.fixture
def pack_policy() -> TierPolicy:
    return TierPolicy(_PACK_CEILINGS)


def test_policy_covers_every_pack_action_kind(pack_policy: TierPolicy) -> None:
    """Every pack action kind has a configured ceiling."""
    assert set(pack_policy.known_actions()) == set(_PACK_CEILINGS)


def test_financial_actions_cap_at_approver(pack_policy: TierPolicy) -> None:
    """Financial / security actions are pinned to APPROVER."""
    for kind in ("charge_fee", "issue_refund", "release_code", "escalate"):
        assert pack_policy.ceiling_for(kind) is AutonomyTier.APPROVER


def test_log_decision_allows_full_autonomy(pack_policy: TierPolicy) -> None:
    """Audit-only actions allow OPERATOR (no oversight needed)."""
    assert pack_policy.ceiling_for("log_decision") is AutonomyTier.OPERATOR


def test_override_replaces_configured_ceiling(pack_policy: TierPolicy) -> None:
    """``override`` replaces the configured ceiling."""
    pack_policy.override("send_message", AutonomyTier.APPROVER)
    assert pack_policy.ceiling_for("send_message") is AutonomyTier.APPROVER


def test_kernel_policy_has_no_builtin_vocabulary() -> None:
    """The kernel default is an empty mapping — no vertical content."""
    assert TierPolicy().known_actions() == ()


def test_unknown_action_raises_keyerror() -> None:
    """Empty policy fails loudly when asked about unknown kinds."""
    policy = TierPolicy(defaults={})
    with pytest.raises(KeyError, match="no tier ceiling"):
        policy.ceiling_for("send_message")


def test_no_pack_default_above_consultant_for_pricing(pack_policy: TierPolicy) -> None:
    """Pricing/discount actions cap at CONSULTANT or below."""
    for kind in ("apply_discount", "counter_offer", "dispatch_vendor"):
        assert tier_rank(pack_policy.ceiling_for(kind)) <= tier_rank(AutonomyTier.CONSULTANT)
