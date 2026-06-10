"""Approval gateway + confidence-based routing.

Port of the reference's ``approval/{models,confidence_router,gateway}.py``
@a761e29 (``mira`` / ``notifier`` / ``knowledge_sync`` are not in the
Batch 2 map row and stay in the reference).

Three-tier confidence routing (Phase 2 of the reference):

  HIGH   (>= 0.85) → auto-approve
  MEDIUM (0.50-0.85) → notify the PM with an :class:`EvidencePack`
  LOW    (< 0.50) → escalate with urgency+1

Genericised per golden rule 4: the reference's ``ActionType`` enum
(late_checkout, call_cleaner, send_access_code, …) is hospitality
vocabulary — action kinds are opaque ``str`` here, and the three
routing policy sets (``AUTO_APPROVE_ACTIONS``,
``CONDITIONAL_APPROVE_ACTIONS``, ``ALWAYS_REQUIRE_APPROVAL``) plus the
router's never-auto set are injected (pack data:
``packs/hospitality/approval.yaml``).  ``URGENCY_TIMEOUTS`` is
mechanism and stays.

Async-to-sync note: the reference's ``request_approval`` parked a
coroutine on an ``asyncio.Event`` until the owner answered or the
timeout fired.  Blocking a worker thread for minutes is not portable to
Dify's sync runtime — and PORTING_MAP maps these verdicts onto Dify's
Human Input node (wiring lands in Batch 4).  The port therefore returns
immediately: :meth:`ApprovalGateway.request_approval` yields either a
decided :class:`ApprovalResponse` (auto-approved / blocker-denied) or a
``PENDING`` one with the request parked in the gateway;
:meth:`submit_response` resolves it, and :meth:`expire_overdue` sweeps
timed-out requests onto their fallback (Celery beat, Batch 5).  The
decision *order* is unchanged: hard blockers → conditional approve →
auto-approve set → preference rules → confidence routing → mandatory
approval.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from core.brain.patterns.blockers import BlockerEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models (from approval/models.py — ActionType genericised to str)
# ---------------------------------------------------------------------------


class ApprovalStatus(StrEnum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    AUTO_APPROVED = "auto_approved"


class ApprovalRequest(BaseModel):
    """A request for owner approval before executing an AI-proposed action."""

    request_id: str = Field(default="", description="Unique request ID.")
    action_type: str = Field(description="Action kind to approve (vertical-defined string).")
    owner_id: str = Field(default="", description="Owner identifier.")
    property_id: str = Field(default="", description="Property identifier.")
    description: str = Field(description="Human-readable action description.")
    proposed_action: dict[str, Any] = Field(default_factory=dict, description="Structured action details.")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context for the decision.")
    urgency: int = Field(default=3, ge=1, le=5, description="Urgency level (1=low, 5=critical).")
    timeout_seconds: int = Field(default=300, description="Seconds to wait before fallback.")
    fallback_action: str = Field(default="notify_manager", description="What to do on timeout.")
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_tier: str | None = Field(default=None, description="Confidence tier: high, medium, low.")
    evidence_summary: str | None = Field(default=None, description="Summary from EvidencePack for PM review.")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    responded_at: str | None = Field(default=None)
    owner_response: str | None = Field(default=None)

    def __repr__(self) -> str:
        return f"ApprovalRequest(id={self.request_id!r}, action={self.action_type!r}, status={self.status.value!r})"


class ApprovalResponse(BaseModel):
    """Owner's response to an approval request."""

    request_id: str
    status: ApprovalStatus
    owner_id: str = ""
    message: str = ""
    apply_rule: bool = Field(default=False, description="Create a preference rule from this decision?")
    rule_scope: str = Field(
        default="this_time",
        description="Rule scope: this_time, always, this_property, all_properties.",
    )


