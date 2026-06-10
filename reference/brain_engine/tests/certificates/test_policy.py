"""Default :class:`TierPolicy` mappings."""

from __future__ import annotations

import pytest

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.certificates.policy import (
    DEFAULT_TIER_POLICY,
    TierPolicy,
)
from brain_engine.certificates.tier import AutonomyTier, tier_rank


def test_default_policy_covers_every_action_kind() -> None:
    """Every canonical action kind has a configured ceiling."""
    known = set(DEFAULT_TIER_POLICY.known_actions())
    assert known == set(CardActionKind)


def test_financial_actions_cap_at_approver() -> None:
    """Financial / security actions are pinned to APPROVER."""
    for kind in (
        CardActionKind.CHARGE_FEE,
        CardActionKind.ISSUE_REFUND,
        CardActionKind.RELEASE_CODE,
        CardActionKind.ESCALATE,
    ):
        assert DEFAULT_TIER_POLICY.ceiling_for(kind) is (
            AutonomyTier.APPROVER
        )


def test_log_decision_allows_full_autonomy() -> None:
    """Audit-only actions allow OPERATOR (no oversight needed)."""
    assert DEFAULT_TIER_POLICY.ceiling_for(
        CardActionKind.LOG_DECISION,
    ) is AutonomyTier.OPERATOR


def test_override_replaces_default_ceiling() -> None:
    """``override`` replaces the configured ceiling."""
    policy = TierPolicy()
    policy.override(
        CardActionKind.SEND_MESSAGE,
        AutonomyTier.APPROVER,
    )
    assert policy.ceiling_for(CardActionKind.SEND_MESSAGE) is (
        AutonomyTier.APPROVER
    )


def test_unknown_action_raises_keyerror() -> None:
    """Empty policy fails loudly when asked about unknown kinds."""
    policy = TierPolicy(defaults={})
    with pytest.raises(KeyError, match="no tier ceiling"):
        policy.ceiling_for(CardActionKind.SEND_MESSAGE)


def test_no_default_above_collaborator_for_pricing() -> None:
    """Pricing/discount actions cap at CONSULTANT or below."""
    pricing_kinds = (
        CardActionKind.APPLY_DISCOUNT,
        CardActionKind.COUNTER_OFFER,
        CardActionKind.DISPATCH_VENDOR,
    )
    for kind in pricing_kinds:
        ceiling = DEFAULT_TIER_POLICY.ceiling_for(kind)
        assert tier_rank(ceiling) <= tier_rank(
            AutonomyTier.CONSULTANT
        )
