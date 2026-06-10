"""Approval gateway + confidence router behaviour.

Written at port time — the reference has no approval tests.  Pins the
three-tier confidence routing, the gateway's decision order (blockers →
conditional → auto-approve → preference rules → confidence → park), the
non-blocking pending/resolve/expire lifecycle, and the pack policy
injection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.brain.autonomy.approval import (
    ApprovalGateway,
    ApprovalNotFoundError,
    ApprovalPolicy,
    ApprovalStatus,
    ConfidenceRouter,
    ConfidenceTier,
)
from core.brain.patterns.blockers import BlockerEngine, BlockerSeverity, InMemoryBlockerStore

# Pack-equivalent policy (mirrors packs/hospitality/approval.yaml)
PACK_POLICY = ApprovalPolicy(
    auto_approve_actions=frozenset({"send_welcome_message"}),
    conditional_approve_actions=frozenset({"send_access_code"}),
    always_require_approval=frozenset({"submit_damage_claim", "charge_guest"}),
)
PACK_NEVER_AUTO = frozenset({"submit_damage_claim", "charge_guest"})


class _Notifier:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    def send_approval_request(self, *, owner_id: str, message: str, request_id: str) -> None:
        self.sent.append({"owner_id": owner_id, "message": message, "request_id": request_id})


class TestConfidenceRouter:
    def test_tier_boundaries(self):
        router = ConfidenceRouter()
        assert router.classify_tier(0.85) is ConfidenceTier.HIGH
        assert router.classify_tier(0.84) is ConfidenceTier.MEDIUM
        assert router.classify_tier(0.50) is ConfidenceTier.MEDIUM
        assert router.classify_tier(0.49) is ConfidenceTier.LOW
        # out-of-range values clamp
        assert router.classify_tier(1.7) is ConfidenceTier.HIGH
        assert router.classify_tier(-2.0) is ConfidenceTier.LOW

    def test_high_auto_approves_with_evidence(self):
        decision = ConfidenceRouter().route(0.9, "late_checkout", reasoning="VIP guest")
        assert decision.auto_approve is True
        assert decision.escalate is False
        assert decision.evidence_pack.tier is ConfidenceTier.HIGH
        assert "VIP guest" in decision.evidence_pack.summary

    def test_never_auto_downgrades_high_to_medium(self):
        router = ConfidenceRouter(never_auto_approve=PACK_NEVER_AUTO)
        decision = router.route(0.95, "charge_guest")
        assert decision.tier is ConfidenceTier.MEDIUM
        assert decision.auto_approve is False

    def test_kernel_default_never_auto_is_empty(self):
        decision = ConfidenceRouter().route(0.95, "charge_guest")
        assert decision.auto_approve is True

    def test_low_escalates_with_urgency_boost(self):
        decision = ConfidenceRouter().route(0.2, "late_checkout")
        assert decision.escalate is True
        assert decision.urgency_boost == 1

    def test_invalid_thresholds_rejected(self):
        with pytest.raises(ValueError, match="medium_threshold"):
            ConfidenceRouter(high_threshold=0.5, medium_threshold=0.5)


class TestGatewayDecisionOrder:
    def test_auto_approve_set_short_circuits(self):
        gateway = ApprovalGateway(policy=PACK_POLICY)
        response = gateway.request_approval(
            action_type="send_welcome_message",
            owner_id="o1",
            property_id="p1",
            description="welcome",
        )
        assert response.status is ApprovalStatus.AUTO_APPROVED
        assert gateway.pending_requests == []

    def test_hard_blocker_denies_outright(self):
        blockers = BlockerEngine(InMemoryBlockerStore())
        blockers.create_blocker(
            blocker_type="payment_incomplete",
            property_id="p1",
            reservation_id="r1",
            description="unpaid balance",
            severity=BlockerSeverity.HARD,
            blocks_actions=("send_access_code",),
        )
        gateway = ApprovalGateway(policy=PACK_POLICY, blocker_engine=blockers)
        response = gateway.request_approval(
            action_type="send_access_code",
            owner_id="o1",
            property_id="p1",
            description="release code",
            context={"reservation_id": "r1"},
            confidence=0.99,
        )
        assert response.status is ApprovalStatus.DENIED
        assert "unpaid balance" in response.message

    def test_conditional_auto_approves_when_blocker_free(self):
        gateway = ApprovalGateway(
            policy=PACK_POLICY,
            blocker_engine=BlockerEngine(InMemoryBlockerStore()),
        )
        response = gateway.request_approval(
            action_type="send_access_code",
            owner_id="o1",
            property_id="p1",
            description="release code",
        )
        assert response.status is ApprovalStatus.AUTO_APPROVED

    def test_preference_rule_auto_approves(self):
        class _Prefs:
            def find_rule(self, **kwargs):
                return {"auto_approve": True, "rule_id": "rule-9"}

            def save_rule(self, **kwargs):
                raise AssertionError("not expected")

        gateway = ApprovalGateway(policy=PACK_POLICY, preference_store=_Prefs())
        response = gateway.request_approval(
            action_type="late_checkout",
            owner_id="o1",
            property_id="p1",
            description="3pm checkout",
        )
        assert response.status is ApprovalStatus.AUTO_APPROVED
        assert "rule-9" in response.message

    def test_high_confidence_auto_approves(self):
        gateway = ApprovalGateway(policy=PACK_POLICY)
        response = gateway.request_approval(
            action_type="late_checkout",
            owner_id="o1",
            property_id="p1",
            description="3pm checkout",
            confidence=0.9,
        )
        assert response.status is ApprovalStatus.AUTO_APPROVED

    def test_medium_confidence_parks_with_evidence(self):
        notifier = _Notifier()
        gateway = ApprovalGateway(policy=PACK_POLICY, notifier=notifier)
        response = gateway.request_approval(
            action_type="late_checkout",
            owner_id="o1",
            property_id="p1",
            description="3pm checkout",
            confidence=0.6,
            reasoning="returning guest",
        )
        assert response.status is ApprovalStatus.PENDING
        (request,) = gateway.pending_requests
        assert request.confidence_tier == "medium"
        assert "returning guest" in request.evidence_summary
        assert len(notifier.sent) == 1
        assert "AI Evidence" in notifier.sent[0]["message"]

    def test_low_confidence_escalates_urgency_and_parks(self):
        gateway = ApprovalGateway(policy=PACK_POLICY)
        response = gateway.request_approval(
            action_type="late_checkout",
            owner_id="o1",
            property_id="p1",
            description="3pm checkout",
            urgency=3,
            confidence=0.2,
        )
        assert response.status is ApprovalStatus.PENDING
        (request,) = gateway.pending_requests
        assert request.urgency == 4
        assert request.timeout_seconds == 300

    def test_notifier_failure_does_not_break_routing(self):
        class _Broken:
            def send_approval_request(self, **kwargs):
                raise RuntimeError("channel down")

        gateway = ApprovalGateway(policy=PACK_POLICY, notifier=_Broken())
        response = gateway.request_approval(
            action_type="late_checkout",
            owner_id="o1",
            property_id="p1",
            description="x",
        )
        assert response.status is ApprovalStatus.PENDING


class TestGatewayLifecycle:
    def test_submit_response_resolves_and_saves_rule(self):
        saved: list[dict[str, Any]] = []

        class _Prefs:
            def find_rule(self, **kwargs):
                return None

            def save_rule(self, **kwargs):
                saved.append(kwargs)

        gateway = ApprovalGateway(policy=PACK_POLICY, preference_store=_Prefs())
        pending = gateway.request_approval(
            action_type="late_checkout",
            owner_id="o1",
            property_id="p1",
            description="x",
        )
        response = gateway.submit_response(
            pending.request_id,
            approved=True,
            owner_id="o1",
            apply_rule=True,
            rule_scope="always",
        )
        assert response.status is ApprovalStatus.APPROVED
        assert gateway.get_request(pending.request_id).status is ApprovalStatus.APPROVED
        assert saved[0]["action_type"] == "late_checkout"
        assert saved[0]["auto_approve"] is True
        assert gateway.pending_requests == []

    def test_submit_response_unknown_request_raises(self):
        gateway = ApprovalGateway()
        with pytest.raises(ApprovalNotFoundError):
            gateway.submit_response("APR-NOPE", approved=True)

    def test_expire_overdue_applies_fallback(self):
        gateway = ApprovalGateway(policy=PACK_POLICY)
        pending = gateway.request_approval(
            action_type="late_checkout",
            owner_id="o1",
            property_id="p1",
            description="x",
            urgency=5,  # 120s timeout
        )
        assert gateway.expire_overdue(now=datetime.now(UTC)) == []
        expired = gateway.expire_overdue(now=datetime.now(UTC) + timedelta(seconds=121))
        assert [r.request_id for r in expired] == [pending.request_id]
        assert expired[0].status is ApprovalStatus.TIMEOUT
        assert "notify_manager" in expired[0].message
        assert gateway.pending_requests == []
        # idempotent: nothing left to expire
        assert gateway.expire_overdue(now=datetime.now(UTC) + timedelta(days=1)) == []