# Default timeout per urgency level (seconds) — mechanism, not vocabulary.
URGENCY_TIMEOUTS: Final[dict[int, int]] = {
    1: 3600,
    2: 1800,
    3: 600,
    4: 300,
    5: 120,
}


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    """Vertical / tenant action-routing policy (pack data).

    The reference pinned these as kernel frozensets of ``ActionType``;
    cendra-platform injects them (see packs/hospitality/approval.yaml).

    Attributes:
        auto_approve_actions: Actions that never need approval.
        conditional_approve_actions: Actions auto-approved ONLY when no
            hard blocker exists (the BlockerEngine is consulted first).
        always_require_approval: Actions that always need approval.
    """

    auto_approve_actions: frozenset[str] = frozenset()
    conditional_approve_actions: frozenset[str] = frozenset()
    always_require_approval: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Confidence router (from approval/confidence_router.py)
# ---------------------------------------------------------------------------


_HIGH_THRESHOLD: Final[float] = 0.85
_MEDIUM_THRESHOLD: Final[float] = 0.50


class ConfidenceTier(StrEnum):
    """Confidence level of a decision, determining its approval route."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Evidence bundle for the PM on MEDIUM/LOW confidence decisions."""

    reasoning: str
    confidence: float
    tier: ConfidenceTier
    kb_entries: tuple[str, ...] = ()
    past_decisions: tuple[str, ...] = ()
    action_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """Short summary rendered into the PM notification."""
        tier_labels: dict[ConfidenceTier, str] = {
            ConfidenceTier.HIGH: "AUTO-APPROVED",
            ConfidenceTier.MEDIUM: "NEEDS REVIEW",
            ConfidenceTier.LOW: "ESCALATED",
        }
        label = tier_labels.get(self.tier, "UNKNOWN")
        return (
            f"[{label}] confidence={self.confidence:.0%} | "
            f"KB entries: {len(self.kb_entries)} | past decisions: {len(self.past_decisions)}\n"
            f"Reasoning: {self.reasoning}"
        )


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Result of routing one decision through :class:`ConfidenceRouter`."""

    tier: ConfidenceTier
    auto_approve: bool
    escalate: bool
    urgency_boost: int = 0
    evidence_pack: EvidencePack | None = None


class ConfidenceRouter:
    """Route decisions by confidence: auto-approve, PM review, or escalate.

    Thresholds are configurable.  ``never_auto_approve`` action kinds can
    never be auto-approved regardless of confidence (pack / tenant data;
    the reference pinned damage-claim and charge actions here — kernel
    default is empty).
    """

    def __init__(
        self,
        high_threshold: float = _HIGH_THRESHOLD,
        medium_threshold: float = _MEDIUM_THRESHOLD,
        never_auto_approve: frozenset[str] | None = None,
    ) -> None:
        if medium_threshold >= high_threshold:
            raise ValueError(f"medium_threshold ({medium_threshold}) must be < high_threshold ({high_threshold})")
        self._high: Final[float] = high_threshold
        self._medium: Final[float] = medium_threshold
        self._never_auto: Final[frozenset[str]] = never_auto_approve or frozenset()

    def route(
        self,
        confidence: float,
        action_type: str,
        reasoning: str = "",
        kb_entries: Sequence[str] = (),
        past_decisions: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        """Return the approval route for a decision at ``confidence``."""
        confidence = max(0.0, min(1.0, confidence))
        tier = self._compute_tier(confidence)
        evidence_pack = EvidencePack(
            reasoning=reasoning,
            confidence=confidence,
            tier=tier,
            kb_entries=tuple(kb_entries),
            past_decisions=tuple(past_decisions),
            action_type=action_type,
            metadata=metadata or {},
        )

        if tier == ConfidenceTier.HIGH:
            if action_type in self._never_auto:
                logger.info(
                    "Confidence HIGH (%.2f) but %s requires manual approval, downgrading to MEDIUM",
                    confidence,
                    action_type,
                )
                return RoutingDecision(
                    tier=ConfidenceTier.MEDIUM,
                    auto_approve=False,
                    escalate=False,
                    evidence_pack=evidence_pack,
                )
            logger.info("Confidence HIGH (%.2f) -> auto-approve %s", confidence, action_type)
            return RoutingDecision(
                tier=ConfidenceTier.HIGH,
                auto_approve=True,
                escalate=False,
                evidence_pack=evidence_pack,
            )

        if tier == ConfidenceTier.MEDIUM:
            logger.info("Confidence MEDIUM (%.2f) -> notify PM for %s", confidence, action_type)
            return RoutingDecision(
                tier=ConfidenceTier.MEDIUM,
                auto_approve=False,
                escalate=False,
                evidence_pack=evidence_pack,
            )

        logger.warning("Confidence LOW (%.2f) -> escalate %s with urgency+1", confidence, action_type)
        return RoutingDecision(
            tier=ConfidenceTier.LOW,
            auto_approve=False,
            escalate=True,
            urgency_boost=1,
            evidence_pack=evidence_pack,
        )

    def classify_tier(self, confidence: float) -> ConfidenceTier:
        """Classify a confidence value without building a full decision."""
        return self._compute_tier(max(0.0, min(1.0, confidence)))

    def _compute_tier(self, confidence: float) -> ConfidenceTier:
        if confidence >= self._high:
            return ConfidenceTier.HIGH
        if confidence >= self._medium:
            return ConfidenceTier.MEDIUM
        return ConfidenceTier.LOW

    def __repr__(self) -> str:
        return f"ConfidenceRouter(high={self._high}, medium={self._medium}, never_auto={len(self._never_auto)} actions)"


# ---------------------------------------------------------------------------
# Gateway seams
# ---------------------------------------------------------------------------


class ApprovalNotFoundError(KeyError):
    """Raised when a response references an unknown approval request."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"approval request {request_id!r} not found")
        self.request_id = request_id


