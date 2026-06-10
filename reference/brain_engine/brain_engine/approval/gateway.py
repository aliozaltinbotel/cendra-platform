"""ApprovalGateway — центральный хаб маршрутизации AI-решений через одобрение.

Перехватывает предлагаемые действия, проверяет необходимость одобрения
(по типу действия, preference-правилам и confidence-маршрутизации),
отправляет уведомления и обрабатывает таймауты.

Phase 2: интеграция ConfidenceRouter для трёхуровневой маршрутизации
(HIGH=auto, MEDIUM=evidence, LOW=escalate).

Использование:
    gateway = ApprovalGateway(notifier=notifier, preference_store=store)
    result = await gateway.request_approval(
        action_type=ActionType.LATE_CHECKOUT,
        owner_id="owner_123",
        property_id="PROP001",
        description="Guest John requests late checkout at 3 PM ($50 fee)",
        proposed_action={"checkout_time": "15:00", "fee": 50},
        context={"guest_name": "John", "guest_rating": 4.8},
        confidence=0.72,
        reasoning="Guest is VIP with 5 past stays",
    )
    if result.status == ApprovalStatus.APPROVED:
        # Выполнить действие
        ...
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from brain_engine.approval.confidence_router import ConfidenceRouter, RoutingDecision
from brain_engine.approval.models import (
    ALWAYS_REQUIRE_APPROVAL,
    AUTO_APPROVE_ACTIONS,
    CONDITIONAL_APPROVE_ACTIONS,
    URGENCY_TIMEOUTS,
    ActionType,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalStatus,
)
from brain_engine.exceptions import ApprovalNotFoundError, ApprovalTimeoutError
from brain_engine.protocols import Notifier

if TYPE_CHECKING:
    from brain_engine.blockers.engine import BlockerEngine

logger = logging.getLogger(__name__)


class ApprovalGateway:
    """Маршрутизатор AI-решений через workflow одобрения владельцем.

    Поддерживает реестр ожидающих запросов и интегрируется с
    PreferenceStore для проверки существующих правил auto-approve.

    Phase 2: ConfidenceRouter для трёхуровневой маршрутизации
    (HIGH=auto, MEDIUM=evidence, LOW=escalate).

    Args:
        notifier: Бэкенд уведомлений (Telegram/WhatsApp).
        preference_store: Хранилище preference-правил владельца.
        confidence_router: Маршрутизатор на основе уверенности (DIP).
    """

    def __init__(
        self,
        notifier: Notifier | None = None,
        preference_store: Any | None = None,
        confidence_router: ConfidenceRouter | None = None,
        blocker_engine: BlockerEngine | None = None,
    ) -> None:
        self._notifier = notifier
        self._preference_store = preference_store
        self._confidence_router = confidence_router or ConfidenceRouter()
        self._blocker_engine = blocker_engine
        self._pending: dict[str, ApprovalRequest] = {}
        self._responses: dict[str, asyncio.Event] = {}
        self._results: dict[str, ApprovalResponse] = {}

    @property
    def pending_requests(self) -> list[ApprovalRequest]:
        """All currently pending approval requests."""
        return [
            req for req in self._pending.values()
            if req.status == ApprovalStatus.PENDING
        ]

    async def request_approval(
        self,
        action_type: ActionType,
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
        """Запросить одобрение владельца на предлагаемое действие.

        Порядок проверок:
        1. AUTO_APPROVE_ACTIONS — мгновенное одобрение по типу действия
        2. Preference rules — существующие правила владельца
        3. ConfidenceRouter — маршрутизация по уверенности (HIGH/MEDIUM/LOW)
        4. Notification + wait — отправка PM и ожидание ответа

        Args:
            action_type: Тип предлагаемого действия.
            owner_id: ID владельца.
            property_id: ID объекта.
            description: Описание действия для PM.
            proposed_action: Структурированные детали действия.
            context: Дополнительный контекст (инфо о госте и т.д.).
            urgency: Уровень срочности (1-5).
            confidence: Уверенность AI (0.0-1.0) для confidence routing.
            reasoning: Обоснование AI для evidence pack.
            kb_entries: Релевантные записи из KB.
            past_decisions: Прошлые решения по аналогичным вопросам.

        Returns:
            ApprovalResponse с решением (approved/denied/timeout/auto_approved).
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

        # ── Шаг 0: Blocker check — hard blockers override everything ── #
        if self._blocker_engine:
            blockers = await self._blocker_engine.check_blockers(
                property_id=property_id,
                reservation_id=(context or {}).get("reservation_id"),
                action_type=action_type,
            )
            hard_blockers = [b for b in blockers if b.is_hard]
            if hard_blockers:
                descriptions = "; ".join(b.description for b in hard_blockers)
                logger.warning(
                    "Action %s BLOCKED by %d hard blocker(s): %s (request=%s)",
                    action_type, len(hard_blockers), descriptions, request_id,
                )
                request.status = ApprovalStatus.DENIED
                return ApprovalResponse(
                    request_id=request_id,
                    status=ApprovalStatus.DENIED,
                    owner_id=owner_id,
                    message=f"Blocked: {descriptions}",
                )

            # Soft blockers: log warning but continue
            soft_blockers = [b for b in blockers if not b.is_hard]
            if soft_blockers:
                logger.info(
                    "Action %s has %d soft blocker(s), proceeding with caution (request=%s)",
                    action_type, len(soft_blockers), request_id,
                )

        # ── Шаг 0b: Conditional approve — auto-approve only if no blockers ── #
        if action_type in CONDITIONAL_APPROVE_ACTIONS:
            if self._blocker_engine:
                has_blocker = await self._blocker_engine.has_hard_blocker(
                    property_id=property_id,
                    reservation_id=(context or {}).get("reservation_id"),
                    action_type=action_type,
                )
                if not has_blocker:
                    logger.info(
                        "Conditional auto-approving %s — no blockers (request=%s)",
                        action_type, request_id,
                    )
                    return self._auto_approve(request)
                logger.info(
                    "Conditional action %s has blockers — routing to approval (request=%s)",
                    action_type, request_id,
                )
            else:
                # No blocker engine — fallback to auto-approve for backwards compat
                logger.info(
                    "Conditional auto-approving %s — no blocker engine (request=%s)",
                    action_type, request_id,
                )
                return self._auto_approve(request)

        # ── Шаг 1: AUTO_APPROVE по типу действия ────────────── #
        if action_type in AUTO_APPROVE_ACTIONS:
            logger.info(
                "Auto-approving %s (request=%s)",
                action_type, request_id,
            )
            return self._auto_approve(request)

        # ── Шаг 2: Preference rules ─────────────────────────── #
        if self._preference_store:
            rule = await self._preference_store.find_rule(
                owner_id=owner_id,
                property_id=property_id,
                action_type=action_type,
                context=context or {},
            )
            if rule and rule.get("auto_approve"):
                logger.info(
                    "Preference rule auto-approves %s (request=%s, rule=%s)",
                    action_type, request_id, rule.get("rule_id"),
                )
                return self._auto_approve(request, rule_id=rule.get("rule_id", ""))

        # ── Шаг 3: Confidence-Based Routing ──────────────────── #
        if confidence is not None:
            routing = self._confidence_router.route(
                confidence=confidence,
                action_type=action_type,
                reasoning=reasoning,
                kb_entries=kb_entries,
                past_decisions=past_decisions,
                metadata=context or {},
            )

            # Записать confidence-данные в request
            request.confidence_score = confidence
            request.confidence_tier = routing.tier.value
            if routing.evidence_pack:
                request.evidence_summary = routing.evidence_pack.summary

            if routing.auto_approve:
                logger.info(
                    "Confidence auto-approve %s (%.2f, request=%s)",
                    action_type, confidence, request_id,
                )
                return self._auto_approve(request)

            if routing.escalate:
                urgency = min(5, urgency + routing.urgency_boost)
                request.urgency = urgency
                timeout = URGENCY_TIMEOUTS.get(urgency, 600)
                request.timeout_seconds = timeout
                logger.warning(
                    "Confidence LOW (%.2f) → escalating %s, urgency=%d (request=%s)",
                    confidence, action_type, urgency, request_id,
                )

        # ── Шаг 4: Mandatory approval check ──────────────────── #
        if action_type in ALWAYS_REQUIRE_APPROVAL:
            logger.info(
                "Action %s requires mandatory approval (request=%s)",
                action_type, request_id,
            )

        # Store pending request
        self._pending[request_id] = request
        self._responses[request_id] = asyncio.Event()

        # Send notification to owner
        await self._notify_owner(request)

        # Wait for response or timeout
        try:
            await asyncio.wait_for(
                self._responses[request_id].wait(),
                timeout=timeout,
            )
            result = self._results.get(request_id)
            if result:
                request.status = result.status
                request.responded_at = datetime.now(timezone.utc).isoformat()
                logger.info(
                    "Owner responded to %s: %s (request=%s)",
                    action_type, result.status, request_id,
                )
                return result
        except asyncio.TimeoutError:
            logger.warning(
                "Approval timeout for %s after %ds (request=%s)",
                action_type, timeout, request_id,
            )
            return self._handle_timeout(request)
        finally:
            self._cleanup_request(request_id)

        return self._handle_timeout(request)

    async def submit_response(
        self,
        request_id: str,
        approved: bool,
        owner_id: str = "",
        message: str = "",
        apply_rule: bool = False,
        rule_scope: str = "this_time",
    ) -> ApprovalResponse:
        """Submit an owner's response to a pending approval request.

        Called when the owner responds via Telegram/WhatsApp/Dashboard.

        Args:
            request_id: The approval request being responded to.
            approved: Whether the owner approves.
            owner_id: Who is responding.
            message: Optional message from owner.
            apply_rule: Whether to save this as a preference rule.
            rule_scope: Scope for the rule (this_time, always, etc.).

        Returns:
            The constructed ApprovalResponse.

        Raises:
            ApprovalNotFoundError: If request_id is not found in pending requests.
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

        # Signal the waiting coroutine
        event = self._responses.get(request_id)
        if event:
            event.set()

        # Create preference rule if requested
        if apply_rule and self._preference_store:
            req = self._pending[request_id]
            await self._preference_store.save_rule(
                owner_id=req.owner_id,
                property_id=req.property_id,
                action_type=req.action_type,
                auto_approve=approved,
                scope=rule_scope,
                context=req.context,
            )

        logger.info(
            "Response submitted: %s -> %s (rule=%s, scope=%s)",
            request_id, status, apply_rule, rule_scope,
        )
        return response

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Retrieve a pending or completed approval request."""
        return self._pending.get(request_id)

    async def _notify_owner(self, request: ApprovalRequest) -> None:
        """Отправить уведомление о запросе одобрения владельцу."""
        urgency_labels = {1: "Low", 2: "Medium", 3: "Normal", 4: "High", 5: "URGENT"}
        urgency_label = urgency_labels.get(request.urgency, "Normal")

        message = (
            f"[{urgency_label}] Approval Required\n\n"
            f"Action: {request.action_type.value}\n"
            f"Property: {request.property_id}\n"
            f"Description: {request.description}\n"
        )

        # Добавить evidence summary если доступен (Phase 2)
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
                await self._notifier.send_approval_request(
                    owner_id=request.owner_id,
                    message=message,
                    request_id=request.request_id,
                )
            except Exception:
                logger.exception(
                    "Failed to send approval notification for %s",
                    request.request_id,
                )

    def _auto_approve(
        self,
        request: ApprovalRequest,
        rule_id: str = "",
    ) -> ApprovalResponse:
        """Create an auto-approval response."""
        request.status = ApprovalStatus.AUTO_APPROVED
        request.responded_at = datetime.now(timezone.utc).isoformat()
        return ApprovalResponse(
            request_id=request.request_id,
            status=ApprovalStatus.AUTO_APPROVED,
            owner_id=request.owner_id,
            message=f"Auto-approved (rule: {rule_id})" if rule_id else "Auto-approved",
        )

    def _handle_timeout(self, request: ApprovalRequest) -> ApprovalResponse:
        """Handle approval timeout — execute fallback action."""
        request.status = ApprovalStatus.TIMEOUT
        logger.warning(
            "Executing fallback '%s' for timed-out request %s",
            request.fallback_action, request.request_id,
        )
        return ApprovalResponse(
            request_id=request.request_id,
            status=ApprovalStatus.TIMEOUT,
            owner_id=request.owner_id,
            message=f"Timeout — fallback: {request.fallback_action}",
        )

    def _cleanup_request(self, request_id: str) -> None:
        """Remove request from pending tracking."""
        self._responses.pop(request_id, None)
        self._results.pop(request_id, None)