@runtime_checkable
class Notifier(Protocol):
    """Notification backend (console / channel plugin / Human Input)."""

    def send_approval_request(self, *, owner_id: str, message: str, request_id: str) -> None:
        """Deliver an approval request to the owner."""
        ...


@runtime_checkable
class PreferenceStore(Protocol):
    """Owner preference-rule lookup consulted before routing."""

    def find_rule(
        self,
        *,
        owner_id: str,
        property_id: str,
        action_type: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a matching rule dict (``{"auto_approve": bool, ...}``) or None."""
        ...

    def save_rule(
        self,
        *,
        owner_id: str,
        property_id: str,
        action_type: str,
        auto_approve: bool,
        scope: str,
        context: dict[str, Any],
    ) -> None:
        """Persist a preference rule derived from an owner response."""
        ...


# ---------------------------------------------------------------------------
# Gateway (from approval/gateway.py — non-blocking rewrite)
# ---------------------------------------------------------------------------


class ApprovalGateway:
    """Central hub routing AI-proposed actions through owner approval.

    Decision order (reference parity): hard blockers deny outright;
    conditional actions auto-approve when blocker-free; the policy's
    auto-approve set short-circuits; owner preference rules apply next;
    confidence routing decides the rest; mandatory-approval actions are
    logged and always parked for the owner.

    Unlike the reference, pending requests do not block a thread —
    they park in the gateway until :meth:`submit_response` or
    :meth:`expire_overdue` resolves them (Dify Human Input wiring lands
    in Batch 4; the timeout sweep becomes a beat job in Batch 5).
    """

    def __init__(
        self,
        notifier: Notifier | None = None,
        preference_store: PreferenceStore | None = None,
        confidence_router: ConfidenceRouter | None = None,
        blocker_engine: BlockerEngine | None = None,
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self._notifier = notifier
        self._preference_store = preference_store
        self._confidence_router = confidence_router or ConfidenceRouter()
        self._blocker_engine = blocker_engine
        self._policy = policy or ApprovalPolicy()
        self._pending: dict[str, ApprovalRequest] = {}
        self._results: dict[str, ApprovalResponse] = {}

    @property
    def pending_requests(self) -> list[ApprovalRequest]:
        """All currently pending approval requests."""
        return [req for req in self._pending.values() if req.status == ApprovalStatus.PENDING]

    def request_approval(
        self,
        action_type: str,
        owner_id: str,
        property_id: str,
        description: str,
        proposed_action: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        urgency: int = 3,
        confidence: float | None = None,
        reasoning: str = "",
        kb_entries: Sequence[str] = (),
        past_decisions: Sequence[str] = (),
    ) -> ApprovalResponse:
        """Route a proposed action; return the verdict or a PENDING response.

        A ``PENDING`` response means the request is parked in the
        gateway awaiting :meth:`submit_response` (or the timeout sweep);
        the owner has been notified through the configured notifier.
        """
        request_id = f"APR-{uuid.uuid4().hex[:8].upper()}"
        timeout = URGENCY_TIMEOUTS.get(urgency, 600)

        request = ApprovalRequest(
            request_id=request_id,
            action_type=action_type,
            owner_id=owner_id,
            property_id=property_id,
            description=description,
            proposed_action=proposed_action or {},
            context=context or {},
            urgency=urgency,
            timeout_seconds=timeout,
        )

        # Step 0: hard blockers override everything
        if self._blocker_engine:
            blockers = self._blocker_engine.check_blockers(
                property_id,
                (context or {}).get("reservation_id"),
                action_type,
            )
            hard_blockers = [b for b in blockers if b.is_hard]
            if hard_blockers:
                descriptions = "; ".join(b.description for b in hard_blockers)
                logger.warning(
                    "Action %s BLOCKED by %d hard blocker(s): %s (request=%s)",
                    action_type,
                    len(hard_blockers),
                    descriptions,
                    request_id,
                )
                request.status = ApprovalStatus.DENIED
                return ApprovalResponse(
                    request_id=request_id,
                    status=ApprovalStatus.DENIED,
                    owner_id=owner_id,
                    message=f"Blocked: {descriptions}",
                )
            soft_blockers = [b for b in blockers if not b.is_hard]
            if soft_blockers:
                logger.info(
                    "Action %s has %d soft blocker(s), proceeding with caution (request=%s)",
                    action_type,
                    len(soft_blockers),
                    request_id,
                )

        # Step 0b: conditional approve — auto only when blocker-free
        if action_type in self._policy.conditional_approve_actions:
            if self._blocker_engine:
                has_blocker = self._blocker_engine.has_hard_blocker(
                    property_id,
                    (context or {}).get("reservation_id"),
                    action_type,
                )
                if not has_blocker:
                    logger.info("Conditional auto-approving %s — no blockers (request=%s)", action_type, request_id)
                    return self._auto_approve(request)
                logger.info(
                    "Conditional action %s has blockers — routing to approval (request=%s)",
                    action_type,
                    request_id,
                )
            else:
                logger.info("Conditional auto-approving %s — no blocker engine (request=%s)", action_type, request_id)
                return self._auto_approve(request)

        # Step 1: auto-approve by action kind
        if action_type in self._policy.auto_approve_actions:
            logger.info("Auto-approving %s (request=%s)", action_type, request_id)
            return self._auto_approve(request)

        # Step 2: owner preference rules
        if self._preference_store:
            rule = self._preference_store.find_rule(
                owner_id=owner_id,
                property_id=property_id,
                action_type=action_type,
                context=context or {},
            )
            if rule and rule.get("auto_approve"):
                logger.info(
                    "Preference rule auto-approves %s (request=%s, rule=%s)",
                    action_type,
                    request_id,
                    rule.get("rule_id"),
                )
                return self._auto_approve(request, rule_id=rule.get("rule_id", ""))

        # Step 3: confidence-based routing
        if confidence is not None:
            routing = self._confidence_router.route(
                confidence=confidence,
                action_type=action_type,
                reasoning=reasoning,
                kb_entries=kb_entries,
                past_decisions=past_decisions,
                metadata=context or {},
            )
            request.confidence_score = confidence
            request.confidence_tier = routing.tier.value
            if routing.evidence_pack:
                request.evidence_summary = routing.evidence_pack.summary
            if routing.auto_approve:
                logger.info("Confidence auto-approve %s (%.2f, request=%s)", action_type, confidence, request_id)
                return self._auto_approve(request)
            if routing.escalate:
                urgency = min(5, urgency + routing.urgency_boost)
                request.urgency = urgency
                request.timeout_seconds = URGENCY_TIMEOUTS.get(urgency, 600)
                logger.warning(
                    "Confidence LOW (%.2f) -> escalating %s, urgency=%d (request=%s)",
                    confidence,
                    action_type,
                    urgency,
                    request_id,
                )

        # Step 4: mandatory approval check (informational, request parks either way)
        if action_type in self._policy.always_require_approval:
            logger.info("Action %s requires mandatory approval (request=%s)", action_type, request_id)

        self._pending[request_id] = request
        self._notify_owner(request)
        return ApprovalResponse(
            request_id=request_id,
            status=ApprovalStatus.PENDING,
            owner_id=owner_id,
            message="Awaiting owner response",
        )

    def submit_response(
        self,
        request_id: str,
        approved: bool,
        owner_id: str = "",
        message: str = "",
        apply_rule: bool = False,
        rule_scope: str = "this_time",
    ) -> ApprovalResponse:
        """Resolve a parked approval request with the owner's decision.

        Raises:
            ApprovalNotFoundError: If ``request_id`` is not pending.
        """
        if request_id not in self._pending:
            raise ApprovalNotFoundError(request_id)

        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        response = ApprovalResponse(
            request_id=request_id,
            status=status,
            owner_id=owner_id,
            message=message,
            apply_rule=apply_rule,
            rule_scope=rule_scope,
        )
        self._results[request_id] = response

        request = self._pending[request_id]
        request.status = status
        request.responded_at = datetime.now(UTC).isoformat()

        if apply_rule and self._preference_store:
            self._preference_store.save_rule(
                owner_id=request.owner_id,
                property_id=request.property_id,
                action_type=request.action_type,
                auto_approve=approved,
                scope=rule_scope,
                context=request.context,
            )

        logger.info("Response submitted: %s -> %s (rule=%s, scope=%s)", request_id, status, apply_rule, rule_scope)
        return response

    def expire_overdue(self, *, now: datetime | None = None) -> list[ApprovalResponse]:
        """Resolve every pending request past its timeout onto the fallback.

        Replaces the reference's per-request ``asyncio.wait_for`` —
        invoked by the Batch 5 beat job (or tests) with the current time.
        """
        moment = now or datetime.now(UTC)
        expired: list[ApprovalResponse] = []
        for request in list(self._pending.values()):
            if request.status != ApprovalStatus.PENDING:
                continue
            created = datetime.fromisoformat(request.created_at)
            if (moment - created).total_seconds() < request.timeout_seconds:
                continue
            expired.append(self._handle_timeout(request))
        return expired

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Retrieve a pending or completed approval request."""
        return self._pending.get(request_id)

    # ── internals ──────────────────────────────────────────────── #

    def _notify_owner(self, request: ApprovalRequest) -> None:
        urgency_labels = {1: "Low", 2: "Medium", 3: "Normal", 4: "High", 5: "URGENT"}
        urgency_label = urgency_labels.get(request.urgency, "Normal")
        message = (
            f"[{urgency_label}] Approval Required\n\n"
            f"Action: {request.action_type}\n"
            f"Property: {request.property_id}\n"
            f"Description: {request.description}\n"
        )
        if request.evidence_summary:
            message += f"\n--- AI Evidence ---\n{request.evidence_summary}\n"
        message += (
            f"\nReply with:\n"
            f"  /approve {request.request_id}\n"
            f"  /deny {request.request_id}\n\n"
            f"Timeout: {request.timeout_seconds // 60} min"
        )
        if self._notifier:
            try:
                self._notifier.send_approval_request(
                    owner_id=request.owner_id,
                    message=message,
                    request_id=request.request_id,
                )
            except Exception:
                logger.exception("Failed to send approval notification for %s", request.request_id)

    def _auto_approve(self, request: ApprovalRequest, rule_id: str = "") -> ApprovalResponse:
        request.status = ApprovalStatus.AUTO_APPROVED
        request.responded_at = datetime.now(UTC).isoformat()
        return ApprovalResponse(
            request_id=request.request_id,
            status=ApprovalStatus.AUTO_APPROVED,
            owner_id=request.owner_id,
            message=f"Auto-approved (rule: {rule_id})" if rule_id else "Auto-approved",
        )

    def _handle_timeout(self, request: ApprovalRequest) -> ApprovalResponse:
        request.status = ApprovalStatus.TIMEOUT
        logger.warning("Executing fallback '%s' for timed-out request %s", request.fallback_action, request.request_id)
        return ApprovalResponse(
            request_id=request.request_id,
            status=ApprovalStatus.TIMEOUT,
            owner_id=request.owner_id,
            message=f"Timeout — fallback: {request.fallback_action}",
        )
